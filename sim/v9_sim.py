"""v9 by simulation (2026-09-29): a PREDICTIVE peak guard, per route x width bin x shape class.

Why (box 2, PlaStim, dry pads, two trips the same day, TEST-LOG 2026-09-29):
  - Intense, taper 0.4, 130 us, route 24: cmd peak 0.460 A primary, sensed 0.582 (1.27x), limit 0.580.
  - a swinging pattern (width 195-253 us, rate 52-198 Hz, amps rising and falling 2.5x several times a second), soft
    square, route 12: cmd 0.282, sensed 0.411 (1.46x), limit 0.402.
  The stock rule trips at command + 0.12 A: the ratio it allows shrinks with the command (1.26 at 0.46 A), and on skin
  the settled sensed/command ratio is ~1.25-1.29 for every shape (v9_explore.py). v6/v7 correct the estimate AFTER a
  pulse, for the amplitude and width just played; a rising amplitude or a jump to another width bin gets there first.

v9, before every pulse (the command a1 and the e-stop limit a1 + MARGIN are unchanged; only the drive is trimmed, as
the v6/v7 guard trims it through the estimate):
  - G[key] = sensed peak (any channel) per volt of planned peak drive, learned per (width bin, shape class) of the
    route: up at once, down slowly (G_DECAY per pulse). key's shape class: rounded (incl. taper <= 4.1), triangle,
    steep (soft, square, taper > 4.1). A key never measured on this route starts from the route's largest G x
    G_SEED_MARGIN, else from G0 (a 1.5x sensed peak at the model's drive).
  - k = min(1, V9_HOLD x (a1 + MARGIN - GUARD_TRIP_FRAC x MARGIN) / (G x planned peak drive)): the drive is scaled by k.
  - The charge adaptation may lower the estimate but not raise it on a pulse where k < 1 (so it doesn't climb back
    to undo the trim and leave an inflated estimate behind).
  - The v6 after-pulse guard stays as a second line.

    py -3.13 sim/v9_sim.py
"""
from __future__ import annotations

import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import biphasic_rc_sim as base  # noqa: E402
import lc_filter_sim as lc  # noqa: E402
from biphasic_rc_sim import MARGIN, MIN_I, ROUNDED, SOFT, SQUARE, TRIANGLE, Load, Model, build, feedforward  # noqa: E402

GUARD_TRIP_FRAC = lc.GUARD_TRIP_FRAC       # 0.5: the guard's ceiling = a1 + MARGIN - 0.5 x MARGIN
V9_HOLD = 0.95                             # aim just under that ceiling
G_DECAY = 0.05                             # per pulse, toward a lower observation
G_SEED_MARGIN = 1.5                        # a key not yet measured on this route: the route's largest G x this
G0_RATIO = 1.5                             # a never-measured route: assume the sensed peak is 1.5x at the model's drive


def shape_class(shape: float) -> int:
    if shape == ROUNDED or (shape >= 4.0 and shape <= 4.1):
        return 0
    if shape == TRIANGLE:
        return 1
    return 2


class V9:
    def __init__(self) -> None:
        self.model = Model(rc=False)
        self.g: dict[tuple[int, int], float] = {}
        self.measured: set[tuple[int, int]] = set()

    def g_for(self, key, r, a1, vpk):
        if key in self.g:
            return self.g[key]
        if self.g:
            return max(self.g.values()) * G_SEED_MARGIN
        return G0_RATIO * a1 / vpk if vpk > 0 else 0.0

    def pulse(self, load: Load, *, shape=ROUNDED, width_us=150.0, asym=1.0, amp=0.15, after_guard=True):
        m = self.model
        w1 = min(max(width_us * 1e-6 * base.FS, 2.0), 20.0)
        i_cmd, ph, s1, w2 = build(shape, w1, asym, amp)
        b, f, r, sig = m.estimate(w1)
        s_el = sig * r / (w1 * base.DT)
        v = feedforward(i_cmd, r, s_el)
        vpk = max(abs(x) for x in v)
        gb = min(range(len(base.BIN_WIDTH)), key=lambda j: abs(math.log(w1 / base.BIN_WIDTH[j])))   # nearest bin
        key = (gb, shape_class(shape))
        g = self.g_for(key, r, amp, vpk)
        ceiling = amp + MARGIN - GUARD_TRIP_FRAC * MARGIN
        k = min(1.0, V9_HOLD * ceiling / (g * vpk)) if g * vpk > 0 else 1.0
        o = lc.play_lc(load, [x * k for x in v])
        sx = [min(a, 0.0) for a in o["lx"]]
        sy = [min(a, 0.0) for a in o["ly"]]
        trip_i = max(-min(a, b_) for a, b_ in zip(sx, sy))
        trip = trip_i > amp + MARGIN
        # learn the peak per volt (up at once, down slowly)
        if vpk > 0 and k > 0:
            obs = trip_i / (k * vpk)
            prev = self.g.get(key)
            first = key not in self.measured                # a seeded (cautious) key takes its first measurement
            self.g[key] = obs if first or prev is None or obs > prev else prev + G_DECAY * (obs - prev)
            self.measured.add(key)
        # charge adaptation on the lead phase (as v4+); not upward while trimmed
        q_cmd = sum(-c for c, p in zip(i_cmd, ph) if p == 1 and c < -MIN_I)
        q_meas = sum(-mm for c, mm, p in zip(i_cmd, sx, ph) if p == 1 and c < -MIN_I)
        rho = q_meas / q_cmd if q_cmd > 0 else 0.0
        if q_cmd > MIN_I * 2 and q_meas > 0 and not (k < 1.0 and rho < 1.0):
            m.update(b, f, rho, 0.0, None)
        if after_guard:                                      # the firmware's v6/v7 guard + hold, second line, from
            g_any = (amp + MARGIN - GUARD_TRIP_FRAC * MARGIN) / trip_i if trip_i > 0 else math.inf   # the drive
            lead_all = max(-mm for mm in sx)                                                          # used (r x k)
            g_lead = lc.GUARD_LEAD_RATIO * amp / lead_all if amp >= lc.GUARD_MIN_AMPS and lead_all > 0 else math.inf
            gg = min(g_any, g_lead)
            if gg < math.inf:
                cap = max(base.R_MIN, r * k * gg * lc.GUARD_HOLD)
                r_after = math.exp((1 - f) * math.log(m.r[b]) + f * math.log(m.r[b + 1]))
                if r_after > cap:
                    q = cap / r_after
                    m.r[b] = max(base.R_MIN, m.r[b] * q)
                    m.r[b + 1] = max(base.R_MIN, m.r[b + 1] * q)
        return {"trip": trip, "trip_i": trip_i, "limit": amp + MARGIN, "k": k, "rho": rho, "delivered": rho}   # rho includes k


class V8:
    def __init__(self) -> None:
        self.model = Model(rc=False)

    def pulse(self, load, **kw):
        res = lc.pulse(self.model, load, guard=True, hold=True, **kw)
        res["k"] = 1.0
        res["delivered"] = res["rho"]
        return res


def scenario(name, ctl, load, seq):
    trips = worst = 0.0
    dl = []
    ks = []
    for i, kw in enumerate(seq):
        r = ctl.pulse(load, **kw)
        if i >= 5:                                           # after a few pulses to learn
            trips += r["trip"]
            worst = max(worst, r["trip_i"] / r["limit"])
            dl.append(r["delivered"])
            ks.append(r["k"])
    return {"trips": int(trips), "worst": worst, "delivered": sum(dl) / len(dl), "k_min": min(ks)}


def seq_swing(n=600, seed=1):
    """The second trip: soft square, width jumping 195-253 us, amps swinging 0.13 -> 0.32 A primary ~ every 30 pulses."""
    rnd = random.Random(seed)
    out, w = [], 220.0
    for i in range(n):
        if i % 8 == 0:
            w = rnd.uniform(195, 253)
        amp = 0.225 + 0.095 * math.sin(2 * math.pi * i / 30)
        out.append({"shape": SOFT, "width_us": w, "amp": amp})
    return out


def seq_steady(shape, width, amp, n=300):
    return [{"shape": shape, "width_us": width, "amp": amp} for _ in range(n)]


def seq_step(shape, width, lo, hi, n=300):
    return [{"shape": shape, "width_us": width, "amp": lo if i < n // 2 else hi} for i in range(n)]


def seq_width_jumps(shape, amp, n=400):
    return [{"shape": shape, "width_us": 60.0 if (i // 20) % 2 else 250.0, "amp": amp} for i in range(n)]


def seq_shape_switch(amp, n=400):
    return [{"shape": ROUNDED if (i // 50) % 2 == 0 else SOFT, "width_us": 200.0, "amp": amp} for i in range(n)]


SCENARIOS = {
    "swing soft 195-253us 0.13-0.32A": seq_swing(),
    "taper0.4 130us 0.46A steady": seq_steady(4.4, 130.0, 0.46),
    "rounded 130us 0.46A steady": seq_steady(ROUNDED, 130.0, 0.46),
    "soft 200us step 0.10->0.45A": seq_step(SOFT, 200.0, 0.10, 0.45),
    "rounded 200us step 0.10->0.45A": seq_step(ROUNDED, 200.0, 0.10, 0.45),
    "soft width 60<->250us 0.30A": seq_width_jumps(SOFT, 0.30),
    "rounded<->soft 200us 0.35A": seq_shape_switch(0.35),
}
LOADS = {"1k R": Load(1000.0), "dry 700R+47n": Load(700.0, 47e-9), "dry 700R+100n": Load(700.0, 100e-9),
         "500R+100n": Load(500.0, 100e-9)}

if __name__ == "__main__":
    print("trips (after pulse 5) | worst sensed / limit | mean delivered charge / command | smallest trim k")
    for sname, seq in SCENARIOS.items():
        print(sname)
        for lname, load in LOADS.items():
            a = scenario(sname, V8(), load, seq)
            b = scenario(sname, V9(), load, seq)
            print(f"  {lname:14s} v8 trips {a['trips']:3d} worst {a['worst']:.2f} deliv {a['delivered']:.2f} | "
                  f"v9 trips {b['trips']:3d} worst {b['worst']:.2f} deliv {b['delivered']:.2f} k_min {b['k_min']:.2f}")
