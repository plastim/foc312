"""Firmware v6 peak guard, checked in firmware/sim/lc_filter_sim.py (V4 output LC filter, sense = bridge current)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # sim/

import lc_filter_sim as L  # noqa: E402
from biphasic_rc_sim import MARGIN, ROUNDED, SOFT, Load  # noqa: E402

AMP = 0.142                     # the tripping pulse's commanded peak (TEST-LOG 2026-09-26, v5 leg)
SKIN = Load(450.0, 100e-9)


def test_guard_recovers_from_the_leg_trip_state_without_tripping():
    # every bin at 32 ohm (the trip report's estimate), rounded 87 us: v5 trips again, v6 stays under the ceiling
    v5 = L.run(SKIN, 40, preset_r=32.0, shape=ROUNDED, width_us=87.0, amp=AMP)
    v6 = L.run(SKIN, 40, preset_r=32.0, guard=True, shape=ROUNDED, width_us=87.0, amp=AMP)
    assert v5["trips_after_1"] > 0
    assert v6["trips_after_1"] == 0
    assert v6["worst_after_1"] <= AMP + MARGIN - L.GUARD_TRIP_FRAC * MARGIN + 0.015


def test_guard_removes_settled_trips_on_capacitive_skin():
    v5 = L.run(SKIN, 80, shape=SOFT, width_us=250.0, amp=AMP)
    v6 = L.run(SKIN, 80, guard=True, shape=SOFT, width_us=250.0, amp=AMP)
    assert v5["trips"] > 0
    assert v6["trips"] == 0


def test_guard_leaves_a_resistor_alone():
    for shape, w in ((ROUNDED, 87.0), (SOFT, 130.0)):
        v5 = L.run(Load(1000.0), 80, shape=shape, width_us=w, amp=AMP)
        v6 = L.run(Load(1000.0), 80, guard=True, shape=shape, width_us=w, amp=AMP)
        assert abs(v6["r"] - v5["r"]) < 1e-6


def test_guard_only_lowers_the_estimate():
    for shape in (ROUNDED, SOFT):
        for w in (50.0, 130.0, 250.0):
            v5 = L.run(SKIN, 80, shape=shape, width_us=w, amp=AMP)
            v6 = L.run(SKIN, 80, guard=True, shape=shape, width_us=w, amp=AMP)
            assert v6["r"] <= v5["r"] + 1e-6


# ---- v7: climb hold ------------------------------------------------------------------------------------------------
TUG = [(Load(450.0, 100e-9), SOFT, 130.0), (Load(450.0, 160e-9), SOFT, 255.0), (Load(300.0, 220e-9), SOFT, 130.0)]


def test_v6_guard_fights_the_adaptation_on_skin():
    # the tug of war PlaStim saw on EMS 1 (2026-09-28): settled, the guard fires on a large share of the pulses
    for load, shape, w in TUG:
        v6 = L.run(load, 200, guard=True, shape=shape, width_us=w, amp=0.10)
        assert v6["guard_rate"] > 0.3 and v6["r_spread"] > 0.05


def test_v7_hold_settles_under_the_ceiling():
    for load, shape, w in TUG:
        v6 = L.run(load, 200, guard=True, shape=shape, width_us=w, amp=0.10)
        v7 = L.run(load, 200, guard=True, hold=True, shape=shape, width_us=w, amp=0.10)
        assert v7["guard_rate"] == 0 and v7["held_rate"] > 0.5 and v7["r_spread"] < 0.02
        assert v7["r"] <= v6["r"] and v7["trips"] == 0


def test_v7_hold_leaves_a_resistor_alone():
    for shape, w in ((ROUNDED, 87.0), (SOFT, 130.0), (ROUNDED, 255.0)):
        v6 = L.run(Load(1000.0), 80, guard=True, shape=shape, width_us=w, amp=AMP)
        v7 = L.run(Load(1000.0), 80, guard=True, hold=True, shape=shape, width_us=w, amp=AMP)
        assert abs(v7["r"] - v6["r"]) < 1e-6 and v7["held_rate"] == 0


def test_v7_hold_only_lowers():
    for load in (SKIN, Load(300.0, 220e-9), Load(1000.0)):
        for shape in (ROUNDED, SOFT):
            for w in (50.0, 130.0, 250.0):
                v6 = L.run(load, 120, guard=True, shape=shape, width_us=w, amp=AMP)
                v7 = L.run(load, 120, guard=True, hold=True, shape=shape, width_us=w, amp=AMP)
                assert v7["r"] <= v6["r"] + 1e-6 and v7["trips"] <= v6["trips"]


# ---- v7: shapes ----------------------------------------------------------------------------------------------------
def test_v7_taper_ends_are_rounded_and_square_and_triangle_is_half():
    from biphasic_rc_sim import SQUARE, TRIANGLE, shape_integral
    for w in (2.0, 3.5, 6.5, 12.25, 20.0):
        for t in (0.0, 0.3, 1.0, w / 3, w / 2, w - 0.5, w):
            assert abs(shape_integral(4.0, t, w) - shape_integral(ROUNDED, t, w)) < 1e-9
            assert abs(shape_integral(5.0, t, w) - shape_integral(SQUARE, t, w)) < 1e-9
        assert abs(shape_integral(TRIANGLE, w, w) - w / 2) < 1e-9
        # the family is monotonic in the flat top: more flat, more charge, between rounded and square
        q = [shape_integral(4.0 + k / 10, w, w) for k in range(11)]
        assert all(a < b for a, b in zip(q, q[1:]))


def test_v7_shapes_play_without_trips_on_skin():
    for shape in (3, 4.0, 4.5, 5.0):
        r = L.run(SKIN, 120, guard=True, hold=True, shape=shape, width_us=130.0, amp=AMP)
        assert r["trips"] == 0
