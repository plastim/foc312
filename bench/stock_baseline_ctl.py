"""Baseline controller: pads on A-B only (fourphase vector e1=e2=1, e3=e4=0), 1 kHz / 10 cycles / 50 Hz.
Reads the master level from level.txt (0..1) every 0.25 s; writes 'stop' in level.txt (or deletes it) to stop.
Keeps the deadman fed while running; logs telemetry + status to baseline.csv. Hard cap: level <= MAX_LEVEL.
If this script dies, the engine's deadman ramps to 0 in 2 s + 3 s."""
import csv, json, time, urllib.request
from pathlib import Path

API = "http://127.0.0.1:8331"
HERE = Path(__file__).resolve().parents[2] / "sessions" / "bench"   # gitignored; run with cwd = the repo (or a worktree of it)
HERE.mkdir(parents=True, exist_ok=True)
LEVEL = HERE / "level.txt"
OUT = HERE / "baseline.csv"
MAX_LEVEL = 0.5
MAX_RUN_S = 20 * 60


def call(path, body=None):
    data = None if body is None else json.dumps({**body, "source": "baseline"}).encode()
    req = urllib.request.Request(API + path, data=data, method="GET" if body is None else "POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=2) as r:
        return json.loads(r.read())


def main():
    call("/vector", {"e1": 1, "e2": 1, "e3": 0, "e4": 0})
    call("/carrier", {"hz": 1000})
    call("/pulse", {"frequency": 50, "width": 10, "rise_time": 0, "interval_random": 0})
    call("/volume", {"level": 0.0})
    call("/arm", {})
    t0 = time.time()
    level_now = 0.0
    with open(OUT, "a", newline="") as f:
        w = None
        while True:
            try:
                txt = LEVEL.read_text().strip() if LEVEL.exists() else "stop"
            except OSError:
                txt = str(level_now)
            if txt == "stop" or time.time() - t0 > MAX_RUN_S:
                break
            try:
                want = min(MAX_LEVEL, max(0.0, float(txt)))
            except ValueError:
                want = level_now
            if want != level_now:
                call("/volume", {"level": want, "ramp_s": 3.0})
                level_now = want
            call("/vector", {"e1": 1, "e2": 1, "e3": 0, "e4": 0})   # control input: keeps the deadman quiet
            tel = call("/telemetry")
            st = call("/status")
            row = {"t": round(time.time() - t0, 2), "level_set": level_now}
            row.update({k: st.get(k) for k in ("master", "master_target", "api_volume", "armed", "running",
                                                "deadman_active", "volume")})
            for k, v in tel.items():
                row[k] = json.dumps(v) if isinstance(v, (list, dict)) else v
            if w is None:
                w = csv.DictWriter(f, fieldnames=list(row.keys()), extrasaction="ignore")
                if f.tell() == 0:
                    w.writeheader()
            w.writerow(row)
            f.flush()
            time.sleep(0.25)
    call("/volume", {"level": 0.0})
    call("/disarm", {})


if __name__ == "__main__":
    try:
        main()
    finally:
        try:
            call("/volume", {"level": 0.0})
            call("/disarm", {})
        except Exception:
            pass
