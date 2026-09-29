"""v9 predictive peak guard (sim/v9_sim.py): no trips in the scenarios that tripped v8 (the 2026-09-29 box 2 trips
among them), and no less delivered charge than v8 (within 0.03)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import v9_sim as V  # noqa: E402

CASES = ["swing soft 195-253us 0.13-0.32A", "soft 200us step 0.10->0.45A", "rounded 200us step 0.10->0.45A",
         "rounded<->soft 200us 0.35A", "taper0.4 130us 0.46A steady"]


@pytest.mark.parametrize("scen", CASES)
@pytest.mark.parametrize("load", ["1k R", "dry 700R+47n", "500R+100n"])
def test_v9_never_trips_and_delivers_as_v8(scen, load):
    seq, ld = V.SCENARIOS[scen], V.LOADS[load]
    v8, v9 = V.scenario(scen, V.V8(), ld, seq), V.scenario(scen, V.V9(), ld, seq)
    assert v9["trips"] == 0 and v9["worst"] < 1.0
    assert v9["delivered"] >= v8["delivered"] - 0.03


def test_v8_did_trip_on_the_swing():                  # the sim reproduces the second 2026-09-29 trip
    seq = V.SCENARIOS["swing soft 195-253us 0.13-0.32A"]
    assert V.scenario("s", V.V8(), V.LOADS["dry 700R+47n"], seq)["trips"] > 0
