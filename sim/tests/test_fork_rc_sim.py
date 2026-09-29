"""Fork firmware v5 (series-RC feedforward) design checks, on the pulse-level simulation in firmware/sim/.
The sim mirrors biphasic_pairs.cpp's pulse builder and adaptation (R from the lead charge, v4; sigma from the
per-sample least-squares fit, v5) through a model of the V4 output transformers."""
import importlib.util
import sys
from pathlib import Path

import pytest


_SIM = Path(__file__).resolve().parents[1] / "biphasic_rc_sim.py"   # sim/
_spec = importlib.util.spec_from_file_location("biphasic_rc_sim", _SIM)
S = importlib.util.module_from_spec(_spec)
sys.modules["biphasic_rc_sim"] = S          # dataclasses resolve their module through sys.modules
_spec.loader.exec_module(S)


def _settle(load, rc, **kw):
    return S.settled(S.run(load, rc, pulses=150, **kw)[1])


def test_resistor_is_unchanged_and_sigma_stays_zero():
    """A pure resistor has no series C: v5 must behave exactly like v4 (sigma clamped at 0)."""
    load = S.Load(1000.0)
    for shape in (S.ROUNDED, S.SOFT):
        v4 = _settle(load, False, shape=shape, width_us=150, asym=1, amp=0.156)
        v5 = _settle(load, True, shape=shape, width_us=150, asym=1, amp=0.156)
        assert v5["sigma"] == pytest.approx(0.0, abs=1e-9)
        assert v5["pk1"] == pytest.approx(v4["pk1"], rel=1e-6)
        assert v5["pk2"] == pytest.approx(v4["pk2"], rel=1e-6)


def test_return_overshoot_on_skin_like_load_is_removed():
    """PlaStim's trip case (soft square, 130 us, symmetric) on a series-RC skin model: v4's return over-drive is
    reproduced and v5 brings both phases within 10 % of the command."""
    load = S.Load(500.0, 100e-9)
    v4 = _settle(load, False, shape=S.SOFT, width_us=130, asym=1, amp=0.156)
    v5 = _settle(load, True, shape=S.SOFT, width_us=130, asym=1, amp=0.156)
    assert v4["pk2"] > 1.4, "the sim reproduces the v4 return overshoot"
    assert v5["pk2"] < 1.1 and v5["pk1"] < 1.15
    assert 0.0 < v5["sigma"] <= S.Model(rc=True).sigma_max


def test_width_jumps_and_stroke_do_not_trip_at_higher_level():
    """Width jumps 50 <-> 250 us, Stroke-like asym 1 <-> 3 alternation and soft-square width changes, at 0.25 A
    primary on the skin model: v4 trips (stock +0.12 A margin), v5 never does after the first pulses."""
    load = S.Load(500.0, 100e-9)
    v4 = S.stroke_and_jumps(load, False, amp=0.25)
    v5 = S.stroke_and_jumps(load, True, amp=0.25)
    assert v4["trips_after_20"] > 0
    assert v5["trips_after_20"] == 0
    assert v5["worst_peak_over_limit"] < 0.9
