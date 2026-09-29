"""biphasic_rc_sim.py plus the V4 output LC filter, with the current sense where the hardware has it.

Each drive channel is DRV8231A -> L_F (220 uH) -> node with C_F (2.2 uF) to ground -> R_FIX -> transformer primary
(NOTES.md section 2). The DRV8231A current mirror reports the BRIDGE current, i.e. the filter inductor current: load +
magnetizing + the filter capacitor's C dv/dt. biphasic_rc_sim.py drove the transformer directly and sensed the primary
current, so it could not show what the filter does to the one-way, per-sample sense at narrow widths.

    py -3.13 firmware/sim/lc_filter_sim.py

Used to diagnose the v5 leg trip (TEST-LOG 2026-09-26: lead phase 1.9x at 87 us rounded, sigma 0, the 60-90 us
estimate about twice the wider ones) and to choose the v6 measurement window.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import biphasic_rc_sim as base  # noqa: E402
from biphasic_rc_sim import (DT, FS, L_M, MARGIN, MIN_I, R_CORE, R_FIX, ROUNDED, SOFT,  # noqa: E402
                             Load, Model, build, feedforward, shape_integral)

L_F = 220e-6
C_F = 2.2e-6
R_LF = 0.3                     # inductor + bridge series resistance (part of R_FIX in the firmware's model)
SUB = 50


def play_lc(load: Load, v_x: list[float], tail: int = 0):
    """Drive v_x / -v_x through the LC filters. Returns per-sample averages of the SENSED bridge currents
    (inductor currents) i_Lx, i_Ly, and of the transformer-side primary currents (for reference)."""
    ilx = ily = vcx = vcy = 0.0
    i_mx = i_my = v_c = 0.0
    h = DT / SUB
    g = 1 + (R_FIX - R_LF) / R_CORE
    rf = R_FIX - R_LF
    out = {"lx": [], "ly": [], "px": [], "py": []}
    for vx in list(v_x) + [0.0] * tail:
        vy = -vx
        a = {"lx": 0.0, "ly": 0.0, "px": 0.0, "py": 0.0}
        for _ in range(SUB):
            # transformer side, driven by the filter capacitor voltages (same algebra as base.play)
            num = (vcx - rf * i_mx) / g - (vcy - rf * i_my) / g - v_c
            den = load.r_l + 2 * rf / g
            i_l = num / den
            u_x = (vcx - rf * (i_mx + i_l)) / g
            u_y = (vcy - rf * (i_my - i_l)) / g
            ipx = i_mx + u_x / R_CORE + i_l
            ipy = i_my + u_y / R_CORE - i_l
            # filter (semi-implicit Euler)
            ilx += (vx - R_LF * ilx - vcx) / L_F * h
            ily += (vy - R_LF * ily - vcy) / L_F * h
            vcx += (ilx - ipx) / C_F * h
            vcy += (ily - ipy) / C_F * h
            i_mx += u_x / L_M * h
            i_my += u_y / L_M * h
            if load.c_p is not None:
                v_c += i_l / load.c_p * h
            a["lx"] += ilx; a["ly"] += ily; a["px"] += ipx; a["py"] += ipy
        for k in a:
            out[k].append(a[k] / SUB)
    return out


GUARD_TRIP_FRAC = 0.5          # firmware v6 BiphasicPairs::GUARD_TRIP_FRAC
GUARD_LEAD_RATIO = 1.5         # GUARD_LEAD_RATIO
GUARD_MIN_AMPS = 0.03          # GUARD_MIN_AMPS
GUARD_HOLD = 0.95              # firmware v7 BiphasicPairs::GUARD_HOLD


def pulse(model: Model, load: Load, *, shape=ROUNDED, width_us=150.0, asym=1.0, amp=0.15, window="v5", guard=False,
          hold=False):
    """One pulse through the filter. window: "v5" = the firmware's lead integration (samples where the lead
    electrode is commanded < -MIN_I); "whole" = the lead electrode's sensed current over the whole pulse (tried for
    v6: always softer, but it did not reproduce or explain the leg trip). guard: the firmware v6 peak guard.
    hold (v7, needs guard): the climb stops at GUARD_HOLD of the guard's ceiling instead of overshooting it and
    being knocked back (PlaStim, EMS 1: the v6 guard fired on ~1 pulse in 3, 2026-09-28)."""
    w1 = min(max(width_us * 1e-6 * FS, 2.0), 20.0)
    i_cmd, ph, s1, w2 = build(shape, w1, asym, amp)
    b, f, r, sig = model.estimate(w1)
    s_el = sig * r / (w1 * DT)
    v = feedforward(i_cmd, r, s_el)
    o = play_lc(load, v)
    sx = [min(a, 0.0) for a in o["lx"]]          # one-way sense of the bridge current
    sy = [min(a, 0.0) for a in o["ly"]]
    n1 = math.ceil(w1)
    q_cmd = sum(-c for c, p in zip(i_cmd, ph) if p == 1 and c < -MIN_I)
    if window != "whole":
        q_meas = sum(-m for c, m, p in zip(i_cmd, sx, ph) if p == 1 and c < -MIN_I)
    else:
        q_cmd = sum(-c for c, p in zip(i_cmd, ph) if p == 1)
        q_meas = sum(-m for m in sx)             # all samples: lead electrode, lead direction only
    trip_i = max(-min(a, b_) for a, b_ in zip(sx, sy))
    trip = trip_i > amp + MARGIN
    lead_pk = max(-m for m in sx[:n1 + 2])
    # true delivered lead charge (loop current into the body, lead direction), for reference
    rho = q_meas / q_cmd if q_cmd > 0 else 0.0
    if q_cmd > MIN_I * 2 and q_meas > 0:
        model.update(b, f, rho, 0.0, None)
    guarded = held = False
    if guard:
        # firmware v6: cap the estimate so this pulse's sensed currents would sit under both ceilings (lower only).
        # g = how far the estimate could scale before the first ceiling (currents are linear in it); < 1 = over.
        g = math.inf
        ceiling = amp + MARGIN - GUARD_TRIP_FRAC * MARGIN
        if trip_i > 0:
            g = min(g, ceiling / trip_i)
        lead_all = max(-m for m in sx)
        if amp >= GUARD_MIN_AMPS and lead_all > 0:
            g = min(g, GUARD_LEAD_RATIO * amp / lead_all)
        if g < 1.0:
            guarded = True
        if hold and g < math.inf:                # v7: hold at GUARD_HOLD of the ceiling, whenever above it; both
            cap = max(base.R_MIN, r * g * GUARD_HOLD)            # bins scaled alike (this width's estimate = cap)
            r_after = math.exp((1 - f) * math.log(model.r[b]) + f * math.log(model.r[b + 1]))
            if r_after > cap:
                held = not guarded
                k = cap / r_after
                model.r[b] = max(base.R_MIN, model.r[b] * k)
                model.r[b + 1] = max(base.R_MIN, model.r[b + 1] * k)
        elif g < 1.0:                            # v6: cap at the ceiling after an overshoot
            cap = max(base.R_MIN, r * g)
            model.r[b] = min(model.r[b], cap)
            model.r[b + 1] = min(model.r[b + 1], cap)
    return {"r": r, "rho": rho, "lead_pk": lead_pk / amp, "trip_i": trip_i, "limit": amp + MARGIN, "trip": trip,
            "guarded": guarded, "held": held}


def run(load, pulses=150, window="v5", guard=False, preset_r=None, hold=False, **kw):
    m = Model(rc=False)
    if preset_r is not None:                     # start from a run-away estimate (the v5 leg trip state)
        m.r = [preset_r] * len(m.r)
        m.seen = [True] * len(m.seen)
    res = [pulse(m, load, window=window, guard=guard, hold=hold, **kw) for _ in range(pulses)]
    tail = res[-30:]
    rs = [x["r"] for x in tail]
    return {"r": sum(rs) / len(tail), "r_spread": (max(rs) - min(rs)) / (sum(rs) / len(rs)),
            "guard_rate": sum(x["guarded"] for x in tail) / len(tail),
            "held_rate": sum(x["held"] for x in tail) / len(tail),
            "rho": sum(x["rho"] for x in tail) / len(tail),
            "lead_pk": max(x["lead_pk"] for x in tail), "trip_i": max(x["trip_i"] for x in tail),
            "limit": res[-1]["limit"], "trips": sum(x["trip"] for x in res[20:]),
            "trips_after_1": sum(x["trip"] for x in res[1:]), "worst_after_1": max(x["trip_i"] for x in res[1:])}


LOADS = {"1k": Load(1000.0), "skin 450R+160n": Load(450.0, 160e-9), "skin 450R+100n": Load(450.0, 100e-9),
         "skin 300R+220n": Load(300.0, 220e-9)}

if __name__ == "__main__":
    amp = 0.142
    print(f"amp {amp} A primary (e-stop limit {amp + MARGIN:.3f}); v5 -> v6 (peak guard)")
    print("A) from the leg-trip state (every bin at 32 ohm), rounded 87 us: trips after the first pulse, worst sensed")
    for name, load in LOADS.items():
        a5 = run(load, 60, preset_r=32.0, shape=ROUNDED, width_us=87.0, amp=amp)
        a6 = run(load, 60, preset_r=32.0, guard=True, shape=ROUNDED, width_us=87.0, amp=amp)
        print(f"  {name:15s} v5 {a5['trips_after_1']:3d} / {a5['worst_after_1']:.3f} A | v6 {a6['trips_after_1']:3d} / "
              f"{a6['worst_after_1']:.3f} A")
    print("B) settled from a fresh model: R (loop ohm), lead peak/cmd (sensed), max sensed, trips after 20")
    for name, load in LOADS.items():
        for shape, sn in ((ROUNDED, "rounded"), (SOFT, "soft")):
            for w in (50.0, 87.0, 130.0, 250.0):
                r5 = run(load, shape=shape, width_us=w, amp=amp)
                r6 = run(load, guard=True, shape=shape, width_us=w, amp=amp)
                print(f"  {name:15s} {sn:7s} {w:4.0f}us | v5 R {r5['r']:5.1f} pk {r5['lead_pk']:.2f} max {r5['trip_i']:.3f}"
                      f" trips {r5['trips']:3d} | v6 R {r6['r']:5.1f} pk {r6['lead_pk']:.2f} max {r6['trip_i']:.3f}"
                      f" trips {r6['trips']:3d}")
