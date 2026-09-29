"""Pulse-by-pulse simulation of the fork's OUTPUT_BIPHASIC_PAIRS pulse builder + impedance adaptation on a
series-RC load, through a model of the V4 output transformers. Used to design and check firmware v5 (series-RC
feedforward) against v4 (resistive feedforward, lead-phase adaptation).

    py -3.13 firmware/sim/biphasic_rc_sim.py            # prints the load table used in NOTES.md (v5 section)

Circuit (all primary-referred, one pair x-y, the other two outputs disabled):

    driver v_x --R_FIX-- u_x --+-- magnetizing (L_M || R_CORE) to neutral
                                +-- ideal 1:N transformer -- secondary x --+
                                                                          body: R_s + C_s (series)
    driver v_y --R_FIX-- u_y --+-- magnetizing (L_M || R_CORE)            |
                                +-- ideal 1:N transformer -- secondary y --+
    loop (primary-referred):  u_x - u_y = R_L * i_l + v_C,   R_L = (R_s + 2 R_HV) / N^2,  C' = C_s * N^2
    primary currents:         i_x = i_mx + u_x / R_CORE + i_l,   i_y = i_my + u_y / R_CORE - i_l

Transformer numbers are the firmware's own (config_g473re_focstim_v4.h / transformers.h, XICON_42TL004): N = 5.57,
R_HV = 12 ohm per secondary, R_FIX = MODEL_FIXED_RESISTANCE = 1.7 ohm. The magnetizing branch is fitted to the
open-circuit data in transformers.cpp (1 kHz: 30.6 ohm at 43.2 deg; 500 Hz: 18.9 ohm at 47.9 deg) as a parallel
L_M = 7 mH, R_CORE = 40 ohm. The output LC filter (220 uH / 2.2 uF, ~7 kHz) is not modelled.

The sense is one-way (an electrode reads only negative current); the firmware integrates each phase on the
electrode that is cathodic in that phase. Each pulse starts from rest (states zeroed): between pulses the body
capacitor and magnetizing current relax through the secondary loop in ~0.1-0.2 ms, much shorter than the gaps at
<= 400 Hz. The e-stop is the stock rule: any sensed |current| > commanded peak + 0.12 A (primary).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

FS = 50_000.0
DT = 1.0 / FS
SUB = 20                       # integration substeps per PWM sample
N = 5.57                       # 42TL004 winding ratio
R_HV = 12.0                    # secondary winding resistance
R_FIX = 1.7                    # MODEL_FIXED_RESISTANCE
L_M = 7e-3                     # magnetizing inductance, primary
R_CORE = 40.0                  # core loss, primary, parallel
MARGIN = 0.12                  # ESTOP_CURRENT_LIMIT_MARGIN (primary A)
R_MIN, R_MAX = 2 * 1.4, 2 * 80.0
MIN_I = 0.02                   # MIN_CURRENT_FOR_UPDATE
BIN_WIDTH = [2.0, 3.0, 4.5, 6.5, 9.5, 14.0, 20.0]
ROUNDED, SQUARE, SOFT, TRIANGLE = 0, 1, 2, 3
# v7 SHAPE_TAPER: a shape code 4.0..5.0 is the taper with flat-top fraction code - 4 (the firmware's shape axis value)


def shape_integral(shape: float, t: float, w: float) -> float:
    t = min(max(t, 0.0), w)
    if shape >= 3.5:                             # v7 taper: quarter-sine edges of (1 - flat) / 2 of w each
        r = (1 - min(max(shape - 4.0, 0.0), 1.0)) * w * 0.5
        if r < 1e-4:
            return t
        k = 2 * r / math.pi
        if t < r:
            return k * (1 - math.cos(math.pi * 0.5 * t / r))
        if t <= w - r:
            return k + (t - r)
        u = w - t
        return (2 * k + (w - 2 * r)) - k * (1 - math.cos(math.pi * 0.5 * u / r))
    if shape == TRIANGLE:
        h = w * 0.5
        if t <= h:
            return t * t / w
        u = w - t
        return h - u * u / w
    if shape == SQUARE:
        return t
    if shape == SOFT:
        r = min(1.0, w * 0.5)
        if t < r:
            return t * t / (2 * r)
        if t <= w - r:
            return r * 0.5 + (t - r)
        u = w - t
        return (w - r) - u * u / (2 * r)
    return w / math.pi * (1 - math.cos(math.pi * t / w))


@dataclass
class Load:
    r_s: float                      # body series resistance (secondary side, ohm)
    c_s: float | None = None        # body series capacitance (F); None = pure resistor

    @property
    def r_l(self) -> float:
        return (self.r_s + 2 * R_HV) / N ** 2

    @property
    def c_p(self) -> float | None:
        return None if self.c_s is None else self.c_s * N ** 2


def build(shape: int, w1: float, asym: float, a1: float):
    """The firmware's sample lists: i_cmd for electrode x per sample and the phase of each sample (1, 2, 0)."""
    w2 = min(max(w1 * asym, w1), 80.0)
    n1, n2 = math.ceil(w1), math.ceil(w2)
    s1, s2 = shape_integral(shape, w1, w1), shape_integral(shape, w2, w2)
    a2 = a1 * s1 / s2
    sx = -1.0                                   # electrode x cathodic in the lead phase
    i, ph = [], []
    f_prev = 0.0
    for j in range(n1):
        f = shape_integral(shape, j + 1.0, w1)
        i.append(sx * a1 * (f - f_prev)); ph.append(1); f_prev = f
    f_prev = 0.0
    for j in range(n2):
        f = shape_integral(shape, j + 1.0, w2)
        i.append(-sx * a2 * (f - f_prev)); ph.append(2); f_prev = f
    for _ in range(3):
        i.append(0.0); ph.append(0)
    return i, ph, s1, w2


def play(load: Load, v_x: list[float]):
    """Drive v_x on electrode x and -v_x on y; return sample-averaged primary currents (i_x, i_y)."""
    i_mx = i_my = v_c = 0.0
    h = DT / SUB
    out_x, out_y = [], []
    for vx in v_x:
        vy = -vx
        ax = ay = 0.0
        for _ in range(SUB):
            # u = v - R_FIX * (i_m + u/R_CORE + (+/-) i_l): solve the loop for i_l with u eliminated
            # u_x = (v_x - R_FIX*(i_mx + i_l)) / (1 + R_FIX/R_CORE); u_y = (v_y - R_FIX*(i_my - i_l)) / (1 + g)
            g = 1 + R_FIX / R_CORE
            num = (vx - R_FIX * i_mx) / g - (vy - R_FIX * i_my) / g - v_c
            den = load.r_l + 2 * R_FIX / g
            i_l = num / den
            u_x = (vx - R_FIX * (i_mx + i_l)) / g
            u_y = (vy - R_FIX * (i_my - i_l)) / g
            ix = i_mx + u_x / R_CORE + i_l
            iy = i_my + u_y / R_CORE - i_l
            ax += ix; ay += iy
            i_mx += u_x / L_M * h
            i_my += u_y / L_M * h
            if load.c_p is not None:
                v_c += i_l / load.c_p * h
        out_x.append(ax / SUB); out_y.append(ay / SUB)
    return out_x, out_y


@dataclass
class Model:
    """Firmware model state for one pair: R and sigma per width bin (v5), R only when rc=False (v4)."""
    rc: bool
    k: float = 0.5                # SIGMA_GAIN (firmware v5)
    sigma_max: float = 1.5        # SIGMA_MAX
    # "ls" (firmware v5): sigma from a per-sample least-squares fit of the current error on q, R from the lead charge;
    # "ls2": R and sigma from one joint fit (tried, same result); "charge": sigma from rho2/rho1 (tried, fails on
    # asymmetric pulses: a capacitor front-loads the return current without adding much net charge)
    fit: str = "ls"
    r: list[float] = field(default_factory=lambda: [R_MIN] * len(BIN_WIDTH))
    sig: list[float] = field(default_factory=lambda: [0.0] * len(BIN_WIDTH))
    seen: list[bool] = field(default_factory=lambda: [False] * len(BIN_WIDTH))

    def locate(self, w: float):
        w = min(max(w, BIN_WIDTH[0]), BIN_WIDTH[-1])
        b = 0
        while b < len(BIN_WIDTH) - 2 and w > BIN_WIDTH[b + 1]:
            b += 1
        f = math.log(w / BIN_WIDTH[b]) / math.log(BIN_WIDTH[b + 1] / BIN_WIDTH[b])
        f = min(max(f, 0.0), 1.0)
        for j in (b, b + 1):
            if not self.seen[j]:
                init_r, init_s = R_MIN, 0.0
                for kk in range(j - 1, -1, -1):
                    if self.seen[kk]:
                        init_r = self.r[kk]
                        break
                # sigma from the nearest measured bin either side, never more than it
                cands = [self.sig[kk] for kk in range(len(BIN_WIDTH)) if self.seen[kk]]
                if cands:
                    near = min((kk for kk in range(len(BIN_WIDTH)) if self.seen[kk]), key=lambda kk: abs(kk - j))
                    init_s = self.sig[near]
                self.r[j], self.sig[j], self.seen[j] = init_r, init_s, True
        return b, f

    def estimate(self, w: float):
        b, f = self.locate(w)
        r = math.exp((1 - f) * math.log(self.r[b]) + f * math.log(self.r[b + 1]))
        s = (1 - f) * self.sig[b] + f * self.sig[b + 1] if self.rc else 0.0
        return b, f, r, s

    def update(self, b: int, f: float, rho1: float, rho2: float, dsig: float | None = None) -> None:
        rho1c = min(max(rho1, 0.5), 2.0)
        corr = rho1c ** -0.5
        self.r[b] = min(max(self.r[b] * corr ** (1 - f), R_MIN), R_MAX)
        self.r[b + 1] = min(max(self.r[b + 1] * corr ** f, R_MIN), R_MAX)
        if not self.rc:
            return
        if self.fit in ("ls", "ls2"):
            if dsig is None:
                return
            step = min(max(self.k * dsig, -0.25), 0.25)
        else:
            if not (rho1 > 0 and rho2 > 0):
                return
            step = self.k * (min(max(rho2 / rho1, 0.5), 2.0) - 1.0)
        self.sig[b] = min(max(self.sig[b] + step * (1 - f), 0.0), self.sigma_max)
        self.sig[b + 1] = min(max(self.sig[b + 1] + step * f, 0.0), self.sigma_max)


def feedforward(i_cmd: list[float], r: float, s_el: float) -> list[float]:
    """v_x per sample: (R i + S q)/2, q = running integral of i_cmd (at the sample midpoint)."""
    v, q = [], 0.0
    for i in i_cmd:
        qm = q + 0.5 * i * DT
        v.append(0.5 * (r * i + s_el * qm))
        q += i * DT
    return v


def pulse(model: Model, load: Load, *, shape=ROUNDED, width_us=150.0, asym=1.0, amp=0.15):
    """One pulse: build, feedforward, play, measure, adapt. Returns a dict of results."""
    w1 = min(max(width_us * 1e-6 * FS, 2.0), 20.0)
    i_cmd, ph, s1, w2 = build(shape, w1, asym, amp)
    b, f, r, sig = model.estimate(w1)
    t1 = w1 * DT
    s_el = sig * r / t1                          # elastance, primary ohm/s
    v = feedforward(i_cmd, r, s_el)
    ix, iy = play(load, v)
    # one-way sense and the firmware's per-phase integration on the cathodic electrode
    sx = [min(a, 0.0) for a in ix]
    sy = [min(a, 0.0) for a in iy]
    q = {1: [0.0, 0.0], 2: [0.0, 0.0]}           # phase -> [cmd, meas]
    peak = {1: 0.0, 2: 0.0}
    for k, p in enumerate(ph):
        if p == 0:
            continue
        cx, cy = i_cmd[k], -i_cmd[k]
        for c, m in ((cx, sx[k]), (cy, sy[k])):
            if c < -MIN_I:
                q[p][0] += -c
                q[p][1] += -m
        peak[p] = max(peak[p], -min(sx[k], sy[k]))
    trip = max(-min(a, b_) for a, b_ in zip(sx, sy)) > amp + MARGIN
    # least-squares fit of the loop-current error against the running commanded charge q (both phases):
    #   l_meas - c  ~=  beta * qn,   qn = q / t1 (amp units)   ->   sigma error = -beta / amplitude-normalised
    # with feedforward v = R c + S q and a load R_t + C: l = (R/R_t) c + ((S - 1/C)/R_t) q, so beta ~ (S - 1/C) t1 / R
    # = sigma - sigma_true when R ~ R_t: the sigma correction is -beta.
    num = den = 0.0
    qrun = 0.0
    for kk, p in enumerate(ph):
        c = i_cmd[kk]
        qm = qrun + 0.5 * c * DT
        qrun += c * DT
        if p == 0 or abs(c) <= MIN_I:
            continue
        l_meas = sx[kk] if c < 0 else -sy[kk]
        qn = qm / t1
        num += (l_meas - c) * qn
        den += qn * qn
    dsig = (-num / den) if den > 0 else None
    # joint fit (fit == "ls2"):  l_meas ~= alpha * c + beta * qn  ->  R_true = R / alpha, sigma_true = sigma - beta / alpha
    scc = scq = sqq = slc = slq = 0.0
    qrun = 0.0
    for kk, p in enumerate(ph):
        c = i_cmd[kk]
        qm = qrun + 0.5 * c * DT
        qrun += c * DT
        if p == 0 or abs(c) <= MIN_I:
            continue
        l_meas = sx[kk] if c < 0 else -sy[kk]
        qn = qm / t1
        scc += c * c; scq += c * qn; sqq += qn * qn; slc += l_meas * c; slq += l_meas * qn
    det = scc * sqq - scq * scq
    alpha = beta = None
    if det > 1e-12 * max(scc * sqq, 1e-30):
        alpha = (slc * sqq - slq * scq) / det
        beta = (scc * slq - scq * slc) / det
    if model.fit == "ls2" and alpha is not None and alpha > 0:
        rho1 = alpha                               # R from the joint fit
        dsig = -beta / alpha
    rho1 = q[1][1] / q[1][0] if q[1][0] else 0.0
    rho2 = q[2][1] / q[2][0] if q[2][0] else 0.0
    cmd_peak2 = amp * s1 / shape_integral(shape, w2, w2)
    if q[1][0] > MIN_I * 2 and q[1][1] > 0:
        model.update(b, f, rho1, rho2, dsig)
    return {"r": r, "sigma": sig, "rho1": rho1, "rho2": rho2, "pk1": peak[1] / amp, "pk2": peak[2] / cmd_peak2,
            "trip": trip, "max_i": max(peak.values()), "limit": amp + MARGIN,
            "vmax": max(abs(x) for x in v) * 2, "true_q_ratio": None}


def run(load: Load, rc: bool, pulses: int = 200, **kw):
    m = Model(rc=rc, **{k: kw.pop(k) for k in ("k", "sigma_max", "fit") if k in kw})
    res = [pulse(m, load, **kw) for _ in range(pulses)]
    return m, res


def settled(res, n=40):
    tail = res[-n:]
    avg = lambda key: sum(r[key] for r in tail) / len(tail)
    return {k: avg(k) for k in ("pk1", "pk2", "rho1", "rho2", "r", "sigma")} | {
        "trips": sum(r["trip"] for r in res), "trips_after_20": sum(r["trip"] for r in res[20:])}


def seq_run(load: Load, rc: bool, schedule, **mk):
    """Run a schedule of (count, kwargs) blocks on one model; returns per-pulse results."""
    m = Model(rc=rc, **mk)
    out = []
    for count, kw in schedule:
        for _ in range(count):
            out.append(pulse(m, load, **kw) | {"kw": kw})
    return m, out


def table(k=0.5, sigma_max=1.5, fit="ls"):
    loads = {"a) 1 kOhm": Load(1000.0), "b) 500R+100n": Load(500.0, 100e-9),
             "b) 500R+220n": Load(500.0, 220e-9), "b) 500R+470n": Load(500.0, 470e-9)}
    rows = []
    for name, load in loads.items():
        for shape, sname in ((ROUNDED, "rounded"), (SOFT, "soft")):
            for w in (130.0, 250.0):
                for asym in (1.0, 3.0):
                    r4 = settled(run(load, False, shape=shape, width_us=w, asym=asym, amp=0.156)[1])
                    r5 = settled(run(load, True, shape=shape, width_us=w, asym=asym, amp=0.156, k=k,
                                     sigma_max=sigma_max, fit=fit)[1])
                    rows.append((name, sname, w, asym, r4, r5))
    return rows


def stroke_and_jumps(load: Load, rc: bool, k=0.5, sigma_max=1.5, amp=0.156, fit="ls"):
    """(c): width jumps 50 <-> 250 us every 25 pulses, and asym 1 <-> 3 alternation (Stroke) every 90 pulses."""
    sched = []
    for rep in range(6):
        sched.append((25, dict(shape=ROUNDED, width_us=50.0, asym=1.0, amp=amp)))
        sched.append((25, dict(shape=ROUNDED, width_us=250.0, asym=1.0, amp=amp)))
    for rep in range(4):
        sched.append((90, dict(shape=ROUNDED, width_us=255.0, asym=1.0, amp=amp)))
        sched.append((90, dict(shape=ROUNDED, width_us=255.0, asym=3.0, amp=amp)))
    for rep in range(4):
        sched.append((30, dict(shape=SOFT, width_us=130.0, asym=1.0, amp=amp)))
        sched.append((30, dict(shape=SOFT, width_us=60.0, asym=1.0, amp=amp)))
    m, out = seq_run(load, rc, sched, k=k, sigma_max=sigma_max, fit=fit)
    worst = max(r["max_i"] / r["limit"] for r in out[20:])
    return {"trips": sum(r["trip"] for r in out), "trips_after_20": sum(r["trip"] for r in out[20:]),
            "worst_peak_over_limit": worst,
            "worst_pk": max(max(r["pk1"], r["pk2"]) for r in out[20:])}


if __name__ == "__main__":
    import sys
    k = float(sys.argv[1]) if len(sys.argv) > 1 else 0.5
    smax = float(sys.argv[2]) if len(sys.argv) > 2 else 1.5
    fit = sys.argv[3] if len(sys.argv) > 3 else "ls"
    print(f"k={k} sigma_max={smax} fit={fit}   peak/command per phase after settling (v4 -> v5), sigma, trips")
    for name, sname, w, asym, r4, r5 in table(k, smax, fit):
        print(f"{name:13s} {sname:7s} {w:5.0f}us asym{asym:.0f} | lead {r4['pk1']:.2f}->{r5['pk1']:.2f} "
              f"return {r4['pk2']:.2f}->{r5['pk2']:.2f} | sigma {r5['sigma']:.2f} | R {r4['r']:.1f}->{r5['r']:.1f}"
              f" | trips v4 {r4['trips']} v5 {r5['trips']}")
    for name, load in (("a) 1 kOhm", Load(1000.0)), ("b) 500R+220n", Load(500.0, 220e-9)),
                       ("b) 500R+470n", Load(500.0, 470e-9))):
        c4 = stroke_and_jumps(load, False, k, smax)
        c5 = stroke_and_jumps(load, True, k, smax, fit=fit)
        print(f"(c) {name:13s} v4 trips {c4['trips']} (after 20: {c4['trips_after_20']}), worst peak/limit "
              f"{c4['worst_peak_over_limit']:.2f} | v5 trips {c5['trips']} (after 20: {c5['trips_after_20']}), "
              f"worst peak/limit {c5['worst_peak_over_limit']:.2f}")
