"""One-resistor bench check of OUTPUT_BIPHASIC_PAIRS: 1 kOhm across A-B only. Body-equivalent target <= 20 mA.
Run with cwd = the se-head worktree. Owns COM13 for its duration. Logs to bench_biphasic.csv.
v2 sequence (TEST-PLAN 5c), channel A on 12: shapes 0/1/2 at 150 us (10 s each, charge-matched by the host);
width sweep 50->120 us over 2 s per shape (8 s each); shape switched every 0.5 s (10 s); asym 3 per shape (5 s each).
v7 (TEST-PLAN 5f) adds triangle (3) and taper 0 / 50 / 100 % (ids 40 / 45 / 50) held, jumped and swept, and the
shape toggle runs through all of them.

    py -3.13 firmware/bench/bench_shapes_widths.py [COMx] [min fork version]     (defaults COM13, 7)
"""
import asyncio, csv, dataclasses, json, sys, time, tomllib
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))
from stimengine.device.client import FocStimClient            # noqa: E402
from stimengine.device.transport import SerialTransport       # noqa: E402
from stimengine.engine import Engine                          # noqa: E402
from stimengine.session import SessionLogger                  # noqa: E402

HERE = Path(__file__).resolve().parents[2] / "sessions" / "bench"   # gitignored; run with cwd = the repo (or a worktree of it)
HERE.mkdir(parents=True, exist_ok=True)
TARGET_BODY_A = 0.020
OUT = HERE / "bench_v7.csv"
PORT = sys.argv[1] if len(sys.argv) > 1 else "COM13"
MIN_FORK = int(sys.argv[2]) if len(sys.argv) > 2 else 7
TOGGLE = [0, 2, 3, 40, 45, 50]
STOPFILE = HERE / "bench_stop.txt"


def tel_row(t):
    out = {}
    for f in dataclasses.fields(t):
        if f.name == "last_update":
            continue
        v = getattr(t, f.name)
        if hasattr(v, "real") and callable(getattr(v, "real", None)) and not isinstance(v, (int, float)):
            v = list(v.real())
        elif isinstance(v, tuple):
            v = list(v)
        out[f.name] = v
    return out


async def main():
    cfg = tomllib.load(open("config/engine.toml", "rb"))
    client = FocStimClient(SerialTransport(PORT))
    eng = Engine(cfg, client, SessionLogger(Path("sessions")))
    tele = {}
    client.on("debug_teleplot", lambda b: tele.__setitem__(getattr(b, "id", "?"), getattr(b, "value", None)))
    await eng.start("biphasic")
    print("firmware", client.telemetry.firmware, "fork", eng.fork_firmware, "status fork_version", eng.status().get("fork_version"), flush=True)
    if (eng.status().get("fork_version") or 0) < MIN_FORK:
        print(f"NOT v{MIN_FORK} firmware: aborting", flush=True)
        await eng.stop(f"bench: not v{MIN_FORK}")
        return
    await asyncio.sleep(1.5)
    knob = float(client.telemetry.device_volume or 0)
    cap = float(eng.safety.amps_cap)
    if knob < 0.05:
        print("knob too low to reach the target:", knob, flush=True)
        await eng.stop("bench: knob low")
        return
    intensity = min(1.0, TARGET_BODY_A / (cap * knob))
    print(f"knob {knob:.3f} cap {cap} intensity {intensity:.3f} -> body {intensity*cap*knob*1000:.1f} mA", flush=True)
    eng.set_biphasic(1, intensity=0.0, route=34, source="bench")
    eng.set_biphasic(0, intensity=intensity, rate_hz=50, width_us=150, asymmetry=1, route=12, source="bench")
    eng.set_master(1.0, source="bench")
    eng.arm()

    steps = [("s0", 8), ("s2", 8), ("jump0", 12), ("jump2", 12), ("sw0", 8), ("sw2", 8), ("toggle", 8),
             ("a0", 5), ("a2", 5)]
    if MIN_FORK >= 7:
        steps += [("s3", 8), ("s40", 6), ("s45", 8), ("s50", 6), ("jump3", 12), ("jump45", 12), ("sw3", 8),
                  ("sw45", 8), ("a3", 5), ("a45", 5), ("toggle", 12)]
    t0 = time.time()
    f = open(OUT, "w", newline="")
    w = None
    try:
        for step, secs in steps:
            ts = time.time()
            end = ts + secs
            k = 0
            while time.time() < end:
                if STOPFILE.exists():
                    raise KeyboardInterrupt
                el = time.time() - ts
                shape, width, asym = 0, 150.0, 1.0
                if step[0] == "s" and step[1] != "w":
                    shape = int(step[1:])
                elif step.startswith("sw") and not step.startswith("jump"):
                    shape = int(step[2:])
                    ph = (el % 4.0) / 2.0                      # 0..2: up 2 s, down 2 s
                    width = 50 + 70 * (ph if ph <= 1 else 2 - ph)
                elif step.startswith("jump"):
                    shape = int(step[4:])
                    width = 50.0 if (k // 2) % 2 == 0 else 250.0      # hard width jumps every 0.5 s
                elif step == "toggle":
                    shape = (TOGGLE if MIN_FORK >= 7 else [0, 1, 2])[(k // 2) % (6 if MIN_FORK >= 7 else 3)]
                elif step[0] == "a":
                    shape, asym = int(step[1:]), 3.0
                eng.set_biphasic(0, route=12, shape=shape, width_us=width, asymmetry=asym, source="bench")
                row = {"t": round(time.time() - t0, 2), "step": step, "shape": shape, "width": round(width, 1),
                       "asym": asym}
                st = eng.status()
                row.update({k2: st.get(k2) for k2 in ("master", "armed", "deadman_active", "faulted", "fault_reason",
                                                      "fork_version")})
                for k2, v in tel_row(client.telemetry).items():
                    row[k2] = json.dumps(v) if isinstance(v, (list, dict)) else v
                row["teleplot"] = json.dumps(tele)
                if w is None:
                    w = csv.DictWriter(f, fieldnames=list(row.keys()), extrasaction="ignore")
                    w.writeheader()
                w.writerow(row)
                f.flush()
                await asyncio.sleep(0.25)
                k += 1
            print("done step", step, flush=True)
    except KeyboardInterrupt:
        print("stopped by stopfile", flush=True)
    finally:
        eng.set_master(0.0, source="bench")
        eng.disarm()
        await asyncio.sleep(0.5)
        await eng.stop("bench done")
        f.close()


asyncio.run(main())
