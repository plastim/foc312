# Bench test: fork firmware on resistor loads (must pass before any body contact)

- **Scope:** the stim-engine fork of FOC-Stim v1.3.2 (`OUTPUT_BIPHASIC_PAIRS` plus the unchanged stock modes).
- **Rule:** every step passes on resistor loads before the device goes anywhere near a body. After that, PlaStim sets the
  caps, and the phase-1/2 gates in `notes/plan.md` still apply.
- **Nothing here is automated.** PlaStim runs it, or approves each step.

## 0. Before flashing

1. **PlaStim's explicit go for this flash.** It is needed per flash (CLAUDE.md).
2. **Restore image on hand:** `firmware/release/focstim_v4_firmware_v1.3.2_stock.hex`. Restore uses the same route as
   the flash.
3. **Stock baseline, recorded before flashing, on the same loads (§1).**
   - Settings: restim four-phase at 1 kHz carrier, 10 cycles width, 50 Hz, 3 amplitudes (10, 50, 100 mA).
   - Record restim's telemetry: currents, output and skin resistance, signal stats.
   - Scope one pulse per amplitude.
   - This is what §8's stock regression compares against.
4. **Build:** `firmware/BUILD.md`, env `focstim_v4`. Record the hex SHA-256 in the test log.
5. **Flash:** restim → Tools → firmware updater with that `.hex`, per `docs/focstim-v4-flashing.md`. Hold the STM32
   boot button while switching on if the updater can't enter the bootloader.
6. **After flashing:**
   - `firmware_version()` → 1.3.2, branch `main`, comment `stim-engine biphasic-pairs`.
   - `capabilities_get()` is unchanged.

## 1. Loads and instruments

| Load | Build | Use |
|---|---|---|
| R-220 | 220 Ω, non-inductive, ≥ 3 W, between electrodes 1–2 **and** a second one between 3–4 | calibration point of the stock model (current_ratio 6.66 is set near 220 Ω) |
| R-1k | 1 kΩ, ≥ 3 W, per pair | typical skin |
| R-2k2 | 2.2 kΩ per pair | high impedance: checks the limits |
| Skin-RC | 470 Ω in series with (2.2 kΩ ∥ 47 nF), per pair | capacitive skin model: checks the resistive-only feedforward |
| Stock-4 | 4 × 110 Ω star (each electrode to a common node) | stock-mode regression (matches `MODEL_IMPEDANCE_INIT`) |
| Star-1k | 4 × 510 Ω star, each electrode to a common node: every one of the six pairs sees ~1 kΩ | live routing (§5b) |
| Star-mixed | star of 330 / 510 / 750 / 1 kΩ on electrodes 1 / 2 / 3 / 4: every pair sees a different load | routing: per-pair impedance estimates (§5b) |
| Short step | a 22 Ω resistor across the pair load, switched in by a push-button | over-current e-stop test |

- **Current:** measure it through a 10 Ω 1 % non-inductive sense resistor in series with electrode 1 (and 3), on the
  body side.
- **Scope:** ≥ 20 MHz with maths (integral). Use a **differential probe**, or run the device on battery with the host
  on Wi-Fi/TCP and at most one ground clip.
  - The outputs float through the transformers, but USB to a grounded laptop and a grounded scope can make a ground
    loop through the load.
- **Open-circuit warning:** the drive is up to about 25 V × 5.57 ≈ 140 V peak on the secondaries. Treat the leads as
  live.

## 2. Driving the tests

The existing `stimengine/device/client.py` is enough (raw ints; see NOTES.md §8, "Host side"). The session is:

```
connect_and_handshake → signal_start(5) → axis_move_to(...) at ≥ 1 Hz (keepalive) → signal_stop
```

Axes:

| Axis | id |
|---|---|
| amplitude A / B | 60 / 61 |
| rate A / B | 62 / 63 |
| width A / B | 64 / 65 |
| gap | 66 |
| polarity A / B | 67 / 68 |
| asymmetry A / B | 69 / 70 |

- The device volume knob multiplies everything. Start at a low knob setting.
- Log the notifications to a file for every step: currents, output_resistance, signal_stats, and the debug teleplots
  `bp_qnet_a_uC` and `bp_qnet_b_uC`.

## 3. Boot and arm

| # | Do | Pass |
|---|---|---|
| 3.1 | Power on with R-1k attached; again with no load | self-test passes both times (no error screen) |
| 3.2 | `signal_start(5)` with all amplitudes at 0 | response OK; no output on the scope; boost idles at its minimum |
| 3.3 | `signal_start(5)` while already playing | `ERROR_ALREADY_PLAYING` |
| 3.4 | amplitude A = 0.05 A, knob at 25 % | pulses on pair A only |
| 3.5 | watch the first pulses | amplitude ramps in over ≥ 0.1 s, not instantly (slow start: 5 V + ≤ 50 V/s) |

## 4. Waveform and charge balance (R-1k, then R-220, then Skin-RC)

Settings: pair A at 50 Hz and 0.05 A body, knob 100 %, unless stated otherwise. Scope the sense resistor and integrate
each phase.

| # | Settings | Pass |
|---|---|---|
| 4.1 | width 150, gap 0, asym 1 | biphasic pulse. Lead about 160 µs, return equal and opposite. The peak is within 15 % of the command after 20 pulses (on Skin-RC: **charge** within 15 %) |
| 4.2 | the charge balance of 4.1 | \|q_lead − q_return\| ≤ 5 % of q_lead (scope integral) |
| 4.3 | width 40, 70, 100, 200, 250, 400 | widths come out at 40/80/100/200/260/400 µs (the 20 µs grid); 4.2 holds for each |
| 4.4 | gap 0, 20, 100, 200 at width 150 | a flat zero segment of the set length between the phases; 4.2 holds |
| 4.5 | asym 1, 1.5, 2, 3, 4 at width 100 | return width ≈ lead × asym; return peak ≈ lead / asym; **4.2 holds for every value** |
| 4.6 | asym 4 at width 400 (the longest pulse, 2.3 ms) | plays; 4.2 holds; no timing e-stop |
| 4.7 | DC: 60 s at 4.5's asym 4, then scope the mean of the sense trace over 1 s | \|mean current\| ≤ 1 % of peak |
| 4.8 | teleplot `bp_qnet_a_uC` during 4.1–4.6 | settles near 0; \|value\| ≤ 5 × the per-pulse charge in µC (≈ 5 % mismatch per pulse, over the ~100-pulse leaky sum). Record the value per load: it is the sense-channel mismatch |
| 4.9 | `output_resistance` telemetry | per-electrode value settles within 10 pulses; on R-1k about (1000/2)/31 + 1.7 ≈ 18 Ω; consistent across the loads |

## 5. Leading polarity (live flip)

| # | Do | Pass |
|---|---|---|
| 5.1 | polarity A = 0 | record which **physical** jack or terminal goes negative first on the sense resistor. Write it in NOTES.md §8 ("which physical terminal is cathodic") |
| 5.2 | polarity A = 1 | the other terminal leads; amplitude, widths and charge are unchanged (4.1/4.2 numbers within 5 %) |
| 5.3 | toggle polarity 0 ↔ 1 every 0.5 s, `interval_ms = 0`, for 60 s, at 100 Hz | every pulse follows the setting from the next pulse on; no missed pulses and no re-arm (continuous train, `signal_stats` rate steady); no e-stop |
| 5.4 | same with asym 3 | the asymmetric shape flips with it; 4.2 holds for both polarities |
| 5.5 | output_resistance during 5.3 | no step or oscillation at the toggles (≤ 3 %) |

## 5b. Live routing (Star-1k, then Star-mixed)

The sense resistor goes in series with the electrode under test, and moves as the route does.

| # | Do | Pass |
|---|---|---|
| 5b.1 | A 0.05 A at 50 Hz; route A through all 12 codes (12, 13, 14, 21, 23, 24, 31, 32, 34, 41, 42, 43), 5 s each, interval 0 | current flows only in the two routed electrodes (scope each: the other two carry < 1 %); 4.2 holds for each route; the first digit leads at polarity 0 |
| 5b.2 | invalid codes 11, 22, 45, 50, 9 while playing on 23 | ignored: A stays on 2–3, no e-stop, no gap |
| 5b.3 | route change with an interval: `axis_move_to(71, 41, interval=2000)` from 12 | switches to 4–1 on the next pulse; never passes through any other pair (scope electrodes 2 and 3: nothing) |
| 5b.4 | A = 23 and B = 41 at 100 / 70 Hz, 0.05 A each | two independent trains on the new pairs; no overlap |
| 5b.5 | shared electrode: A = 12, B = 23, both 100 Hz | electrode 2 carries both trains, interleaved, never overlapping; no e-stop |
| 5b.6 | both channels on the same pair: A = 34, B = 34 | two interleaved trains on 3–4; no e-stop |
| 5b.7 | Star-mixed: A alternates 12 ↔ 34 every 0.5 s for 60 s at 50 Hz, 0.1 A | `output_resistance` settles to a different value on each pair and resumes it after each switch; **the first pulse after a switch is never higher than the steady pulses** (scope peak, every switch) |
| 5b.8 | Star-mixed: switch A to a never-used pair (e.g. 24), then after 2 s idle back to 12 | first pulses on 24 are low and reach the set current within ~5 pulses; the same after the idle return to 12; none overshoot |
| 5b.9 | route toggle every 20 ms (faster than the pulse rate) for 60 s | no e-stop, no timing error, no stuck output (after stop, all four outputs off) |

## 5c. v2: shapes and fractional widths (one 1 kΩ across the pair, ≤ 20 mA)

| # | Do | Pass |
|---|---|---|
| 5c.1 | `firmware_version()` | comment `stim-engine biphasic-pairs v2` |
| 5c.2 | shape 0 / 1 / 2 at 150 µs, host charge-matched, 10 s each | no e-stop (a square's edges ring through the LC filter: this is the step that finds out), rms current equal across shapes within 10 % |
| 5c.3 | width swept 50 → 120 µs over 2 s, repeated 20 s, each shape | rms current follows the sweep smoothly: no steps in the telemetry; no e-stop |
| 5c.4 | switch shape every 0.5 s while playing | no e-stop, no gap, no timing error |
| 5c.5 | asym 3 with each shape | `bp_qnet_a` behaves as in the v1 check (same sign and size); no e-stop |

## 5d. v5: series-RC feedforward (one 1 kΩ across the pair, then the tester's leg at low level)

| # | Do | Pass |
|---|---|---|
| 5d.1 | `firmware_version()` | comment `stim-engine biphasic-pairs v5` |
| 5d.2 | 1 kΩ: the v4 sequence (shapes, width jumps, asym 3), teleplot `bp_sigma_a` | σ stays 0.00 throughout; peaks as in the v4 log (a resistor has no series C) |
| 5d.3 | Leg, route 12, soft square 130 µs, low level (the v4 trip case), 60 s; watch `bp_sigma_a` and the peaks | σ settles (0.1–1.5) within ~20 pulses; return-phase peak within ~1.2× the command (v4: ~1.7×); no e-stop |
| 5d.4 | Leg, flip 12 ↔ 21 every 2 s for 30 s at the same level | no e-stop; σ is shared by both polarities (same pair) and stays put |
| 5d.5 | Leg, Waves / Orgasm / Stroke in foc312 (fork output), low level, a few minutes each | no e-stop; if one trips, the trip report (three `biphasic trip:` lines, now with σ) goes in TEST-LOG |

## 5e. v6: peak guard (one 1 kΩ across the pair, then the tester's leg)

| # | Do | Pass |
|---|---|---|
| 5e.1 | `firmware_version()` | comment `stim-engine biphasic-pairs v6` |
| 5e.2 | 1 kΩ: the v4/v5 sequence, teleplot `bp_guard` | no faults; peaks as in the v5 log; `bp_guard` stays low (the resistor peaks are under both ceilings) |
| 5e.3 | Leg, the v5 trip pattern (whatever foc312 was playing; narrow rounded widths, 12 <-> 21), low level, a few minutes | no e-stop; lead peak <= ~1.5x the command; `bp_guard` counts up where it acts |
| 5e.4 | Leg, 5d.3-5d.5 again | as 5d |

## 5f. v7: climb hold, diagnostics, triangle and taper (one 1 kΩ across A-B, then the tester's body)

| # | Do | Pass |
|---|---|---|
| 5f.1 | `firmware_version()` | comment `stim-engine biphasic-pairs v7` |
| 5f.2 | 1 kΩ: `py -3.13 firmware/bench/bench_shapes_widths.py COMx 7` (the v6 sequence, then triangle, taper 0/50/100 % held, jumped, swept, asym 3, and a toggle through every shape) | no faults; peaks in the v5/v6 range for the old shapes; the new shapes' peaks in the same range (charge-matched); `bp_guard` ~0 and `bp_hold` ~0 (a resistor sits under both ceilings); `bp_pk_a` ~1.0-1.3, `bp_rho_a` ~1.0; `bp_r_a` as v6 |
| 5f.3 | Body, EMS 1 at the level of 2026-09-28 (rounded, then soft square) | no e-stop; the remote's guard reading ~+0/10 s after the first seconds; note `bp_pk_a`, `bp_rho_a`, `bp_hold` |
| 5f.4 | Body, triangle and taper 50 % at low level | no e-stop; same strength on a switch (charge-matched); how they feel |

## 6. Two pairs, rate and crosstalk (R-1k on both pairs)

| # | Do | Pass |
|---|---|---|
| 6.1 | A 100 Hz, B 70 Hz, both 0.05 A | two independent pulse trains; each rate within 1 % (scope over 10 s); pulses never overlap |
| 6.2 | A 400 Hz, B 400 Hz, width 150, asym 1 | record the achieved total rate (`signal_stats`) and the per-pulse loop overhead (scope: interval minus pulse length). Record the ceiling in NOTES.md §8 |
| 6.3 | A 400 Hz at width 400, asym 4 | the rate falls back gracefully (grid restart, no bursts); no e-stop |
| 6.4 | A only at 0.1 A; scope across pair B's load | crosstalk on B ≤ 2 % of A's current |
| 6.5 | different widths, polarities and asymmetries on A and B at the same time | each pair has its own shape; 4.2 holds for each |

## 7. Safety envelope

| # | Do | Pass |
|---|---|---|
| 7.1 | **Keepalive:** stop sending axis updates while playing | output stops 4–5 s after the last `axis_move_to`; device shows idle |
| 7.2 | **Device volume:** turn the knob 100 % → 50 % → 0 | the output scales proportionally, and stops at 0 |
| 7.3 | **Volume lock:** lock via `lock_device_volume(true)`, then turn the knob | behaves as stock (locked value holds; the quick-turn unlock resets volume) |
| 7.4 | **Body cap:** amplitude axis = 0.5 | clamped: peak ≤ 0.20 A × knob (scope), no more |
| 7.5 | **Drive / flux limit:** R-2k2, amplitude 0.2, widths 100 then 400 | output limited to about the NOTES §8 numbers (drive about 25 V/r_pair; flux lower at 400 µs); `signal_stats` utilisation near 1; no e-stop; shape intact |
| 7.6 | **Open circuit:** R-1k, 0.05 A, disconnect the load mid-run, then reconnect | While open: no e-stop; `r_pair` climbs to its clamp; the secondary voltage stays within the drive limit (≤ about 140 V peak). On reconnect, **either** a clean `OUTPUT_OVER_CURRENT` trip (the model still expects an open circuit, so the first pulse overshoots; stock behaves the same way) **or** recovery within about 10 pulses. Record which, and run the same test in stock four-phase for comparison |
| 7.7 | **Over-current e-stop:** R-1k, 0.1 A, press the 22 Ω short-step button | `OUTPUT_OVER_CURRENT` e-stop within that pulse. Outputs off, error screen, no further pulses until power-cycle. **Repeat for pair B** |
| 7.8 | **Over-current, low amplitude:** same at 0.01 A | either no trip (the current stays within command + 0.12 A primary) or a clean trip; no damage either way. Record which |
| 7.9 | **Signal stop:** `signal_stop()` mid-train | output stops immediately; boost off |
| 7.10 | **Mode switching:** biphasic → stop → four-phase (restim) → stop → biphasic | each start works; no stale state (first biphasic pulses ramp again per 3.5) |
| 7.11 | **Thermal:** 10 min, A and B at 400 Hz, width 150, 0.1 A on R-220 | board temperature < 50 °C (the e-stop is at 60); no e-stop; boost stable |

Not bench-testable, so reviewed in code instead:
- the playback-timeout `MODEL_TIMING_ERROR` (`BiphasicPairs::play_pulse`);
- the boost under- and over-voltage e-stops (the stock code paths, reused unchanged).

## 8. Stock modes unchanged (Stock-4 load, restim)

| # | Do | Pass |
|---|---|---|
| 8.1 | Repeat §0.3's baseline (four-phase, then three-phase) | telemetry and scope within 3 % of the stock-firmware baseline; same `output_resistance` convergence |
| 8.2 | 7.1, 7.2 and 7.7 in four-phase | same behaviour as stock |

## 9. Sign-off

- Record the log files, scope captures, hex SHA-256 and any §5.1 / §6.2 values in the KB (`project:stim-engine`).
- Any fail: flash the stock hex back, and fix before any retest.
- **All pass:** PlaStim sets the caps (`config/engine.toml`). Body use then follows `notes/plan.md`: slow-start on arm,
  starting well below the caps.
