"""One-resistor bench check of OUTPUT_BIPHASIC_PAIRS: 1 kOhm across A-B only. Body-equivalent target <= 20 mA.
Run with cwd = the se-head worktree. Owns COM13 for its duration. Logs to bench_biphasic.csv.
Sequence (channel A only, B silent): 12 steady 30 s -> 21 steady 15 s -> toggle 12/21 every 0.5 s for 10 s
-> 13 (C open: expect ~0 current) 5 s -> 12 again 10 s (fresh estimate must not overshoot) -> zero.
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
OUT = HERE / "bench_biphasic.csv"
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
    client = FocStimClient(SerialTransport("COM13"))
    eng = Engine(cfg, client, SessionLogger(Path("sessions")))
    tele = {}
    client.on("debug_teleplot", lambda b: tele.__setitem__(getattr(b, "id", "?"), getattr(b, "value", None)))
    await eng.start("biphasic")
    print("firmware", client.telemetry.firmware, "fork", eng.fork_firmware, flush=True)
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

    steps = [(12, 30), (21, 15), ("toggle", 10), (13, 5), (12, 10)]
    t0 = time.time()
    f = open(OUT, "w", newline="")
    w = None
    try:
        for route, secs in steps:
            end = time.time() + secs
            k = 0
            while time.time() < end:
                if STOPFILE.exists():
                    raise KeyboardInterrupt
                r = (12 if (k // 2) % 2 == 0 else 21) if route == "toggle" else route
                eng.set_biphasic(0, route=r, source="bench")      # also feeds the deadman
                row = {"t": round(time.time() - t0, 2), "route": r, "step": str(route)}
                st = eng.status()
                row.update({k2: st.get(k2) for k2 in ("master", "armed", "deadman_active", "faulted", "fault_reason")})
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
            print("done step", route, flush=True)
    except KeyboardInterrupt:
        print("stopped by stopfile", flush=True)
    finally:
        eng.set_master(0.0, source="bench")
        eng.disarm()
        await asyncio.sleep(0.5)
        await eng.stop("bench done")
        f.close()


asyncio.run(main())
