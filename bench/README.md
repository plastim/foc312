# Bench scripts for the fork firmware (OUTPUT_BIPHASIC_PAIRS)

Run from the repo root (or a worktree of it) with the venv: `venv\Scripts\python.exe firmware\bench\<script>.py`.
Each script **owns COM13** while it runs, so stop the engine first (`POST http://127.0.0.1:<api>/stop` or kill
`stimengine.tools.serve`). Logs go to `sessions/bench/` (gitignored). Create `sessions/bench/bench_stop.txt` to stop a
run early; every run ends by zeroing the master and disarming.

| Script | What | Load |
|---|---|---|
| `bench_v1_routing.py` | channel A at a 20 mA body-side target: 12, 21, 12/21 toggling, 13 (open C), 12 | one 1 kOhm across A-B |
| `bench_shapes_widths.py` | shapes 0/2, 50<->250 us width jumps, sweeps, shape toggling, asym 3 (Stroke-like) | one 1 kOhm across A-B |
| `summarize_routing.py` | per-step table from the routing run | - |
| `stock_baseline_ctl.py` | stock four-phase A-B at 1 kHz / 10 cycles / 50 Hz through the engine API (level file) | the tester's pads |

- The target is scaled to the V4 knob (device volume), so the knob must be up (~30 %); the scripts refuse below 0.05.
- `bench_shapes_widths.py` refuses firmware below the version it was written for (edit the `< 4` check).
- Results of every run so far: `firmware/TEST-LOG.md`.
