# Fork firmware test log

## 2026-09-26: first flash, stock-mode regression on the tester's leg (no resistor loads)

- **Decision:** PlaStim chose his leg over built resistor loads for the stock-mode regression. The stock code path is
  unchanged by the fork (additive only). The biphasic mode still gets a one-resistor pre-check before body use (see
  the conversation of 2026-09-26; TEST-PLAN §0 step 3 adapted).
- **Image:** `focstim_v4` built from commit c538325 (live routing). SHA-256
  `4b1c14b99db97945f91ba8f96fdfc74f1dbad338902337b96efa71b2ee9d82c5`. Flashed by PlaStim with the restim firmware updater.
- **After the flash:** firmware reports 1.3.2, branch `main`, comment `stim-engine biphasic-pairs`; capabilities
  unchanged (threephase, fourphase, battery, device_volume, max 0.2 A, lsm6dsox).
- **Setup, both runs:** stim-engine (HEAD worktree), four-phase, vector e1 = e2 = 1, e3 = e4 = 0 (pads on A and B,
  one leg; C and D open). Carrier 1 kHz, 10 cycles, 50 Hz requested, master 0.3, device knob ≈ 0.28. 60 s windows
  after the model settled.

| | Stock 1.3.2 (before) | Fork c538325 (after) |
|---|---|---|
| Device knob | 0.283 | 0.287 |
| Commanded peak | 8.45 mA | 8.51 mA |
| Measured peak, A / B | 7.11 / 8.34 mA | 7.22 / 8.75 mA |
| RMS, A / B | 2.36 / 2.78 mA | 2.37 / 2.86 mA |
| Model output impedance, A / B | 13.8 / 23.0 Ω | 13.7 / 21.4 Ω |
| Skin impedance estimate, A / B | 433 / 715 Ω | 203 / 359 Ω |
| Drive voltage | 2.85 V | 1.93 V |
| Output power (skin) | 22.5 (6.2) mW | 11.2 (3.7) mW |
| Achieved pulse rate | 33.1 Hz | 33.2 Hz |
| C / D (open) | ~1 mA peak sense noise | ~1 mA peak sense noise |

- **Result: pass.** Current per electrode within 1–5 %, same model convergence (the adaptive model starts at
  5.5 Ω per electrode and takes ~40 s to reach the delivered current in both runs), same achieved pulse rate.
- **Skin impedance halved** between the runs (pads on ~10 min longer; hydration), so drive voltage and power fell
  at the same current. PlaStim reported the after-flash sensation as faint at the same current: consistent with
  lower skin impedance and habituation, not with a firmware change (the delivered current matched).
- **33 Hz, not 50 Hz:** 10 cycles at 1 kHz is a 10 ms pulse; stock scheduling can't fit 50 of them per second.
  Same before and after.
- Raw logs (not committed): scratchpad `baseline_stock_leg_AB.csv`, `afterflash_leg_AB.csv`, 4 Hz telemetry.

## 2026-09-26: biphasic-pairs mode, one 1 kOhm 1/4 W across A-B (C, D open)

- **Setup:** fork c538325, bench script (engine `set_biphasic`), channel A only, target 20 mA body-side, 50 Hz,
  150 us, asym 1, knob 0.307. Sequence: 12 (30 s), 21 (15 s), 12/21 toggled every 0.5 s (10 s), 13 (5 s), 12 (10 s).
- **No faults, no e-stops, no timing errors.** Achieved rate 50.0 Hz in every step. Channel B (silent) and the
  unrouted electrodes stayed at sense-noise level.

| Step | Peak A / B | RMS A / B | Model Z (per electrode) | bp_qnet_a (leaky, uC) |
|---|---|---|---|---|
| 12 | 23.9 / 19.8 mA | 1.87 / 1.63 mA | 12.7 ohm | +22.8..23.0 |
| 21 | 19.4 / 24.3 mA | 1.59 / 1.90 mA | 12.7 ohm | +28.5..29.1 |
| toggle 12/21 | 23.7 / 24.3 mA | 1.73 / 1.77 mA | 12.5 ohm | +0.1..6.1 |
| 13 (C open) | 31.3 / 0.6, C 29.0 mA | 2.6 / 0.07 mA | A, C 22.3 ohm | +24..35 |
| 12 again | 23.8 / 19.8 mA | 1.86 / 1.63 mA | 12.7 ohm | +17..23 |

- **Reversal works:** the higher reading follows the leading electrode (A in 12, B in 21), so it is the phase, not
  a sense-channel gain mismatch.
- **Finding 1, lead vs return:** the leading phase measures about 20 % higher peak than the return phase, and the
  per-pulse measured charge differs by about 12-15 % (qnet ~ 100 x the per-pulse difference; per-pulse phase charge
  ~1.9 uC). It follows the leading electrode and almost vanishes when the lead alternates every 0.5 s. That points
  at the transformer's flux state: the sense reads PRIMARY current, which includes magnetizing current that never
  reaches the body. The transformers pass no DC, so the skin sees no net DC either way. The body-side split
  between the phases is unmeasured: it needs a sense resistor and a scope (TEST-PLAN 4.2 / 4.7).
- **Finding 2, open route:** with C open (route 13) the sense still reports ~30 mA on A and C and the model
  estimate climbs (22 ohm): that is magnetizing current with no load, not delivered current. The firmware cannot
  tell an open electrode from a load (stock can't either). Candidate improvement: an open-circuit flag from the
  phase shape of the sensed current.
- **First pulses after returning to 12:** the loaded electrode (B) read 19.4 mA in the first telemetry window,
  at or below steady. A's first window still held step-13 peaks (telemetry peak is a 50-pulse max), so A's
  first-pulse behaviour is not resolved by this data.
- **Result: pass for low-level body use** (PlaStim's call to use his leg without the full resistor matrix), with
  findings 1-2 open for the scope.

## 2026-09-26: v2 flashed (b67f596, SHA-256 0fd5cc66...72d41), TEST-PLAN 5c on one 1 kOhm across A-B

- Flashed by PlaStim (restim updater). `fork_version` 2 detected by the host (comment `stim-engine biphasic-pairs v2`).
- Host ff39898 (charge-matched shapes), channel A on 12, 50 Hz, 150 us, target 20 mA peak (rounded), knob 0.314.
- **No faults and no e-stops in any step, square included** (the sharp-edge ringing risk through the output LC
  filter did not trip the per-sample over-current check at this level). Rate 50.0 Hz in every step.

| Step | RMS A / B | Peak A / B | bp_qnet_a (uC) |
|---|---|---|---|
| rounded | 1.84 / 1.49 mA | 23.2 / 18.7 mA | +12..35 |
| square | 1.69 / 1.44 mA | 17.1 / 18.2 mA | +37..38 |
| soft square | 1.78 / 1.49 mA | 19.3 / 17.6 mA | +32..33 |
| sweeps 50-120 us (each shape) | 1.2-1.3 mA | 16-21 mA | -8..+9 |
| shape switched every 0.5 s | 1.81 / 1.51 mA | 24.1 / 23.3 mA | +24..35 |
| asym 3, rounded / square / soft | 2.15 / 0.83, 1.85 / 0.80, 1.99 / 0.80 mA | ~20-27 / 6.5-7.8 mA | +67..94 |

- **5c.2 pass:** charge-matched shapes give RMS within 9 % of each other; the square's peak is lower, as it should
  be at equal charge (2/pi of the rounded peak, ~0.64; measured 0.74 through the output filter).
- **5c.3 not resolved by telemetry:** the RMS/peak notifications are 50-pulse windows sent about once a second, so a
  2 s sweep can't be judged for smoothness from them. The per-pulse charge continuity is verified in the firmware
  math (NOTES §8 v2); the physical check needs the scope, or PlaStim's feel.
- **5c.4 pass:** shape switched every 0.5 s: no gap, no e-stop, no timing error.
- **5c.5 finding:** with asym 3 the measured lead-minus-return charge roughly triples (qnet +67..94 uC, vs +12..38 at
  asym 1). The long low return phase is measured on the other electrode at about a third of the current, where
  magnetizing current and the one-way sense matter most. As before, the transformers pass no DC; the body-side
  balance of long asymmetric pulses is a scope item. ET-312-style asymmetric pulses are used at low levels until then.
- **Result: pass for low-level body use.**

## 2026-09-26: v3 flashed (cea3a42, SHA-256 d4f76136...e143), one 1 kOhm across A-B

- Context: two more latches on the tester's leg under v2: (1) 34/12 routed channel A onto 3-4 with no pads (open pair:
  magnetizing current climbs with the estimate until the e-stop), and (2) a pattern swinging width (commanded
  ~14 mA, reading ~2.3x), attributed to one impedance estimate per pair on capacitive skin. v3 = width-aware
  model + trip report; the host got a "pads connected" guard for (1).
- Knob 0.291, target 20 mA peak, 50 Hz. **No faults, no e-stops.** Rate 50.0 Hz throughout.

| Step | RMS A | Peak A mean / max | Peak B max | Estimate (per electrode) | bp_qnet_a |
|---|---|---|---|---|---|
| rounded 150 us | 1.67 mA | 21.6 / 23.9 mA | 19.3 mA | 13.0 ohm | +4..35 |
| soft square 150 us | 1.78 mA | 19.2 / 19.4 mA | 17.6 mA | 13.0 ohm | +32 |
| rounded, width jumping 50 <-> 250 us every 0.5 s | 1.92 mA | 25.7 / 33.5 mA | 27.8 mA | 13.9 ohm | +45..54 |
| soft square, same jumps | 1.85 mA | 23.0 / 25.0 mA | 29.7 mA | 10.3-13.3 ohm | +46..50 |
| sweeps 50-120 us | 1.3 mA | 18-21 mA | 21-22 mA | 9.1-12.2 ohm | -8..+18 |
| shape switched every 0.5 s | 1.16 mA | 22.4 / 23.8 mA | 19.5 mA | 12.9 ohm | +13..20 |
| asym 3, rounded / soft | 2.15 / 1.97 mA | 26.6 mA | 19.1 / 6.4 mA | 14.5 ohm | +61..93 |

- **Pass on the resistor.** A resistor has no capacitance, so it cannot reproduce the skin trip; that test is PlaStim's
  leg with the trip report armed.
- **Observation, width jumps:** on the wide (250 us) pulses the sensed peak reaches ~33 mA for a 20 mA command on a
  pure resistor. The sense reads primary current, which includes magnetizing current; that grows with volt-seconds
  (amplitude x width). Wide pulses at higher levels therefore eat into the stock +0.12 A e-stop margin by
  themselves. Candidate firmware improvement: subtract the modelled magnetizing current (from the known volt-seconds
  and the transformer inductance) before the over-current comparison, i.e. compare load current, not primary
  current. That keeps the margin's meaning the same; it does not widen it.

## 2026-09-26: v4 flashed (28a37a0, SHA-256 31697134...bd70); restim check; one 1 kOhm across A-B

- **restim on the fork (first time):** PlaStim played from restim on his pads after the flash: "restim good". Closes
  the "restim not tested on the fork" gap.
- Cause fixed in v4 (from the v3 trip report on Stroke): asymmetric return phases read low, so the both-phase
  adaptation ran the estimate up until the lead phase hit ~2x and tripped. v4 adapts on the lead phase only.
- Same sequence as v3, knob 0.291, target 20 mA peak. **No faults.** v3 -> v4, channel A lead-phase peak (mean):

| Step | v3 | v4 | Estimate v3 -> v4 |
|---|---|---|---|
| rounded 150 us | 21.6 mA | 19.7 mA | 13.0 -> 11.8 ohm |
| soft square | 19.2 | 17.8 | 13.0 -> 12.0 |
| width jumps, rounded (max) | 33.5 | 28.2 | 13.9 -> 11.7 |
| **asym 3, rounded** | **26.6 (1.33x)** | **21.8 (1.09x)** | **14.5 -> 11.8** |
| asym 3, soft | 21.9 | 18.0 | 14.5 -> 11.9 |

- **Pass.** The lead phase now holds at about the command on asymmetric pulses, and the estimate no longer climbs
  on them. Overall the lead peaks sit closer to the target in every step.

## 2026-09-26: v5 flashed (d279141, SHA-256 c3d00f91...0b89b) with firmware/flash.py

- Checked before the flash: the hex matches the helper's reported SHA-256; a rebuild from d279141 on the laptop is
  byte-identical (Flash 158,640 B, RAM 49,352 B); `python -m pytest` at d279141: 1360 passed.
- **Flashed on PlaStim's go** with the new `firmware/flash.py` (a CLI port of restim's updater: HDLC enter-bootloader
  frame, then the STM32 ROM bootloader through the ESP32 bridge at 115200 8E1, via diglet48/stm32loader@c71b592).
  It refuses without the expected SHA-256, off-range segments, or a non-G473/256 KB target. Log: application
  answered HDLC, enter-bootloader sent, bootloader ACK, STM32G47xxx/48xxx 256 KB, extended erase, 61,152 B at
  0x08000000 + 98,028 B at 0x08040000, both verified by readback, go.
- **After the flash:** firmware reports 1.3.2, branch `main`, comment `stim-engine biphasic-pairs v5`.
- Next: TEST-PLAN 5d.2 on one 1 kOhm across A-B (sigma must stay 0.00), then the tester's leg (5d.3-5d.5), watching
  `bp_sigma_a`, at the v4 limits (<= ~0.15 A primary, Rounded preferred, Stroke low).

## 2026-09-26: v5 TEST-PLAN 5d.2, one 1 kOhm 1/4 W across A-B (C, D open)

- `bench_shapes_widths.py` (the v3/v4 sequence), knob 0.314, target 20 mA peak body-side, route 12, 50 Hz.
  **No faults, no e-stops.** fork_version 5.

| Step | RMS A | Peak A mean / max | Peak B max | Estimate (per electrode) | bp_sigma_a | bp_qnet_a |
|---|---|---|---|---|---|---|
| rounded 150 us | 1.27 mA | 17.1 / 21.9 mA | 17.6 mA | 11.3-11.8 ohm | 0.000 | +0..32 |
| soft square 150 us | 1.62 | 18.5 / 21.8 | 17.5 | 11.7-11.9 | 0.000 | +29..32 |
| rounded, width jumping 50 <-> 250 us | 1.65 | 23.5 / 28.7 | 24.6 | 11.6-11.9 | 0.000..0.106 | +24..39 |
| soft square, same jumps | 1.66 | 21.9 / 27.4 | 29.4 | 11.4-11.7 | 0.16..0.243 | +39..47 |
| sweeps 50-120 us, rounded | 1.37 | 20.5 / 21.0 | 22.5 | 10.2-11.4 | 0.03..0.248 | +1..47 |
| sweeps 50-120 us, soft | 1.23 | 18.5 / 18.7 | 23.2 | 10.2-11.2 | 0.02..0.127 | -3..+7 |
| shape switched every 0.5 s (150 us) | 1.05 | 20.0 / 21.6 | 22.6 | 11.4-11.7 | 0.034 -> 0.000 | +3..18 |
| asym 3, rounded / soft | 1.66 / 1.60 | 21.7 / 18.3 (max 21.8) | 17.2 / 5.3 | 11.6-11.9 | 0.000 | +18..77 |

- **Peaks and faults: pass.** Peaks sit in the v3/v4 range (v4 rounded jumps max 28.2 mA; v3 soft jumps B 29.7).
- **sigma: fails the letter of 5d.2** ("stays 0.00 throughout"). It is 0.000 at every step at 150 us (both shapes,
  shape toggling, asym 3). On narrow pulses it settles to a steady non-zero value on a pure resistor: ~0.22-0.24
  through the soft-square 50 <-> 250 us jumps (held for 11 s, so a bias, not a transient) and ~0.1 at 75-95 us in the
  sweeps. The sim has no output LC filter (~22 us) or deadtime; at 50-100 us those are a large fraction of the phase,
  and the fit reads them as a series C. Effect: on narrow pulses a little more drive late in the lead phase and less
  in the return; the measured peaks above show it is small. PlaStim's trip case (130 us) sits where the bias is ~0.
- Candidate fix (not done): fit sigma against the command passed through the modelled output-filter lag, or only
  from the lead/return samples beyond ~2 filter time constants; recheck on the resistor that sigma stays ~0.

## 2026-09-26: v5 on the tester's leg (pads A-B), foc312 fork output: over-current trip after ~40 s

- Engine at 605073c, fork_version 5. Commanded peak rose to 31.6 mA body-side (~0.21 A primary), above the
  <= ~22 mA agreed for the first v5 run. Route 12 for most of it, then 12 <-> 21.
- **Return phase: v5 worked.** Early on B (return, route 12) read 1.2-1.5x the command, as on v4. sigma_a rose to
  0.28 and B came down to ~1.0x and stayed there.
- **Trip report:**
  - `current limit exceeded (limit 0.262 A primary)`
  - `meas a 0.003 b -0.271 ... at sample 4 of 13`
  - `cmd peak 0.142 A primary, lead 87.1 us, return 87.1 us, shape 0, route 21`
  - `r_est 31.95 ohm, sigma 0.00 (bin 1 + 0.92), v_drive 4.31 V, scale 1.00`
- **Reading:** LEAD phase (route 21 leads on electrode 2 = b; the lead is samples 0-4, and 4 is its last, partial
  sample), rounded, 87 us, 1.91x the command, **sigma 0** (so not the v5 term), and under the 0.15 A cap. The
  estimate for that bin (60-90 us) was 32 ohm loop, about twice the pair's wider-width estimates (13-17 ohm loop in
  telemetry). An over-high R at narrow widths over-drives the lead, and the lagged current peaks at the lead's last
  sample.
- **Likely cause (unproven):** `model_update` counts the lead's measured charge only in samples where the lead
  electrode is commanded below -0.02 A. On a narrow rounded phase the edge samples and the lagged tail (output filter,
  leakage; it runs into the partial last sample and the first return samples) carry lead-direction current that
  isn't counted, so the lead reads low, rho < 1, and R climbs. The 1 kOhm sweeps (50-120 us) did not show the climb
  (estimate 10.2-11.4 ohm vs 11.7 at 150 us), so skin + lag must do something the resistor doesn't; to be checked.
- Candidate v6: count the lead electrode's measured lead-direction current over the whole pulse window (all samples
  where it is sensed cathodic, until the return current takes over), and use the same window for the sigma fit;
  check the narrow-width estimate on the resistor and in the sim with an output-filter model.

## 2026-09-26: v6 flashed (ad83c49, SHA-256 81b948c6...b1e085) with firmware/flash.py

- Built both V4 envs clean (Flash 158,960 B, RAM 49,364 B); `python -m pytest` 1365 passed (incl.
  tests/test_fork_lc_sim.py for the guard). Flashed on PlaStim's go: bootloader entered from the running app, erase,
  61,160 B at 0x08000000 + 98,340 B at 0x08040000, both verified by readback, go.
- Boot self-test after the flash: all PASS (drivers off 0.14 mA; A-D 2.15-2.29 mA; ABCD 8.24 mA).
- Firmware reports 1.3.2, branch `main`, comment `stim-engine biphasic-pairs v6`.
- **5e.2 (1 kOhm) skipped by PlaStim's decision** ("lets skip the check this time"): v6 only lowers the estimate
  and leaves the e-stop unchanged; boot self-test passed. Straight to his leg (5e.3), engine at ef81e85.

## 2026-09-26: v6 on the tester's leg (pads A-B), foc312 fork output: no trips

- Engine ef81e85 (logs width/shape/route/asymmetry/rate). foc312 patterns, Rounded, lead widths 50-200 us, routes
  12 / 21 / 34 / 43 (34/43 onto pads marked not connected: zeroed by the host guard), asymmetry 1.
- **No trips, no faults.** Commanded peak up to 61.2 mA body-side, far past where v5 tripped (0.142 A primary);
  max sensed peak 73.5 mA. At a steady command the lead read 1.0-1.3x and the return 1.28-1.39x.
- **Peak guard:** acted on 1,966 pulses. At 37-61 mA it acted on roughly every other pulse to every pulse: it is
  the effective limiter up there, holding the peaks under limit - margin/2 (pulses fuller rather than sharper).
- sigma_a reached 0.42 on route 12 (the return-phase correction working).
- Telemetry artifact (not a fault): the peak telemetry is a 50-pulse max (~2 s at 23 Hz), so right after a level
  drop peak/commanded reads 2-11x. Compare peaks with the highest command over that window.
- PlaStim: "that worked well".

## 2026-09-27: second box (box 2, COM17) flashed to v6 (81b948c6...b1e085)

- Box 2: FOC-Stim V4, 42TL004 transformers (PlaStim: "same as other"), USB serial COM17 (ESP32-S3, VID 303A; box 1
  is COM13). Before: stock **1.1.2**, max 0.15 A, knob 0, battery 4.20 V / 100 % on wall power, wifi 192.168.1.51.
- `firmware/flash.py --backup`: read the whole 256 KB through the ROM bootloader before erasing:
  `firmware/release/box2_focstim_v4_v1.1.2_stock_backup.hex` (SHA-256 c11699f2...bdd13). 1.1.2 is no longer
  published upstream (oldest release is 1.3.0), so this backup is the only restore image for that version.
- Flashed on PlaStim's go ("flash it to our firmware"): 61,160 + 98,340 B, both verified by readback. Boot self-test:
  all PASS (vsys 5.02 V, vbus 4.92 V, boost to 10.03 V, drivers 2.04-2.26 mA, ABCD 8.06 mA).
- Firmware reports 1.3.2 `stim-engine biphasic-pairs v6`, max 0.2 A. Engine switched to COM17 (supervisor).
- Not done: the 1 kOhm check on box 2.

### Still open

- Biphasic-pairs mode: one 1 kΩ 1/4 W across the pair at ≤ 20 mA, telemetry check (currents, `bp_qnet`, routing),
  then the tester's leg, starting low.
- TEST-PLAN §4–§7 on real loads when 3 W resistors arrive.

## 2026-09-28: v7 built, not flashed yet (SHA-256 26bf86bd...e8f7)

- **Why:** on the M5 remote (box 2, v6) PlaStim saw `bp_guard` climb ~+500 per 10 s on EMS 1 (130 us, 163 Hz, bursts),
  barely changed by MA or by switching rounded -> soft square: the charge adaptation and the guard fighting
  (NOTES.md v7). PlaStim: "go on firmware 7".
- **Image:** `focstim_v4`, comment `stim-engine biphasic-pairs v7`, SHA-256
  `26bf86bdae4b71c5459791bb0b8efd59b5872f1db2a8af42af179f52b2e6e8f7` (copy in Downloadsocstim_v4_fork_v7.hex).
  Flash 61.0 %, RAM 37.7 %.
- **Sim:** `tests/test_fork_lc_sim.py` 10 pass (v6 tug of war reproduced on skin with soft square; v7 hold: no guard
  events, no swing, never above v6, a resistor identical to v6; taper ends equal rounded / square exactly).
- **Host:** triangle (3) and taper (40..50) in `device/fork.py`, charge-matched; the engine plays rounded on a box
  below v7. Also fixed: since the shape fade was shortened to 20 + 40 ms (this morning), the tick that switched the
  shape could send the new shape at up to ~40 % instead of from 0 (the fade-in counted from the fade-out's start, and
  a 17 ms tick could skip the zero). Now the switch goes out on a tick that sends 0 and the fade-in counts from there
  (engine and remote core alike); `test_v2_shape_change_fades_out_and_in` failed before this and passes after.
- **Next:** TEST-PLAN 5f.1-5f.2 on a 1 kOhm with PlaStim's go for the flash, then 5f.3-5f.4.

## 2026-09-28: v7 flashed on box 2 (COM17), resistor check skipped by PlaStim

- `firmware/flash.py` (dry run, then `--sha256 26bf86bd...e8f7 --port COM17`); the M5 remote stopped first. Boot
  self-test: all PASS (vsys 5.05 V, vbus 4.95 V, charge to 10.01 V, drv A/B/C/D 2.22/2.15/2.08/2.22 mA, ABCD 8.06 mA).
  Firmware reports 1.3.2 `stim-engine biphasic-pairs v7`.
- **TEST-PLAN 5f.2 (1 kOhm) skipped: PlaStim's decision** ("lets skip the check, i lost the resistor ... ill be careful").
  v7 changes pulse building (triangle, taper), not only the guard, so 5f.2 is still owed when a resistor turns up.
  Rollback image: Downloads\focstim_v4_fork_v6.hex (81b948c6...b1e085). Box 1 (COM13) stays on v6.

## 2026-09-28: v7 on the tester's leg: A + B on the same pads, B flipped to 21: trip; v8 built (not flashed)

- Remote, box 2 (v7), A on 12 and B brought onto 12 (PlaStim: only pads 1-2 on the leg). Passive USB log
  (sessions/_logs/box2-listen.log): `bp_guard_any` ~17/s, `bp_guard_lead` 0, `bp_hold` ~30/s, pk A/B 0.98/0.94,
  rho A/B 0.90/0.80 and falling, r 11.4 ohm and being pushed down.
- Then B's polarity flipped to 21: trip. `biphasic: current limit exceeded (limit 0.304 A primary)`;
  `meas a -0.309 b 0.001 c 0.000 d -0.000 A primary at sample 12 of 17`; `cmd peak 0.184 A primary, lead 130.0 us,
  return 130.0 us, shape 2 (0.00), route 21`; `r_est 15.79 ohm, sigma 0.44 (bin 2 + 1.00), v_drive 3.70 V`.
  The remote showed only "request timed out": its reconnect attempts wiped the trip lines (fixed in the remote).
- **v8** (NOTES.md): one model per direction, reverse seeding at 0.7, "any"-ceiling location counters. SHA-256
  `27e44ac1a761289231216defcdd0095e337865269feebc93f5dea05567c1ec0e` (Downloadsocstim_v4_fork_v8.hex).
  Flash 61.3 %, RAM 38.0 %. Not flashed; the 1 kOhm check (5f.2) is still owed from v7.

## 2026-09-28: v8 flash to box 2 failed mid-write; box 2 needs boot-button recovery

- PlaStim okayed flashing v8 while out. The M5 remote was not on USB (could not be confirmed stopped) and was paired to
  box 2 over Wi-Fi. The first `flash.py` run failed while writing: `0x31 programming failed: 0x49` (a stray byte in
  the bootloader conversation; the box's ESP32 forwards Wi-Fi traffic to the STM32 too, and the remote reconnects
  every few seconds once the box goes silent).
- flash.py now retries each 256-byte block and read-back chunk on a garbled answer, and re-erases / re-writes on a
  verify mismatch (3 passes); nothing counts until the read-back matches. A second run erased and wrote, then every
  read-back retry timed out; after that the STM32 bootloader stopped answering entirely (filler bursts and GET: no
  bytes). Not verified yet whether the retries help on a clean link.
- **State:** box 2 has no valid firmware. Recovery: hold the STM32 boot button while switching the box on, then
  `flash.py <hex> --sha256 ... --port COM17` (it finds the bootloader already active). **Switch the remote off (or
  out of range) first.** Images: Downloads\focstim_v4_fork_v8.hex (27e44ac1...ec0e), v6 (81b948c6...b1e085).
- Rule from now: never flash a box whose Wi-Fi has a live client; the flash tool / app must check first.

## 2026-09-28: box 2 recovered, v8 flashed and verified

- With the M5 remote's own Wi-Fi turned off (its config loaded with `[direct] enabled = false`), PlaStim power-cycled box
  2 normally: it booted the v8 image the second (unverified) run had written, self-test all PASS, comment v8.
- Re-flashed v8 cleanly with `flash.py` (firmware/release/focstim_v4_fork_v8.hex, 27e44ac1...ec0e): no retries at all,
  both segments verified. That confirms the cause of the failure: Wi-Fi traffic from the remote reaching the STM32
  bootloader through the box's ESP32. The block retries were never exercised on a clean link.
- Remote's direct Wi-Fi back on; box 2 re-paired over USB (192.168.4.2). The 1 kOhm check (5f.2) is still owed.

## 2026-09-29: box 2 (v8, reflashed v8 by the hub the same morning) tripped twice from the PC player, dry pads

- **18:02, Intense, taper 0.4, 130 us, A 13 / B 24, ~0.10 A body asked on B:**
  `current limit exceeded (limit 0.580 A primary)`, `meas b -0.582 at sample 5 of 17`, `cmd peak 0.460, route 24`,
  `r_est 21.66, sigma 0.33 (bin 2 + 1.00), v_drive 11.79 V, scale 1.00`. 1.27x the command; the stock rule allows
  1 + 0.12 / 0.46 = 1.26x at that command.
- **18:13, a swinging pattern (width 195-253 us, rate 52-198 Hz, amps swinging 0.037-0.091 A body several times a
  second), soft square, A 12:** `limit 0.402`, `meas a -0.411 at sample 4 of 23`, `cmd peak 0.282, lead 198 us,
  shape 2, route 12`, `r_est 27.33, sigma 0.09 (bin 4 + 0.11), v_drive 8.21 V, scale 1.00`. 1.46x.
- PlaStim was testing without gel (dry pads). Voltage use peaked at 59 %, flux at 65 %: not drive-limited.
- Reading: on skin the settled sensed / command ratio is ~1.25-1.29 for every shape (sim/v9_explore.py), and the
  ratio the stock rule allows shrinks as the command grows. v6/v7 correct after a pulse, for the amplitude and width
  just played; a rising amplitude or a jump to another width bin gets there first. -> v9 (predictive peak guard).
- Host side the same day: the hub now waits for the box after a fault and reconnects once it is power-cycled.

## 2026-09-29: v9 built (not flashed)

- v9 = the predictive peak guard (NOTES.md, v9). sim/v9_sim.py mirrors it: 0 trips where v8 tripped 1-20 times (the
  swing reproduces the 18:13 trip), delivered charge within 0.01-0.03 of v8; all 29 sim tests pass.
- `.pio/build/focstim_v4/firmware.hex` SHA-256 4d0428680c7bdc921c4610359cf7746b740847e5f74dbafbf520e567bbab8787,
  Flash 61.7 %, RAM 39.0 %. Comment `stim-engine biphasic-pairs v9`. Not flashed; 5g.2 (1 kOhm) before a body.

## 2026-09-29: v9 flashed to box 2

- PlaStim's go. Checked first over USB: engine stopped, box 2 on v8, its Wi-Fi address 0.0.0.0 (not linked to the
  M5 remote). `flash_focstim.py release/focstim_v4_fork_v9.hex --sha256 4d042868...8787 --port COM17`: erase, write
  61352 + 100908 B, both segments verified, started: comment `stim-engine biphasic-pairs v9`. No retries.
- 5g.2 (1 kOhm) not done yet; body tests 5g.3-5g.4 next. v8 (27e44ac1...ec0e) is the image to go back to.
- 5g.2 (1 kOhm) SKIPPED on PlaStim's call (no resistor to hand), as for v7; body tests 5g.3-5g.4 go ahead.
