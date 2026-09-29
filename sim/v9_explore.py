"""v9 design exploration (2026-09-29): how far above its command does the SENSED peak sit, per shape, on dry-skin-like
loads, with the v7/v8 guard + hold? That ratio decides how much a per-shape predictive cap has to hold back."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lc_filter_sim as lc  # noqa: E402
from biphasic_rc_sim import Load, MARGIN  # noqa: E402

SHAPES = {"rounded": 0, "triangle": 3, "taper0.4": 4.4, "soft": 2, "square": 1}
LOADS = {"1k R": Load(1000.0), "dry 700R+47n": Load(700.0, 47e-9), "dry 700R+100n": Load(700.0, 100e-9),
         "500R+100n": Load(500.0, 100e-9), "450R+160n": Load(450.0, 160e-9)}


def settled_ratio(load, shape, width, amp=0.2, pulses=120):
    r = lc.run(load, pulses=pulses, guard=True, hold=True, shape=shape, width_us=width, amp=amp)
    return r["trip_i"] / amp, r["trips"], r["rho"]


if __name__ == "__main__":
    print("sensed peak / commanded peak, settled (v7/v8 guard + hold), amp 0.20 A primary; trips after 20 pulses")
    for lname, load in LOADS.items():
        for w in (130.0, 200.0, 250.0):
            cells = []
            for sname, sh in SHAPES.items():
                k, trips, rho = settled_ratio(load, sh, w)
                cells.append(f"{sname} {k:.2f}{'!' if trips else ' '}")
            print(f"  {lname:14s} {w:3.0f}us | " + " | ".join(cells))
    print(f"(the stock trip rule: sensed > command + {MARGIN} A primary; at a command a1 the allowed ratio is 1 + {MARGIN}/a1:"
          f" {1 + MARGIN / 0.1:.2f} at 0.1 A, {1 + MARGIN / 0.2:.2f} at 0.2 A, {1 + MARGIN / 0.3:.2f} at 0.3 A)")
