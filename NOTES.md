# FOC-Stim V4 firmware: how it works, and the stim-engine fork

These notes cover stock v1.3.2 (`VENDORED.md`) as read from the source, then our addition: the `OUTPUT_BIPHASIC_PAIRS`
mode. File references are to `src/`. Line numbers drift, so functions are named instead.

## 1. MCU and clocks

- **MCU:** STM32G473RE, a Cortex-M4F at 170 MHz. It runs the Arduino STM32 core with direct HAL/LL register setup in
  `bsp/bsp_g473re_focstim_v4.cpp`. The ADC clocks come from the 170 MHz PLL.
- **PWM:** TIM1, centre-aligned. `STIM_PWM_FREQ` is 50 kHz, the rate of the update interrupt, so there is one output
  sample every **20 µs**. The bridges switch at twice that rate.
- **Sampling:** TIM1 TRGO2 (OC5) triggers the current-sense ADCs at a fixed offset after the PWM peak.
- **The 20 µs interrupt** (`TIM1_UP_TIM16_IRQHandler`) calls whatever the signal code attached with
  `BSP_AttachPWMInterrupt()`. That function writes the next four duty cycles and reads back four currents.
- **Current timing:** the currents it reads belong to the command written **two samples earlier**, 40 µs before.
- **Duty-cycle limits:**
  - `STIM_PWM_MINIMUM_OFF_TIME` is 3 µs, so the maximum duty cycle is 0.85. This is needed because the current sense
    must reject the PWM edge.
  - Dead-time compensation adds ±185 ns worth of duty cycle, based on the sign of the commanded current.
- **The main loop** is the Arduino `loop()` in `main_focstim_v4.cpp`. It handles comms, the safety checks, the UI and
  the sensors, then plays **one pulse, blocking**, and returns.

## 2. Output stage (V4 with Xicon 42TL004)

- **Four drive channels (A–D).** Each is a DRV8231A H-bridge used as a half-bridge. It drives an LC filter
  (220 µH, 2.2 µF, corner about 7.2 kHz) into the primary of its own **42TL004** transformer.
- **Transformer:**
  - Winding ratio 5.57 (`transformers.h`).
  - The secondary-to-primary current ratio is taken as 6.66. It is higher than the winding ratio because some current
    goes into the magnetising branch. The 6.66 is calibrated at about 220 Ω.
- **Body side:** the four secondaries go to the four electrodes, and their other ends are tied together (the star
  point, "N").
  - So the body sees four current sources whose currents must **sum to zero**. Every waveform the firmware makes
    obeys Σi = 0.
  - In voltage terms the model solves `v_k = z_k·i_k + N` with `Σv_k = 0`.
- **Primary-referred ohms:** firmware impedances (`z1..z4`) are in primary ohms.
  - Body ohms ≈ primary ohms × 5.57² ≈ ×31.
  - `MODEL_FIXED_RESISTANCE` (1.7 Ω) is the non-body part of the path: traces, Rds(on), the LC filter's resistance
    and the transformer's low-voltage winding.
  - `OutputStage` (`signals/output_stage.cpp`) models the full ladder (LC filter, magnetising branch, both windings)
    to convert between total and body impedance, and to estimate power.
- **Output D** is switched through a triac (OUT_D_EN) as well. `BSP_OutputEnable(a, b, c, d)` turns the drivers
  (and the triac) on.
  - The main loop waits **300 µs** after enabling: 250 µs is the DRV8231A wake time from its datasheet, and the triac
    turn-on was measured at 300 µs.
  - It then calls `BSP_AdjustCurrentSenseOffsets()` before every pulse. This re-zeroes the sense while no current
    flows.
- **Boost converter:** the drive rail comes from a boost converter set between 10 and 29.5 V
  (`BOOST_MINIMUM/MAXIMUM_VOLTAGE`) by `battery/boost_control.cpp`. It tracks the largest drive voltage recently
  requested, plus margins.
- **Transformer saturation limit:** `MODEL_MAXIMUM_VOLT_SECONDS` is 1100 µV·s on the primary (about 6.1 mV·s on the
  secondary). It is the peak flux the model allows.

## 3. Current sense (one-directional)

- **What the ADC sees:** the voltage across the DRV8231A's current-mirror resistor (`DRIVER_RISEN` 1 kΩ). The mirror
  only reports current in **one direction**: `CURRENT_SENSE_SCALE_HALF` in `config_g473re_focstim_v4.h`.
- **Which samples count:**
  - A channel's reading is meaningful only while its commanded current is **negative** (the firmware sign convention).
  - The model ignores samples unless `i_cmd < −0.02 A`; see `accumulate_errors()`.
  - RMS estimates are multiplied by √2 to cover the unmeasured half.
- **Range:** full scale is about 1.93 A primary (`BSP_MaximumMeasurableCurrent()`). The e-stop threshold is clipped to
  that value.
- **Consequences:**
  - Nothing is closed-loop inside a pulse; the sense serves the pulse-to-pulse model and the over-current trip.
  - The over-current trip looks at |i| on every channel at every sample. Positive currents read as about zero, so the
    trip is effectively one-sided per channel. In a balanced pulse, though, every channel spends part of the pulse
    negative, so every channel is covered in each pulse.

## 4. Pulse scheduler (stock)

- **What a pulse is:** a **burst of sine carrier**:
  - `carrier_frequency` 300–2000 Hz;
  - `pulse_width` in carrier cycles, 3–20;
  - `pulse_rise` 2–10 cycles, as a cosine-shaped envelope (a rotating complex envelope), clamped so that
    2·rise ≤ width;
  - repeated at `pulse_frequency` 1–100 Hz;
  - optional random interval jitter, and a start phase that advances about 1/60 of a turn each pulse.
- **How `loop()` runs a pulse:**
  1. Stall until the previous pulse's slot (active + pause) is over.
  2. Wait for the boost converter to be ready. If it isn't ready for more than 100 ms, trip `BOOST_UNDER_VOLTAGE`.
  3. Read every axis.
  4. Compute `driving_current = body_amps × volume × 6.66`.
  5. Project it onto the electrodes (`project_threephase_2` / `project_fourphase_2`).
  6. Enable the outputs, wait 300 µs, re-zero the sense, then `play_pulse` (blocking).
  7. Disable the outputs and call `boostControl.update()`.
  8. Send telemetry. Each of the four notifications goes out once every 50 pulses, staggered.
- **Inside `play_pulse` (`signals/fourphase_model.cpp`):**
  - Solve the phasor voltages from the model impedances.
  - Scale down for both limits:
    - **drive voltage** (`find_v_drive`, the peak-to-peak spread of the four phase voltages, against
      `boostControl.max_allowed_vdrive()`);
    - **flux** (`find_v_seconds` = (|z| − R_fixed)·|i| / (2π·f)).
  - Then **stream** the samples into a 256-entry ring buffer. The ISR plays it while the producer loop
    `accumulate_errors()` runs I/Q demodulation.
- **Why the limits are what they are.** These are our reading; upstream doesn't state its reasons.
  - **≥ 3 carrier cycles.** The model is a *phasor* model, so each electrode is one complex number at one frequency.
    It learns by I/Q-demodulating the measured current against the carrier. That only works on a burst long enough to
    have a defined frequency. In addition:
    - the rise and fall envelopes eat 2×rise cycles;
    - the one-way sense only sees half of each cycle.

    Shorter bursts spread the spectrum, and the single-frequency impedance no longer describes the load.
  - **≤ 2 kHz carrier.** At 50 kHz sampling, 2 kHz is 25 samples per cycle. Several effects all grow with frequency:
    - the 40 µs sense lag (29° at 2 kHz);
    - the LC filter's phase shift (7.2 kHz corner);
    - the PWM quantisation;
    - the magnetising and leakage behaviour that `convert_impedance()` has to extrapolate.

    Above about 2 kHz the per-frequency model stops being trustworthy.
  - **≤ 100 Hz pulse rate.** The axis is clamped there. Two things make the limit practical:
    - Each pulse costs a fixed ~0.3 ms (enable plus triac), the sense re-zero, the model update and telemetry, while
      the rest of `loop()` (display, I²C sensors, comms) runs between pulses.
    - A 3–20 cycle burst at 300–2000 Hz already lasts 1.5–67 ms.

    100 Hz is where restim's pulse-based waveforms live, and it keeps boost refill and CPU time comfortable.

## 5. Impedance model and adaptation law (stock)

- **Per-electrode model:** `z_k = |z_k|·e^{jθ_k}`, primary-referred.
  - Initialised to `MODEL_IMPEDANCE_INIT` = 5.5 + 1.3j Ω, which is a 110 Ω resistor through the 42TL004 at 1 kHz.
  - Clamped to |z| ∈ [1.4, 80] Ω and θ ∈ [−1.5, 1.5] rad.
  - When the carrier frequency changes, every z is carried to the new frequency through the output-stage ladder
    (`OutputStage::convert_impedance`).
- **Feedforward, open loop, inside the pulse:**
  - For commanded phasors `p_k` (Σp = 0): `N = −¼·Σ p_k z_k`, and `v_k = p_k z_k + N`.
  - The sample stream is `Re(v_k · e^{jωt} · envelope)`, plus dead-time compensation, around a duty centre shifted
    down so the highest phase stays under the maximum duty cycle.
- **Measurement.** For each sample where `i_cmd,k < −0.02 A`:
  - `meas_IQ_k += (cos, sin)·i_meas,k` and `cmd_IQ_k += (cos, sin)·i_cmd,k`;
  - `phase_IQ_k += (v_cmd,k + j·v_cmd,k^quadrature)·i_meas,k`, which correlates the commanded voltage with the
    measured current.
- **Magnitude update, after every pulse** (`FourphaseModel::model_update`):
  - `meas_k = |meas_IQ_k|·(4/N_samples)`, and the same for `cmd_k`.
    - The 2/N makes it a sine amplitude, and the extra ×2 is for the half-wave sense.
    - Upstream notes that these read 10–40 % low because of the rise envelope; that bias cancels because command and
      measurement are windowed identically.
  - `integrated_meas`, `integrated_cmd`: an EMA with decay 0.002 (about 500 pulses).
  - `error_ratio = Σ|integrated_cmd − integrated_meas| / max(Σ integrated_cmd, 0.05)`.
  - `step = interpolate(error_ratio, 0.01 → 0.1, 0.30 → 1.0)`. This is an adaptive gain: when the long-run error is
    small, the step is small and the model barely moves.
  - `|z_k| ← |z_k| − step · |p_k z_k| · (meas_k − cmd_k)`.
    - This is gradient descent on the current error, scaled by the phase voltage.
    - More current than commanded means the impedance was under-estimated, so it is raised.
    - After this step z is momentarily real; the angle is restored in the phase update below.
- **Phase update:**
  - `phase_IQ_k /= N`.
  - If `|p_k| > 0.02 A`: `phase_IQ_avg_k ← 0.99·phase_IQ_avg_k + 0.01·phase_IQ_k`. This EMA has about a 100-pulse
    time constant.
  - The magnitude is floored at 0.01 so the angle is defined near zero.
  - `θ_k = arg(phase_IQ_avg_k)`.
- **Clamp:** `constrain_in_bound` applies the |z| and θ limits.
- **In short:** open-loop voltage feedforward inside each pulse, with a model-reference adaptive update between pulses.
  Magnitude adapts quickly when wrong and slowly when right; phase adapts slowly (EMA).
  - Because there is no in-pulse current loop, **the first pulses after a load change carry the old model's error**.
  - Output accuracy depends on the waveform matching what the model can describe: a steady sine of known frequency.

## 6. Safety envelope (stock), and where it lives

| Mechanism | What it does | Code |
|---|---|---|
| Boot self-test | temperature, analog supply, VSYS, VBUS; boost drain, charge and current draw; sense channels A–D. Fails into an error state | `self_test()` in `main_focstim_v4.cpp` |
| Per-sample over-current e-stop | any channel with \|i\| > commanded + 0.12 A (primary, `ESTOP_CURRENT_LIMIT_MARGIN`), capped at the measurable range: outputs off, `OUTPUT_OVER_CURRENT`, halt until power-cycle | `interrupt_fn()` + `play_pulse()` in `signals/*_model.cpp`; `trigger_emergency_stop()` |
| Timing / producer e-stop | the ISR outran the producer: `MODEL_TIMING_ERROR`, halt | `FourphaseModel::play_pulse` |
| Body-current cap | the amplitude axis is clamped to `BODY_CURRENT_MAX` 0.20 A | `simple_axes.waveform_amplitude_amps` (axis max) |
| Device-volume lock | amplitude × `encoder.volume()` (the physical knob, lockable) | `loop()` |
| Comms keepalive | no `axis_move_to` for more than 4 s: stop playing ("Comms lost? Stopping."). Only `axis_move_to` resets the timer | `loop()`; `ProtobufAPI::handle_request_axis_move_to` |
| Drive-voltage limit and slow start | the pulse is scaled so the phase-voltage spread ≤ `boostControl.max_allowed_vdrive()`; that limit starts at 5 V on each start and rises by at most 0.5 V per pulse | `battery/boost_control.cpp`; `find_v_drive` |
| Transformer saturation | scale so the peak flux ≤ 1100 µV·s | `find_v_seconds`, `MODEL_MAXIMUM_VOLT_SECONDS` |
| Boost over-voltage | VBUS ≥ 31 V: e-stop | `loop()` |
| Boost under-voltage | boost not ready for more than 100 ms: e-stop | `loop()` |
| Board temperature | ≥ 60 °C: e-stop | `loop()` |
| I²C bus hang | e-stop | `loop()` |
| Outputs off between pulses | drivers and triac are disabled after every pulse | `loop()` |

## 7. Protobuf / HDLC

- **Framing:** the serial link (USB CDC; the ESP32 bridges TCP :55533 to the same stream) carries **HDLC** frames
  (`lib/hdlc`, CRC-16-CCITT).
- **Payloads** are nanopb-encoded `focstim_rpc.RpcMessage` (`proto/focstim/*.proto`): a request or response with an
  `id`, or a notification.
- **Dispatch:** `ProtobufAPI::process_incoming_messages()` → `handle_frame` → `handle_request` switches on the request
  tag:
  - firmware version, capabilities;
  - signal start/stop;
  - `axis_move_to` (value plus glide interval in ms; matched against every registered `SimpleAxis` by id);
  - timestamp set/get, device-volume lock, LSM6DSOX start/stop, Wi-Fi, debug.
- **Registering axes:** `set_simple_axis(&simple_axes, sizeof/sizeof)` treats the `simple_axes` struct as an array.
  Any axis added to the struct is automatically addressable.
- **Unknown values:**
  - An axis id the firmware doesn't know is silently ignored, though it still resets the keepalive.
  - An unknown output mode returns `ERROR_OUTPUT_NOT_SUPPORTED`.
- **Notifications** are sent from `loop()`: currents, model and skin resistance, signal stats, system stats, battery,
  device volume, button, IMU, and debug strings and teleplots.

## 8. Fork addition: `OUTPUT_BIPHASIC_PAIRS` (mode 5)

### What it does

- **Two independent channels, A and B, each on an electrode pair.** By default **A = electrodes 1–2** and
  **B = electrodes 3–4**. Each channel's pair is **routable live** (below): any two of the four electrodes, for
  example A = 2–3 and B = 4–1.
- **Each pulse is a true biphasic pulse on one pair:** a leading phase, an optional inter-phase gap, then a return
  phase of opposite sign carrying the **same charge**.
- **Within a pair the two electrodes carry equal and opposite current** (i₂ = −i₁), so each pair sums to zero on its
  own. The other pair's drivers are **not enabled** during the pulse.
- **Each pair runs on its own pulse clock.** When both are due, the more overdue one goes first. They never overlap in
  time.

Code:
- `signals/biphasic_pairs.{h,cpp}` generate and play the pulse.
- `biphasic_loop()` in `main_focstim_v4.cpp` schedules pulses, applies the safety rules and sends telemetry.
- `OUTPUT_BIPHASIC_PAIRS` and `AXIS_BIPHASIC_*` are in `proto/focstim/constants.proto`.
- The version `comment` string is `"stim-engine biphasic-pairs"`, for host feature detection. The branch stays
  `"main"` so restim still accepts the firmware.

### Axes (all are `axis_move_to`; all are read fresh at the start of every pulse)

| Axis | id | Range (default) | Meaning |
|---|---|---|---|
| `AXIS_BIPHASIC_A/B_AMPLITUDE_AMPS` | 60 / 61 | 0–0.2 A (0) | peak body current of the **leading** phase, × device volume, capped at `BODY_CURRENT_MAX` |
| `AXIS_BIPHASIC_A/B_PULSE_FREQUENCY_HZ` | 62 / 63 | 1–400 Hz (50) | pulse rate per pair |
| `AXIS_BIPHASIC_A/B_PHASE_WIDTH_US` | 64 / 65 | 40–400 µs (150) | leading-phase width, rounded to the 20 µs grid |
| `AXIS_BIPHASIC_INTERPHASE_GAP_US` | 66 | 0–200 µs (0) | gap between the phases, both pairs |
| `AXIS_BIPHASIC_A/B_POLARITY` | 67 / 68 | 0–1 (0) | < 0.5: electrode 1 (or 3) cathodic in the leading phase; ≥ 0.5: electrode 2 (or 4) |
| `AXIS_BIPHASIC_A/B_ASYMMETRY` | 69 / 70 | 1–4 (1) | return width ÷ leading width; the return amplitude drops so the charge stays equal |
| `AXIS_BIPHASIC_A/B_ROUTE` | 71 / 72 | 11–44 (12 / 34) | the channel's electrodes as a two-digit code, first then second: 12, 23, 41, 13 … |

### Live routing (which electrodes each channel drives)

- **The code** is two digits, 1-based: `23` = electrodes 2 and 3, `41` = electrodes 4 and 1. The first digit is the
  electrode that is cathodic in the leading phase when polarity < 0.5, so `41` with polarity 0 leads with 4.
  `41` with polarity 0 is the same pulse as `14` with polarity 1.
- **Switching** takes effect on that channel's next pulse, with no re-arm and no gap in the train. Send
  `axis_move_to(71, 23, interval=0)` and `axis_move_to(72, 41, interval=0)` for "A = 2–3, B = 4–1".
- **No glide, ever.** The firmware uses the axis **target**, not the interpolated value, so a MoveTo with an interval
  still switches in one step. Interpolating 12 → 41 would otherwise pass through 23, 34 and so on.
- **Invalid codes are ignored:** a digit outside 1–4, or both digits the same (`22`). The channel keeps its last
  valid route. A host should still validate before sending.
- **Shared electrodes are allowed.** A = 1–2 with B = 2–3, or both channels on the same pair, is fine: the two
  channels' pulses never overlap in time, and only the playing route's two drivers are enabled.
- **Only the two routed outputs are enabled** for each pulse (`BSP_OutputEnable` from the route). The other two
  float, exactly as the other pair did before.
- **Impedance estimate per electrode pair, not per channel.** There are six pairs (1–2, 1–3, 1–4, 2–3, 2–4, 3–4).
  Each has its own loop-resistance estimate, shared by both channels and both polarities, so switching back to a
  pair resumes where it left off.
- **Safe start on a new or idle pair.** A pair that hasn't pulsed since the signal started begins at the **lowest**
  plausible estimate, 2 × `MODEL_RESISTANCE_MIN` = 2.8 Ω. A pair idle for more than 1 s restarts at half its old
  estimate. The feedforward voltage is estimate × current, so a low estimate under-drives: the first pulses on a new
  route are **softer**, never harder. The update then climbs by at most √2 per pulse, which takes about 4–5 pulses
  from 2.8 Ω to a typical 11–18 Ω (under 0.1 s at 50 Hz). The only unsafe direction, an estimate that is too high,
  can't happen on a route change. (This mode used to start every pair at stock's 11.3 Ω; a lower-impedance load
  then took more than the commanded current for its first pulses, bounded only by the slow start and the e-stop.)
- **Telemetry:** each electrode reports half the loop resistance of the route it is on. If it is on both routes it
  reports channel A's; if it is on neither it reports 0. `bp_qnet_a/b_uC` reset when that channel's route changes.

### Leading polarity (live, for a blind lead-swap A/B)

- The polarity axis picks which electrode of the pair is cathodic (negative current, in firmware convention) in the
  leading phase.
- It is read for every pulse, so sending `axis_move_to(67, 1.0, interval=0)` makes the **next** A pulse lead with
  electrode 2. There is no re-arm and no gap in the pulse train.
- Only a threshold at 0.5 matters, so a glide (interval > 0) crosses over at its midpoint.
- A pure polarity flip changes nothing else: same amplitude, widths and charge.
- The model's resistance estimate is shared by both polarities. Current magnitude, not direction, is what it
  estimates, and one-way sensing already measures each phase on the electrode that is cathodic in that phase (see
  below). So a flip does not disturb the adaptation.
- **Which physical terminal is "cathodic"** depends on how the transformer secondaries are phased to the jacks. That
  is fixed but unverified. The bench test confirms it with a scope (`TEST-PLAN.md` step 5). Whatever the answer, the
  flip is real: it swaps the leading edge between the two electrodes. For a blind A/B only the swap matters.

### Phase asymmetry (ET-312-style unequal phases), charge-balanced at every setting

- `n1 = round(width / 20 µs)` is clamped to 2–20 samples (40–400 µs).
- `ng = round(gap / 20 µs)` is 0–10 samples.
- `n2 = round(n1 × asymmetry)` is clamped to n1–80 samples, so the return phase is at most 1.6 ms.
- Both phases are **half-sines on the 20 µs grid**: lead `a1·sin(π(k+½)/n1)`, return `a2·sin(π(k+½)/n2)`.
- **Charge balance is exact by construction, not by approximation:**
  `a2 = a1 · Σ_k sin(π(k+½)/n1) / Σ_k sin(π(k+½)/n2)`.
  - The discrete sums of the commanded current are therefore equal: `q_lead == q_return` to float precision.
  - This holds for every width, gap, asymmetry, amplitude and polarity.
  - It still holds after limiting, because both limits scale a1 and a2 by the same factor.
  - The commanded pair current is i_x = −i_y at every sample, so the pair's electrodes are balanced against each other
    too.
- **Effective asymmetry** is n2/n1, so it is quantised.
  - At 40 µs lead width the steps are 0.5 (1, 1.5, 2 …).
  - At 150 µs (n1 = 8) they are 0.125.
  - At 200 µs and above they are 0.1 or finer.
  - The return amplitude is then ≈ a1/asymmetry. The ratio is exact in charge, not in peak.
- **Transformer flux** returns to zero at the end of every pulse. The feedforward voltage is proportional to the
  commanded current, so equal charge means equal volt-seconds in each phase. (The transformers can't pass DC anyway.)

### v2: fractional widths and selectable phase shape

The version comment is **`stim-engine biphasic-pairs v2`**; hosts send the shape axes and unsnapped widths only to
v2 (a v1 box gets snapped widths and charge-matched amplitude from the host, as before).

- **Why:** v1 rounded every width to the 20 µs sample grid, so a smoothly swept width (ET-312 Waves, 50 → 120 µs)
  played as 40 / 60 / 80 / 100 / 120 µs steps: +50 / +33 / +25 % jumps in charge, felt as distinct steps (PlaStim,
  2026-09-26). The host-side stopgap held the charge continuous through the amplitude; v2 fixes it at the source.
- **Fractional widths:** width `w` is in samples, not rounded (2..20 = 40..400 µs; return `w × asymmetry`, ≤ 80).
  The phase touches `ceil(w)` samples, and **each sample carries the exact integral of the shape over the part of the
  phase it covers**, so the last sample of a phase may be partial. Charge, flux and shape all follow `w`
  continuously: across 40–400 µs in 0.1 µs steps the phase charge changes by at most 0.25 % (rounded, square) or
  0.5 % (soft square) per step.
- **Shapes** (`AXIS_BIPHASIC_A/B_SHAPE`, 73 / 74; target value, rounded, so a MoveTo never glides through shapes):

  | Value | Shape | Unit-peak phase charge (w in samples) | Notes |
  |---|---|---|---|
  | 0 | Rounded (half-sine), default | 2w/π | v1's shape, now integrated per sample instead of mid-point sampled |
  | 1 | Square | w | closest to the ET-312's drive; highest flux per peak |
  | 2 | Soft square (trapezoid) | w − r, r = min(1, w/2) | 20 µs linear edges; a triangle below 40 µs |

- **Amplitude is still the peak current.** At the same peak a square phase carries π/2 = 1.57 × the charge of a
  rounded one. The host charge-matches when the shape changes (amps × q_rounded / q_shape), so switching shape at a
  given level keeps the charge equal and changes only the shape. Clipping at the cap only ever makes a pulse weaker.
- **Charge balance is still exact by construction:** `a2 = a1 · Q(w1) / Q(w2)` with the same shape for both phases,
  and the per-sample values telescope to exactly `a·Q(w)`. Checked numerically for every width 40–400 µs (0.1 µs
  steps) × asymmetry 1 / 1.37 / 2.9 / 4 × the three shapes.
- **Flux limit** uses the phase's real charge: `r_electrode · a1 · Q(w1) / fs`. A square phase at the same peak hits
  the flux and drive limits sooner than a rounded one; charge-matched, it has the same flux.
- **What the grid still limits:** at 20 µs per sample a short "square" is coarse (50 µs square = samples 1, 1, 0.5),
  and the output LC filter and transformer round the edges further. The real current shape at short widths needs
  the scope; the charge is right either way.
- **Cost:** one `cosf` per sample for the rounded shape (a running integral), the same order as v1's `sinf`.

### v9: predictive peak guard (per route x width bin x shape class)

Comment **`stim-engine biphasic-pairs v9`** (host: same features as v7).

- **Why:** box 2, dry pads, 2026-09-29 (TEST-LOG): Intense, taper 0.4, 130 us tripped at 1.27x a 0.46 A command,
  and a swinging soft-square pattern (width 195-253 us, amplitude swinging 2.5x several times a second) at 1.46x of
  0.28 A. The stock rule trips at command + 0.12 A, so the ratio it allows shrinks as the command grows (1.26 at
  0.46 A); on skin loads the settled sensed / command ratio is ~1.25-1.29 for every shape (`sim/v9_explore.py`).
  v6/v7 correct after a pulse, for the amplitude and width just played; a rising amplitude, a jump to another width
  bin or a shape switch gets there first.
- **Model:** per directed route x width bin (nearest of the 7) x shape class (rounded incl. taper <= 0.1, triangle,
  steep = soft / square / taper), `G` = the largest sensed current on any channel per volt of peak drive, learned
  after each pulse: up at once, down by `G_DECAY` (0.05) per pulse. A key never measured starts from this
  direction's largest `G` (else its reverse's) x `G_SEED_MARGIN` (1.5), else from `G0_RATIO` (1.5) x the command at
  the model's drive, and takes its first measurement as it is (so only its first pulse or so is softer).
- **Before each pulse**, after the drive / flux limits: `k = min(1, V9_HOLD x (e-stop limit - 0.5 x margin) /
  (G x planned peak drive))`, `V9_HOLD` 0.95, and the DRIVE (`v_cmd`) is scaled by k. The command `i_cmd` and the
  e-stop limit stay as requested, exactly as the v6/v7 guard trims through the estimate: lowers only, the e-stop
  margin is untouched.
- **With it:** on a trimmed pulse the charge adaptation may lower the estimate, not raise it (it would climb back to
  undo the trim); the v6/v7 guard and hold compute from the drive actually used (`cur_r x k`); the v5 sigma fit
  compares against the trimmed command.
- **Sim** (`sim/v9_sim.py`, mirrors the firmware; `sim/tests/test_v9_sim.py`), trips after pulse 5, v8 -> v9, on
  700R+47n / 700R+100n / 500R+100n: the swing 20 / 20 / 18 -> 0 (worst 0.88-0.92 of the limit), a step 0.10 ->
  0.45 A on soft 1 -> 0, rounded <-> soft switches every 50 pulses 4 -> 0; steady high levels unchanged (0 -> 0).
  Delivered charge within 0.01-0.03 of v8 everywhere. Worth knowing: at high levels on skin loads both v8 and v9
  deliver only ~0.45-0.8 of the commanded charge (the guard holding under the stock rule); that is headroom the
  controls don't show.
- **Diagnostics:** `bp_trim` (pulses trimmed, cumulative), `bp_k_a` / `bp_k_b` (smoothed trim per channel); the trip
  report's third line ends `trim k`.

### v8: one model per direction, reverse seeding, where the "any" ceiling is crossed

Comment **`stim-engine biphasic-pairs v8`** (host: same features as v7).

- **Why:** 2026-09-28 on the tester's leg (box 2, v7), A on 12 and B on the same pads; flipping B's polarity to 21 tripped:
  `meas a -0.309 A at sample 12 of 17` (the return phase, electrode 1), `cmd peak 0.184, 130 us, soft square, route
  21`, `r_est 15.79, sigma 0.44`, limit 0.304 (2 % over). v1-v7 kept one model per PAIR, so the new direction was
  driven with the other direction's estimate; the pair's two current senses and pads differ.
- **Per direction:** 12 models (route_index 0..11, 12 and 21 separate), each with its own width bins and sigma.
- **Reverse seeding (instant feel on a flip):** a direction that is new or stale (idle > 1 s) while its reverse is in
  use starts from the reverse's estimates x `REVERSE_SEED` 0.7 (sigma: the reverse's, never more than its own old
  one; a stale own estimate x 0.5 wins if lower; bins the reverse never measured follow the stale rule). The charge
  adaptation climbs back within a few pulses. PlaStim asked for no fade on flips ("that instant feedback is important").
- **Diagnostics:** `bp_any_lead` / `bp_any_ret` / `bp_any_start` / `bp_any_other` count where the largest sensed
  sample of each "any"-ceiling guard event was (start = the first 3 samples, i.e. left over from before the pulse),
  `bp_rev_seed` counts reverse seeds. For the open question from the same session: with A and B on the same pads in
  the same direction the "any" ceiling was crossed ~17 times a second although the v7 hold kept lowering the estimate,
  so something not proportional to the pulse's own drive (another pulse's residue, or noise) is suspected.

### v7: climb hold, guard diagnostics, triangle and taper shapes

Comment **`stim-engine biphasic-pairs v7`** (host: `fork_version` 7; the new shapes are only sent to v7).

- **Why the hold:** on the M5 remote PlaStim saw the v6 guard at ~+500 per 10 s (~50 events a second, about one pulse in
  three while EMS 1 plays: 130 us, 163 Hz, bursts), barely changed by MA or by rounded -> soft square (2026-09-28).
  That is a tug of war: where a pair's sensed peak is high against its charge, the charge adaptation climbs past the
  guard's ceiling every pulse or two and is knocked back. The filter sim reproduces it on skin loads with soft square
  (guard on 33-73 % of pulses, the estimate swinging 7-16 % pulse to pulse); rounded at 130 us did not show it in the
  sim, so the sim's skin models are not the tester's skin.
- **Hold:** after each pulse, `g = min((limit - 0.5 x margin) / max sensed, 1.5 x a1 / lead peak)` as in v6 (without
  the clamp at 1). The estimate for this width is held at `GUARD_HOLD` (0.95) x g x the estimate used, whenever the
  adaptation has climbed (or the pair sits) above that: both width bins scaled by the same factor, so their ratio is
  kept (capping each bin at the value, as v6 did, clipped the wider bin, which sits above it by design; the sim
  showed a resistor settling 3 % low with that). Factor < 1 only: never above what v6 allowed.
- **Effect (sim, `tests/test_fork_lc_sim.py`):** identical to v6 wherever v6 was calm, including every resistor case;
  where v6 fought, no guard events, no swing, the estimate 2-10 % lower (steadier, slightly softer). No trips either
  way. Real sense noise is not in the sim: if single pulses still cross, `bp_guard` shows it.
- **Diagnostics** (teleplots every 50 pulses at offset 45): `bp_guard_lead` / `bp_guard_any` (guard events by
  ceiling, cumulative), `bp_hold` (pulses where the hold stopped the climb), `bp_pk_a/b` (lead electrode's sensed peak
  / commanded lead peak, smoothed), `bp_rho_a/b` (sensed / commanded lead charge, smoothed), `bp_r_a/b` (the route's
  loop estimate). `bp_guard` is unchanged. Open question they answer: is the high peak body current or the output
  filter capacitor charging (the sense is on the bridge side)? If pk stays high while rho is low, the guard holds
  back pulses on current that never reaches the body; correcting for the modelled C_F current would loosen the guard,
  so it waits for that evidence.
- **Shapes:** `SHAPE_TRIANGLE` (3) and `SHAPE_TAPER` (shape axis 4.0..5.0 = flat-top fraction 0..1: quarter-sine
  edges of (1 - f) / 2 of the width each, flat between; 4.0 is exactly the half-sine, 5.0 exactly square). The shape
  axis range is now 0..5. Host ids: 3, and 40..50 in tenths (`device/fork.py`, `shape_axis_value`, `phase_charge`
  charge-matches them like the others). An older fork clamps an unknown shape value to soft square, more charge than
  the host planned, so hosts send them only to v7 (`shape_min_fork`; the engine plays rounded on an older box).

### v6: peak guard on the estimate

Comment **`stim-engine biphasic-pairs v6`** (host: `fork_version` 6, same host features).

- **Why:** the charge adaptation matches the lead's charge, but the e-stop trips on the sensed peak. The sense is the
  DRV8231A current mirror, i.e. the bridge current before the output LC filter: load + the 2.2 uF filter capacitor's
  C dv/dt + magnetizing current. It can peak well above the command while the charge reads right. v5 leg trip
  (TEST-LOG 2026-09-26): rounded 87 us, route 21, lead 0.271 A for 0.142 A (1.91x), sigma 0, the 60-90 us estimate
  32 ohm (about twice the pair's wider bins); the lead peak had crept from 1.0x to 1.3x over ~20 s before it.
- **Guard, after every pulse:** currents scale with the estimate, so the estimate used for the pulse is scaled by
  `g = min(1, (limit - 0.5 x margin) / max sensed on any channel, 1.5 x a1 / lead-electrode sensed peak)` and both
  width bins are capped at that value (`min`, never raised). The lead ceiling applies only at a1 >= 0.03 A primary
  (sense noise). The e-stop limit and margin are unchanged; the guard keeps the drive under them.
- **Effect:** the next pulse lands at or under both ceilings; the charge adaptation climbs back only as far as they
  allow, so where the filter/magnetizing current makes the sensed peak high, the pulse delivers somewhat less charge
  (softer), never more. Teleplot `bp_guard` counts the pulses where it acted.
- **Sim** (`firmware/sim/lc_filter_sim.py`, which adds the output LC filter with the sense on the bridge side;
  `tests/test_fork_lc_sim.py`): from the trip state (every bin at 32 ohm, rounded 87 us, 0.142 A) v5 trips again on
  1-2 skin loads, v6 has 0 trips after the first pulse with the worst sensed sample 0.20-0.21 A (limit 0.262);
  settled, soft 87-250 us on skin loads trips on every pulse in v5 and never in v6; a 1 kOhm resistor is unchanged.
  The first pulse from a run-away state still over-shoots (the guard acts after a pulse); on the leg the climb was
  gradual, which the guard stops early.
- **Not explained yet:** why the 60-90 us estimate reached 32 ohm on the leg. The filter sim does not reproduce it
  (it settles near 19 ohm), and the host did not log widths/shapes/routes, so the pattern at the time is unknown.
  Counting the lead's charge over the whole pulse was tried in the sim: always softer, but it neither reproduced
  nor explained the trip, so it was not adopted.

### v5: series-RC feedforward (skin + pad as R in series with C)

Comment **`stim-engine biphasic-pairs v5`**. Host: `fork_version` 5, v2 features.

- **Why:** skin and pad behave like R in series with C. The lead phase charges C. In the return phase C's voltage
  aids the drive, so a resistive feedforward over-drives the return. v4 trip report (route 21, soft square
  130 µs): commanded 0.156 A primary, return phase 0.278 A at sample 11 of 17. Before the flip, the return peaks on
  route 12 were already ~1.7× the command.
- **Drive per sample:** `v_x − v_y = R·i + S·q`, with `q` the running integral of the commanded loop current (at
  the sample midpoint) and `v_x = −v_y`. Per pair and width bin the model keeps a dimensionless
  `σ = S·T1/R` (T1 = lead width), so `S = σ·R/T1`. Charge balance brings `q` back to 0 at the end of the pulse.
  With `σ ≥ 0` the S·q term adds drive late in the lead phase and **reduces** it throughout the return phase.
  `σ` is clamped to `0 … SIGMA_MAX` (1.5), so the return drive is never above the resistive one.
- **Adaptation, per pulse:**
  - R: from the lead phase charge, unchanged from v4.
  - σ: least-squares fit of the per-sample loop-current error against `q/T1`, over both phases. The loop current
    is read on whichever electrode is cathodic in that sample (one-way sense). For the drive `R i + S q` on a load
    `R_t + C`, the current is `(R/R_t)·i + ((S − 1/C)/R_t)·q`, so the fitted slope is ≈ σ − σ_true. The step is
    `−SIGMA_GAIN (0.5) × slope`, clamped to ±0.25 per pulse, then shared between the two width bins like R.
  - A never-measured bin starts from the σ of the nearest measured bin (same value, never more), else 0.
  - A pair idle > 1 s keeps σ; only R halves.
- **Limits from the actual shaped drive:**
  - The drive-voltage limit uses max |v_x − v_y| over the pulse, not R·a1.
  - The flux limit uses the lead phase's volt-seconds per transformer, summed from the samples:
    `Σ (v_loop/2 − MODEL_FIXED_RESISTANCE·i) / fs`.
  - Both scale linearly with amplitude, so the whole pulse (currents and voltages) is scaled together.
  - The e-stop margin is unchanged (stock +0.12 A primary).
- **Telemetry:** debug teleplots `bp_sigma_a` and `bp_sigma_b` give each channel's route σ, every 50 pulses. The
  trip report now prints σ as well.

**Design by simulation:** `firmware/sim/biphasic_rc_sim.py`, checked by `tests/test_fork_rc_sim.py`.
- **What it models:** the firmware's pulse builder and adaptation, driving the V4 output network:
  - `R_FIX` 1.7 Ω; 42TL004, N = 5.57, `R_HV` 12 Ω;
  - a magnetizing branch fitted to the open-circuit data in transformers.cpp (7 mH ∥ 40 Ω);
  - the one-way sense, and the stock e-stop rule.
- **Validation:** it reproduces the resistor bench's "return reads low" (0.77–0.89 of command on 1 kΩ; bench about
  0.9) and v4's return over-drive on skin-like loads.
- Peak / command, per phase, after settling (amplitude 0.156 A primary; lead → return):

  | Load | Pulse | v4 | v5 | σ |
  |---|---|---|---|---|
  | 1 kΩ | rounded / soft, 130–250 µs, asym 1 and 3 | unchanged | unchanged | 0.00 |
  | 500 Ω + 100 nF | soft 130 µs | 1.30 / 1.60 | 1.11 / 1.06 | 0.56 |
  | 500 Ω + 100 nF | rounded 130 µs | 1.12 / 1.24 | 0.99 / 0.86 | 0.60 |
  | 500 Ω + 220 nF | soft 130 µs | 1.15 / 1.40 | 1.04 / 1.03 | 0.37 |
  | 500 Ω + 220 nF | soft 250 µs | 1.32 / 1.64 | 1.17 / 1.23 | 0.30 |
  | 500 Ω + 470 nF | soft 250 µs | 1.13 / 1.31 | 1.06 / 1.08 | 0.17 |
  | 500 Ω + 100 nF | soft 250 µs | 1.54 / 1.76 | 1.44 / 1.50 | 0.17 |

- **Trips (stock rule) in a sequence:** width jumps 50 ↔ 250 µs, Stroke-like asym 1 ↔ 3 every 90 pulses, and soft
  square 130 ↔ 60 µs. Skin model 500 Ω + 100 nF:

  | Amplitude (A primary) | v4 trips (of 900) | v5 trips | Worst peak / limit, v4 → v5 |
  |---|---|---|---|
  | 0.156 | 0 | 0 | 0.91 → 0.67 |
  | 0.25 | 121 | 0 | 1.11 → 0.80 |
  | 0.35 | 124 | 0 | 1.22 → 0.88 |

- **Tuning:** k = 0.3–0.8 all converge in 5–13 pulses with the same end point. With 4 mA of per-sample sense
  noise, σ jitters ±0.01 and never drifts on a resistor (the magnetizing branch pulls the fit slightly negative,
  and the clamp holds σ at 0).
- **What the sim showed that the brief didn't expect:**
  - **σ from the charge ratio (ρ2/ρ1) fails.** A series C front-loads the return current (the peak goes 2×) without
    adding much net charge, so the return *charge* can even read low. The per-sample fit on `q` sees the shape.
  - **σ does not reach the body's true value, and must not.** On 500 Ω + 100 nF at 250 µs the body's σ is ≈ 4,
    and at that σ the lead phase is driven to 2.6×. The transformer's magnetizing and core-loss branch carries
    current that never charges the body's C, so the best primary-side fit is a compromise (σ ≈ 0.2–0.6). The fit
    finds it; forcing a physical σ would be wrong.
  - **Residual:** wide soft/square phases on a small C (500 Ω + 100 nF, 250 µs) keep ~1.45× on both phases (v4
    1.54 / 1.76), because a 2-parameter R + S model can't follow a τ = 50 µs load through a 250 µs flat top. The
    sensed peak is then ~1.45 × the command: at 0.156 A primary that's inside the margin, above ~0.27 A it isn't.
    Rounded pulses don't have this (1.08 / 0.78). Wide soft/square on skin remains the shape to be careful with.
  - **Asym 3 returns stay over-driven relative to their own command:** the long, low return's peak is up to
    2.2–2.3× its command, but that is ≈ 0.75 of the lead peak in absolute terms, far from the trip line, and it's
    the RC load's shape, not net charge.
- **Not modelled:** the output LC filter (220 µH / 2.2 µF, ~7 kHz), the deadtime compensation, and the
  inter-pulse relaxation (each pulse starts at rest: the body C and the magnetizing current relax through the
  secondary loop in 0.1–0.2 ms). The hardware check is TEST-PLAN 5d.

### v4: the impedance model learns from the lead phase only

Comment **`stim-engine biphasic-pairs v4`**. v1-v3 adapted on the charge of both phases. On asymmetric pulses the
long, low return phase reads low (TEST-LOG: qnet ~3x at asym 3), so the adaptation concluded it was under-delivering,
raised the estimate every pulse, and over-drove the lead phase until the over-current e-stop tripped (ET-312 Stroke,
255 / 765 us, 0.236 A measured for 0.114 A commanded, estimate 11 -> 19 ohm per electrode within a second; the v3 trip
report). v4 compares only the lead phase's measured charge with its command: full amplitude, measured on its own
cathodic electrode, and the phase that trips. Its reading includes some magnetizing current, so the estimate errs
low (softer). The return phase is still measured and reported (`bp_qnet`); it no longer steers anything.

### v3: width-aware impedance model and trip report

Comment **`stim-engine biphasic-pairs v3`** (host: `fork_version` 3, same host features as v2).

- **Why:** skin is partly capacitive, so the loop "resistance" a pulse sees rises with its width. v1/v2 kept one
  estimate per pair; learned on wider pulses, it over-drove narrower ones after a width jump. On the tester's leg
  (2026-09-26, a pattern swinging width) the current reached ~2.3x the command and the per-sample over-current
  e-stop latched. The e-stop margin is stock and unchanged.
- **Model:** per pair, `NBINS` = 7 estimates at log-spaced lead widths 40 / 60 / 90 / 130 / 190 / 280 / 400 µs. A
  pulse uses the log-interpolation of its two neighbouring bins; the pulse-to-pulse correction (the same half step
  as before) is shared between them in log space by the same fraction.
- **Safe initialisation:** a bin never measured on that pair starts from the nearest narrower measured bin (lower
  impedance, so the feedforward under-drives: softer, never harder), else the minimum (2 × MODEL_RESISTANCE_MIN). A
  pair idle > 1 s halves all its bins. Telemetry reports each pair's estimate at its last used width.
- **Trip report:** on an over-current trip the box also prints the measured current on all four channels, the
  sample where it tripped, the commanded peak, lead/return widths, shape, route, the estimate and its bin, drive
  voltage and limit scale (three `biphasic trip:` debug lines).
- **Not in v3:** open-pair detection. An unconnected pair still drives until its magnetizing current trips the
  e-stop; the host's "pads connected" guard covers it, and the trip report gathers the data to design a firmware
  detector (the open-circuit current ramps through the phase instead of following the pulse shape).

### Voltage, limits and the pulse-to-pulse model

- **Model:** a **resistive** model per electrode pair (`r_route`, six of them; see Live routing). It is the loop
  resistance in primary ohms: both electrodes, skin, and twice the fixed output resistance. Below, `r_pair` means
  the estimate of the route being played. It starts low (2.8 Ω) on each signal start.
- **Feedforward:** `v_x = −v_y = r_pair·i/2`, so the star point stays at 0.
- **Limits.** Both scale both phases:
  - **Drive voltage:** `r_pair·a1 ≤ max_vdrive`, where
    `max_vdrive = min(boostControl.max_allowed_vdrive(), time-based slow start)`.
  - **Flux:** `(r_pair/2 − 1.7 Ω)·a1·(2/π)·width ≤ 1100 µV·s`, the flux of one half-sine phase starting from zero.
    This is the same quantity as the stock limit: peak |flux|.
  - The return phase is lower and longer but has the same volt-seconds, so it never exceeds either limit.
- **Adaptation, after every pulse:**
  - Integrate the measured charge of each phase **on the electrode that is cathodic in that phase**. This is the only
    one the one-way sense can see: in phase 1 it's the leading-cathode electrode, in phase 2 the other one.
  - Only samples with a commanded current below −0.02 A count.
  - `ρ = clamp(Σq_meas / Σq_cmd, 0.5, 2)` over both phases.
  - `r_pair ← r_pair · ρ^−½`, a half step toward `r_pair/ρ`, which is where the measured current would equal the
    command.
  - Clamped to [2.8, 160] Ω (2× the stock per-electrode clamp).
  - It converges geometrically, halving the log-error every pulse, so it is within a few percent in about 5–8 pulses
    at a steady load.
  - It measures **charge**, not peak, so the delivered charge is what gets regulated. That is the physiologically
    relevant quantity for short pulses.

### Safety in this mode (stock envelope kept, one piece strengthened)

- **Kept from stock, all active:**
  - the boot self-test;
  - the keepalive (the 4 s check runs in `loop()` before `biphasic_loop()`, and only `axis_move_to` resets it);
  - temperature, boost over- and under-voltage, and I²C-hang e-stops (the under-voltage wait is reproduced in
    `biphasic_loop()` with the same 100 ms rule);
  - the amplitude axis max of 0.2 A, **× device volume, then clamped again** to `BODY_CURRENT_MAX` in code;
  - the per-sample over-current e-stop on **all four channels** (commanded peak + 0.12 A primary, capped at the
    measurable range) → `OUTPUT_OVER_CURRENT`, halt until power-cycle;
  - drive-voltage and flux limits;
  - outputs disabled after every pulse;
  - the 300 µs enable wait and the sense re-zero before every pulse.
- **Added:**
  - A **playback timeout**: if the ISR hasn't finished the pulse in (samples + 50)·20 µs + 1 ms, trip
    `MODEL_TIMING_ERROR` and halt.
  - A **time-based slow start**. Stock limits the drive-voltage rise to 0.5 V per pulse, which is ≤ 50 V/s at stock's
    ≤ 100 Hz. This mode can pulse several times faster, so the rise is also capped at 50 V/s from 5 V. It resets on
    every signal start.
- **Unchanged:** the stock modes. The changes are additive: an enum value, appended axes, a new `PlayStatus`, an early
  branch in `loop()`, and the version comment string.

### Measured charge-balance accounting (per pair)

- **Per pulse:** `stats.q_cmd_lead == stats.q_cmd_return` (commanded, exact), and `stats.q_meas_lead` /
  `q_meas_return` (measured, primary A·s).
- **Running:** `net_charge_measured[pair] ← 0.99·previous + (q_meas_lead − q_meas_return)`, a leaky sum over about
  100 pulses. It is sent every 50 pulses as the debug teleplots `bp_qnet_a_uC` and `bp_qnet_b_uC` (body-side µC).
- **What it means:**
  - It should hover near 0.
  - A steady offset means the two electrodes' sense channels disagree (gain or offset mismatch), or that part of one
    phase's current fell outside its sample window because of the LC and transformer lag. It does **not** mean DC is
    flowing; the transformers block DC.
  - It is a diagnostic, not a control input.

### Telemetry in this mode

- Currents: RMS per electrode and peaks, from the same half-wave √2 estimate as stock.
- `output_resistance`: `r_pair/2` for each electrode of a pair, with reactance 0.
- `skin_resistance`: the stock ladder evaluated at f_eq = 1/(2·width).
- `signal_stats`: the achieved pulse rate is **the sum of both pairs**, plus drive voltage and flux/boost utilisation.

### Fidelity limits (what this mode can and can't do)

1. **20 µs time grid.**
   - Widths, gaps and the return width are multiples of 20 µs.
   - The minimum phase is 40 µs (2 samples).
   - A 70 µs request becomes 80 µs (round(3.5) = 4 samples); 250 µs becomes 260 µs (13 samples). Pick multiples of
     20 µs when it matters.
2. **Rounded phases.**
   - The commanded shape is a half-sine, chosen because it has the least high-frequency content for the model and the
     filter.
   - The 7.2 kHz LC filter (time constant about 22 µs) and the transformer's leakage further round edges. A "square"
     ET-312-style phase cannot be reproduced: 40–80 µs phases come out as smooth bumps.
   - At 150 µs and above the shape is close to the command.
3. **Resistive feedforward only.**
   - There is no in-pulse current loop, and no reactance in the model.
   - Real skin is partly capacitive, so the current waveform leads the voltage. Peak current and shape differ from the
     command, but the adaptation makes **charge per pulse** match.
   - The first 5–10 pulses after arming, or after a big load change, are off by up to the model error. Amplitude
     changes are fine; the model is per pair, not per amplitude.
4. **One-way sense.** Each phase is measured on a different electrode's sensor, so any mismatch between those two
   channels shows up as apparent charge imbalance (see accounting above) and as a small bias in `r_pair`.
5. **Pulse rate.**
   - Each pulse costs 300 µs of enable time, plus the pulse itself, plus about 100–300 µs of loop and telemetry
     overhead (to be measured, `TEST-PLAN.md`).
   - A symmetric 150 µs, no-gap pulse is about 0.4 ms of playback, so about 1 ms per pulse all in. That is roughly 800
     to 1000 pulses/s **total** across both pairs.
   - 400 Hz on each pair is near the ceiling, and less with wide or asymmetric pulses. A 400 µs lead with ×4 asymmetry
     is 2.3 ms of playback.
   - When the loop can't keep up, a pair's grid restarts instead of bunching pulses. The achieved rate is reported in
     `signal_stats`.
   - Pulses are also delayed while the boost refills, and the stock 100 ms under-voltage e-stop still applies.
   - Timing jitter is on the order of the main-loop time, about 0.1–1 ms. The display's partial update runs every loop.
6. **Pairs never overlap.** A and B pulse alternately, never simultaneously. That is inherent to enabling only the
   playing pair, and it is what keeps each pair's current balanced by itself.
   - While pair A plays, the drivers for electrodes 3 and 4 are off. Their transformers still connect those electrodes
     to the secondary star point, through the open-circuit magnetising impedance, so a small leakage path remains.
     `TEST-PLAN.md` step 6 measures that crosstalk.
7. **Drive limits.** At high body impedance, the drive-voltage limit (29.5 V boost → about 25 V drive, ×5.57) and the
   flux limit for wide phases will scale the amplitude down.
   - For example, take 2 kΩ between the pair's electrodes: each transformer sees 1 kΩ, which is about 32 Ω primary,
     and r_pair ≈ 68 Ω.
     - The drive limit allows about 25 V / 68 Ω ≈ 0.37 A primary, which is **55 mA body**.
     - With a 250 µs lead, the flux limit allows 1100e-6 / (32·(2/π)·250e-6) ≈ 0.21 A primary, which is
       **32 mA body**.
     - Wide phases into dry skin will hit this.
   - `signal_stats` shows transformer utilisation, and the amplitude scale is kept in `stats.amplitude_scale`.
8. **The transformer blocks DC.** No net DC can reach the body whatever the firmware does. Charge balance here is
   about the per-pulse electrochemistry: each phase's charge is recovered by the next phase, not later.

### Host side (stim-engine)

No change is needed to use this mode. `stimengine/device/client.py` already takes raw ints:

```python
await client.signal_start(5)                       # OUTPUT_BIPHASIC_PAIRS; stock firmware answers ERROR_OUTPUT_NOT_SUPPORTED
await client.axis_move_to(60, 0.05, interval_ms=30) # pair A 50 mA leading phase (x device volume)
await client.axis_move_to(67, 1.0, interval_ms=0)   # pair A: lead with electrode 2 from the next pulse on
```

- Keep sending axis updates at least every 4 s, or the keepalive stops output. A normal streaming loop (restim-style,
  about 60 Hz) is far inside that.
- Feature-detect with `firmware_version()` → `stm32_firmware_version_2.comment == "stim-engine biphasic-pairs"`.
- Regenerating `stimengine/device/proto/*_pb2.py` from this `constants.proto` would add named enums. It isn't needed,
  and it wasn't done, because other work touches the engine now. When it is done, put it behind a feature flag and
  gate on the comment string.
