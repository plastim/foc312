// stim-engine fork: biphasic pulses on two independent electrode pairs. See biphasic_pairs.h, firmware/NOTES.md.
#include "biphasic_pairs.h"

#if defined(BSP_ENABLE_FOURPHASE)

#include <algorithm>
#include <atomic>
#include <math.h>

#include <Arduino.h>

#include "foc_utils.h"
#include "utils.h"

void BiphasicPairs::init(std::function<void(FOCError)> emergency_stop_fn)
{
    this->emergency_stop_fn = emergency_stop_fn;
    reset_routes();
    reset_totals();
}

constexpr float BiphasicPairs::BIN_WIDTH[BiphasicPairs::NBINS];

void BiphasicPairs::reset_routes()
{
    for (int r = 0; r < NROUTES; r++) {
        for (int b = 0; b < NBINS; b++) {
            r_bin[r][b] = 2 * MODEL_RESISTANCE_MIN;
            sig_bin[r][b] = 0;
            bin_seen[r][b] = false;
        }
        route_seen[r] = false;
        r_last[r] = 2 * MODEL_RESISTANCE_MIN;
        sig_last[r] = 0;
    }
    net_charge_measured[0] = net_charge_measured[1] = 0;
}

float BiphasicPairs::begin_route(int ex, int ey, float width_samples, uint32_t now_ms)
{
    const int r = route_index(ex, ey);
    const int rr = route_index(ey, ex);         // the same pair the other way round
    const float r_min = 2 * MODEL_RESISTANCE_MIN;
    const bool stale = route_seen[r] && now_ms - route_last_ms[r] > STALE_MS;
    const bool reverse_live = route_seen[rr] && now_ms - route_last_ms[rr] <= STALE_MS;
    if ((!route_seen[r] || stale) && reverse_live) {
        // v8: new or stale while the reverse is in use: start from the reverse, softer. Per bin, the lower of that
        // and (stale) half this direction's own old estimate; a bin the reverse never measured keeps this
        // direction's own rules below. sigma: the reverse's, never more than this direction's own if it had one.
        for (int b = 0; b < NBINS; b++) {
            if (!bin_seen[rr][b]) {
                if (stale) r_bin[r][b] = std::max(r_min, r_bin[r][b] * 0.5f);   // the stale rule, as without v8
                continue;
            }
            float seed = std::max(r_min, r_bin[rr][b] * REVERSE_SEED);
            float sseed = sig_bin[rr][b];
            if (stale && bin_seen[r][b]) {
                seed = std::min(seed, std::max(r_min, r_bin[r][b] * 0.5f));
                sseed = std::min(sseed, sig_bin[r][b]);
            }
            r_bin[r][b] = seed;
            sig_bin[r][b] = sseed;
            bin_seen[r][b] = true;
        }
        reverse_seed_count++;
    } else if (stale) {
        // contact may have improved (lower R) while the route was idle: restart at half the old estimates
        // (v5: sigma is kept; it only ever lowers the return drive)
        for (int b = 0; b < NBINS; b++) {
            r_bin[r][b] = std::max(r_min, r_bin[r][b] * 0.5f);
        }
    }
    route_seen[r] = true;
    route_last_ms[r] = now_ms;

    // neighbouring bins and the log-interpolation fraction between them
    float w = std::clamp(width_samples, BIN_WIDTH[0], BIN_WIDTH[NBINS - 1]);
    int b = 0;
    while (b < NBINS - 2 && w > BIN_WIDTH[b + 1]) b++;
    float frac = logf(w / BIN_WIDTH[b]) / logf(BIN_WIDTH[b + 1] / BIN_WIDTH[b]);
    frac = std::clamp(frac, 0.f, 1.f);

    // a never-measured bin starts from the nearest narrower measured bin (softer, never harder), else the minimum;
    // v5: its sigma starts from the nearest measured bin on either side (the same value, never more), else 0
    for (int j : {b, b + 1}) {
        if (bin_seen[r][j]) continue;
        float init = r_min;
        for (int k = j - 1; k >= 0; k--) {
            if (bin_seen[r][k]) { init = r_bin[r][k]; break; }
        }
        float sig_init = 0;
        for (int d = 1; d < NBINS; d++) {
            if (j - d >= 0 && bin_seen[r][j - d]) { sig_init = sig_bin[r][j - d]; break; }
            if (j + d < NBINS && bin_seen[r][j + d]) { sig_init = sig_bin[r][j + d]; break; }
        }
        r_bin[r][j] = init;
        sig_bin[r][j] = sig_init;
        bin_seen[r][j] = true;
    }

    cur_route = r;
    cur_bin = b;
    cur_frac = frac;
    cur_r = expf((1 - frac) * logf(r_bin[r][b]) + frac * logf(r_bin[r][b + 1]));
    cur_sig = std::clamp((1 - frac) * sig_bin[r][b] + frac * sig_bin[r][b + 1], 0.f, SIGMA_MAX);
    r_last[r] = cur_r;
    sig_last[r] = cur_sig;
    return cur_r;
}

void BiphasicPairs::reset_totals()
{
    for (int e = 0; e < 4; e++) {
        total_current_squared[e] = 0;
        total_current_max[e] = 0;
    }
    total_volt_seconds = 0;
}

Vec4f BiphasicPairs::estimate_rms_current(float dt_seconds)
{
    float n = std::max(1.f, dt_seconds * STIM_PWM_FREQ);
    // one-way current sense: every electrode is measured only while its current is negative, which is half
    // of its conduction in a balanced biphasic pulse -> same sqrt(2) correction as stock (CURRENT_SENSE_SCALE_HALF)
    return Vec4f(sqrtf(total_current_squared[0] / n) * _SQRT2,
                 sqrtf(total_current_squared[1] / n) * _SQRT2,
                 sqrtf(total_current_squared[2] / n) * _SQRT2,
                 sqrtf(total_current_squared[3] / n) * _SQRT2);
}

float BiphasicPairs::shape_integral(int shape, float t, float w, float param)
{
    t = std::clamp(t, 0.f, w);
    switch (shape) {
    case SHAPE_SQUARE:
        return t;
    case SHAPE_TRIANGLE: {
        float h = w * 0.5f;
        if (t <= h) return t * t / w;
        float u = w - t;
        return h - u * u / w;
    }
    case SHAPE_TAPER: {
        float r = (1 - std::clamp(param, 0.f, 1.f)) * w * 0.5f;     // each edge: a quarter sine of r samples
        if (r < 1e-4f) return t;
        const float k = 2 * r / float(M_PI);                         // charge of one edge
        if (t < r) return k * (1 - cosf(float(M_PI) * 0.5f * t / r));
        if (t <= w - r) return k + (t - r);
        float u = w - t;
        return (2 * k + (w - 2 * r)) - k * (1 - cosf(float(M_PI) * 0.5f * u / r));
    }
    case SHAPE_SOFT_SQUARE: {
        float r = std::min(SOFT_EDGE_SAMPLES, w * 0.5f);
        if (t < r) return t * t / (2 * r);
        if (t <= w - r) return r * 0.5f + (t - r);
        float u = w - t;
        return (w - r) - u * u / (2 * r);
    }
    default:    // SHAPE_ROUNDED: half-sine
        return w / float(M_PI) * (1 - cosf(float(M_PI) * t / w));
    }
}

void BiphasicPairs::play_pulse(int channel, PulseParams p, OutputLimits limits)
{
    const float fs = float(STIM_PWM_FREQ);
    if (p.ex < 0 || p.ex > 3 || p.ey < 0 || p.ey > 3 || p.ex == p.ey) {
        return;     // the caller validates routes; never drive an invalid one
    }
    ex_ = p.ex;
    ey_ = p.ey;
    const int ex = ex_;     // first electrode of the route
    const int ey = ey_;     // second electrode of the route

    // --- widths in samples, fractional (v2): 2..20 samples = 40..400 us; the return phase is w1 x asymmetry
    const int shape = (p.shape >= SHAPE_ROUNDED && p.shape <= SHAPE_TAPER) ? p.shape : SHAPE_ROUNDED;
    const float sp = shape == SHAPE_TAPER ? std::clamp(p.shape_param, 0.f, 1.f) : 0.f;
    float asym = std::clamp(p.asymmetry, 1.f, 4.f);
    float w1 = std::clamp(p.phase_width_s * fs, 2.f, 20.f);
    int ng = std::clamp(int(p.gap_s * fs + 0.5f), 0, 10);             // 0..200 us, whole samples
    float w2 = std::clamp(w1 * asym, w1, 80.f);
    int n1 = int(ceilf(w1));                                          // samples touched by each phase (a last
                                                                      // sample may carry ~0 charge: harmless)
    int n2 = int(ceilf(w2));
    const int tail = 3;                                               // zero-volt samples: the ISR reads currents 2 samples late
    if (n1 + ng + n2 + tail > MAX_SAMPLES) {                          // cannot happen (20+10+80+3); kept as a guard
        n2 = MAX_SAMPLES - n1 - ng - tail;
        w2 = float(n2);
    }

    // --- unit-peak phase charges (amp-samples); the return amplitude makes the two phases' charges exactly equal
    float sum1 = shape_integral(shape, w1, w1, sp);
    float sum2 = shape_integral(shape, w2, w2, sp);
    float a1 = std::max(0.f, p.amplitude);
    float a2 = a1 * sum1 / sum2;

    // the width-aware estimates prepared by begin_route() for this pair and width (v3 R, v5 sigma)
    const bool prepared = (cur_route == route_index(ex, ey));
    float rp = prepared ? cur_r : r_last[route_index(ex, ey)];
    float sig = prepared ? cur_sig : sig_last[route_index(ex, ey)];
    cur_t1 = w1 / fs;
    const float s_el = sig * rp / cur_t1;          // elastance, primary ohm per second (v5)

    // --- build the pulse at the requested amplitude; the limits below then scale currents and voltages together
    //     (both are linear in the amplitude). Current convention: positive = out of the device into the body
    //     through that electrode. "Cathodic" = negative. swap_polarity=false: electrode x is cathodic in the lead.
    //     Drive (v5): v_x - v_y = rp * i + s_el * q, q = running integral of i at the sample midpoint;
    //     v_x = -v_y (the neutral stays at zero).
    float sx = p.swap_polarity ? 1.f : -1.f;
    int k = 0;
    float q_run = 0;
    float v_loop_max = 0;           // max |v_x - v_y| over the pulse (drive-voltage limit)
    float vs_lead = 0;              // per-transformer volt-seconds of the lead phase (flux limit)
    auto put = [&](float ix, uint8_t phase) {
        for (int e = 0; e < 4; e++) { i_cmd[k][e] = 0; v_cmd[k][e] = 0; i_meas[k][e] = 0; }
        float q_mid = q_run + 0.5f * ix / fs;
        q_run += ix / fs;
        float v_loop = rp * ix + s_el * q_mid;
        i_cmd[k][ex] = ix;
        i_cmd[k][ey] = -ix;
        v_cmd[k][ex] = v_loop * 0.5f;
        v_cmd[k][ey] = -v_loop * 0.5f;
        v_loop_max = std::max(v_loop_max, std::abs(v_loop));
        if (phase == 1) {
            // voltage across one transformer primary: its half of the drive minus the fixed series resistance drop
            vs_lead += (v_loop * 0.5f - MODEL_FIXED_RESISTANCE * ix) / fs;
        }
        phase_of[k] = phase;
        k++;
    };
    // each sample carries the shape's exact integral over the part of the phase it covers (partial last sample)
    float f_prev = 0;
    for (int j = 0; j < n1; j++) {
        float f = shape_integral(shape, j + 1.f, w1, sp);
        put(sx * a1 * (f - f_prev), 1);
        f_prev = f;
    }
    for (int j = 0; j < ng; j++) put(0, 0);
    f_prev = 0;
    for (int j = 0; j < n2; j++) {
        float f = shape_integral(shape, j + 1.f, w2, sp);
        put(-sx * a2 * (f - f_prev), 2);
        f_prev = f;
    }
    for (int j = 0; j < tail; j++) put(0, 0);
    n_samples = k;
    stats.samples = k;

    // --- limits, from the actual shaped drive (v5): (1) drive voltage = max |v_x - v_y| over the pulse;
    //     (2) transformer flux = the lead phase's volt-seconds per transformer (the flux starts at zero and the
    //     return phase brings it back). Both scale linearly with the amplitude.
    float scale = 1;
    stats.v_drive_requested = v_loop_max;
    if (v_loop_max > limits.max_allowed_v_drive) {
        scale = std::min(scale, limits.max_allowed_v_drive / v_loop_max);
    }
    float volt_seconds = std::max(0.f, std::abs(vs_lead));
    if (volt_seconds > limits.max_allowed_v_sec) {
        scale = std::min(scale, limits.max_allowed_v_sec / volt_seconds);
    }
    if (scale < 1) {
        for (int j = 0; j < n_samples; j++) {
            for (int e = 0; e < 4; e++) { i_cmd[j][e] *= scale; v_cmd[j][e] *= scale; }
        }
    }
    a1 *= scale;
    a2 *= scale;
    cur_a1 = a1;
    cur_lead_e = p.swap_polarity ? ey : ex;
    stats.amplitude_scale = scale;
    stats.v_drive_actual = v_loop_max * scale;
    stats.volt_seconds = volt_seconds * scale;
    total_volt_seconds = std::max(total_volt_seconds, stats.volt_seconds);
    stats.q_cmd_lead = a1 * sum1 / fs;
    stats.q_cmd_return = a2 * sum2 / fs;     // == q_cmd_lead (by construction, to float precision)

    // --- play
    for (int e = 0; e < 4; e++) stats.current_max[e] = 0;
    stats.v_bus_min = 99;
    stats.v_bus_max = 0;
    current_limit = std::min<float>(p.estop_current_limit, BSP_MaximumMeasurableCurrent());
    current_limit_exceeded = false;
    trip_sample = -1;
    isr_index = 0;
    isr_finished = false;
    std::atomic_signal_fence(std::memory_order_release);

    BSP_SetPWM4Atomic(.5f, .5f, .5f, .5f);
    BSP_AttachPWMInterrupt([&]{interrupt_fn();});

    // wait for completion, with a timeout (the stock equivalent is MODEL_TIMING_ERROR)
    uint32_t t0 = micros();
    uint32_t timeout_us = uint32_t((n_samples + 50) * 1e6f / fs) + 1000;
    while (!isr_finished) {
        if (micros() - t0 > timeout_us) {
            BSP_DisableOutputs();
            BSP_AttachPWMInterrupt(nullptr);
            BSP_PrintDebugMsg("biphasic: pulse playback timeout");
            emergency_stop_fn(FOCError::MODEL_TIMING_ERROR);
            while (1) {}
        }
    }

    BSP_AttachPWMInterrupt(nullptr);
    BSP_SetPWM4Atomic(0, 0, 0, 0);

    if (current_limit_exceeded) {
        BSP_DisableOutputs();
        BSP_PrintDebugMsg("biphasic: current limit exceeded (limit %.3f A primary)", current_limit);
        // v3 trip report: what was measured, where in the pulse, and what the pulse was
        BSP_PrintDebugMsg("biphasic trip: meas a %.3f b %.3f c %.3f d %.3f A primary at sample %d of %d",
                          trip_current[0], trip_current[1], trip_current[2], trip_current[3], trip_sample, n_samples);
        BSP_PrintDebugMsg("biphasic trip: cmd peak %.3f A primary, lead %.1f us, return %.1f us, shape %d (%.2f), route %d%d",
                          a1, w1 * 1e6f / fs, w2 * 1e6f / fs, shape, sp, ex + 1, ey + 1);
        BSP_PrintDebugMsg("biphasic trip: r_est %.2f ohm, sigma %.2f (bin %d + %.2f), v_drive %.2f V, scale %.2f",
                          rp, sig, cur_bin, cur_frac, stats.v_drive_actual, stats.amplitude_scale);
        emergency_stop_fn(FOCError::OUTPUT_OVER_CURRENT);
        while (1) {}
    }

    model_update(channel);
}

void BiphasicPairs::interrupt_fn()
{
    Vec4f currents = BSP_ReadPhaseCurrents4();
    int i = isr_index;
    std::atomic_signal_fence(std::memory_order_acquire);
    if (isr_finished) {
        return;
    }
    if (i >= n_samples) {
        isr_finished = true;
        return;
    }

#ifdef STIM_DYNAMIC_VOLTAGE
    float vbus = BSP_ReadVBus();
    stats.v_bus_max = std::max(stats.v_bus_max, vbus);
    stats.v_bus_min = std::min(stats.v_bus_min, vbus);
#else
    float vbus = STIM_PSU_VOLTAGE;
#endif

#if defined(DEADTIME_COMPENSATION_ENABLE)
    auto dtcomp = [=](float voltage, float current) {
        float comp_percent;
        if (current >= DEADTIME_COMPENSATION_CURRENT_THRESHOLD) {
            comp_percent = DEADTIME_COMPENSATION_PERCENTAGE;
        } else if (current <= -DEADTIME_COMPENSATION_CURRENT_THRESHOLD) {
            comp_percent = -DEADTIME_COMPENSATION_PERCENTAGE;
        } else {
            comp_percent = DEADTIME_COMPENSATION_PERCENTAGE * current / DEADTIME_COMPENSATION_CURRENT_THRESHOLD;
        }
        return voltage + comp_percent * vbus;
    };
#else
    auto dtcomp = [](float voltage, float current) { return voltage; };
#endif
    float v[4];
    for (int e = 0; e < 4; e++) {
        v[e] = dtcomp(v_cmd[i][e], i_cmd[i][e]);
    }
    float v_max = std::max({v[0], v[1], v[2], v[3]});
    float center = vbus / 2;
    if (center + v_max > (vbus * STIM_PWM_MAX_DUTY_CYCLE)) {
        center = (vbus * STIM_PWM_MAX_DUTY_CYCLE) - v_max;
    }
    BSP_SetPWM4((v[0] + center) / vbus, (v[1] + center) / vbus, (v[2] + center) / vbus, (v[3] + center) / vbus);

    // currents lag the PWM command by 2 samples (as in stock)
    if (i >= 2) {
        int w = i - 2;
        float c[4] = {currents.a, currents.b, currents.c, currents.d};
        for (int e = 0; e < 4; e++) {
            i_meas[w][e] = c[e];
            total_current_squared[e] += c[e] * c[e];
            stats.current_max[e] = std::max(stats.current_max[e], std::abs(c[e]));
            total_current_max[e] = std::max(total_current_max[e], std::abs(c[e]));
        }
    }

    // per-sample over-current e-stop on every channel (as in stock)
    if (std::abs(currents.a) > current_limit || std::abs(currents.b) > current_limit ||
        std::abs(currents.c) > current_limit || std::abs(currents.d) > current_limit) {
        BSP_DisableOutputs();
        trip_current[0] = currents.a;
        trip_current[1] = currents.b;
        trip_current[2] = currents.c;
        trip_current[3] = currents.d;
        trip_sample = i;
        current_limit_exceeded = true;
        isr_finished = true;
        return;
    }

    std::atomic_signal_fence(std::memory_order_release);
    isr_index = i + 1;
}

void BiphasicPairs::model_update(int channel)
{
    // One-way current sense: an electrode is measured only while its commanded current is negative
    // (cathodic). In a biphasic pair pulse that is one electrode per phase, and both see the same loop current,
    // so the lead phase is measured on its cathodic electrode and the return phase on the other one.
    const int ex = ex_, ey = ey_;
    float q_meas[3] = {0, 0, 0}, q_cmd[3] = {0, 0, 0};
    for (int k = 0; k < n_samples; k++) {
        int ph = phase_of[k];
        if (ph == 0) continue;
        for (int e : {ex, ey}) {
            if (i_cmd[k][e] < -MIN_CURRENT_FOR_UPDATE) {
                q_cmd[ph] += -i_cmd[k][e];
                q_meas[ph] += -i_meas[k][e];
            }
        }
    }
    const float fs = float(STIM_PWM_FREQ);
    stats.q_meas_lead = q_meas[1] / fs;
    stats.q_meas_return = q_meas[2] / fs;

    // measured charge-balance accounting (leaky, ~1 s at 100 Hz), reporting only
    net_charge_measured[channel] = net_charge_measured[channel] * 0.99f + (stats.q_meas_lead - stats.q_meas_return);

    // pulse-to-pulse resistance update from the LEAD phase only (v4).
    // applied V = r_est * I_cmd; actual I = V / r_true  ->  rho = I / I_cmd = r_est / r_true.
    // v1-v3 used both phases. The return phase of an asymmetric pulse (long, low; ET-312 monophasic emulation) reads
    // LOW (magnetizing current and the one-way sense at a third of the amplitude), so the summed ratio said
    // "under-delivering" and the estimate ran up until the lead phase reached ~2x the command and tripped the
    // over-current e-stop (Stroke on PlaStim's leg, 2026-09-26: 0.236 A measured for 0.114 A commanded). The lead phase
    // is full amplitude, measured on its cathodic electrode, and it is the phase that trips; its reading includes
    // some magnetizing current, which errs toward a LOWER estimate (softer).
    float cmd = q_cmd[1];
    float meas = q_meas[1];
    if (cmd > MIN_CURRENT_FOR_UPDATE * 2 && meas > 0) {
        diag_rho[channel & 1] = 0.9f * diag_rho[channel & 1] + 0.1f * (meas / cmd);
        float rho = std::clamp(meas / cmd, 0.5f, 2.f);
        // half step (stable with noisy, rise-limited pulses), shared between the two width bins in log space:
        // the bin nearer this pulse's width takes most of the correction
        float corr = powf(rho, -0.5f);
        const int r = cur_route, b = cur_bin;
        r_bin[r][b] = std::clamp(r_bin[r][b] * powf(corr, 1 - cur_frac), 2 * MODEL_RESISTANCE_MIN, 2 * MODEL_RESISTANCE_MAX);
        r_bin[r][b + 1] = std::clamp(r_bin[r][b + 1] * powf(corr, cur_frac), 2 * MODEL_RESISTANCE_MIN, 2 * MODEL_RESISTANCE_MAX);
        r_last[r] = expf((1 - cur_frac) * logf(r_bin[r][b]) + cur_frac * logf(r_bin[r][b + 1]));
    }

    // v6: peak guard. The charge adaptation above matches the lead's CHARGE, but the e-stop trips on the sensed PEAK,
    // and the sense reads the bridge current (load + output-filter capacitor + magnetizing current), which can peak
    // well above the command while the charge reads right (v5 leg trip, 2026-09-26: rounded 87 us, lead 1.91x,
    // estimate 32 ohm, about twice the pair's wider bins; the lead peak had crept from 1.0x to 1.3x over ~20 s).
    // Currents scale with the estimate, so after each pulse the estimate is capped so that the same pulse would stay
    // under two ceilings: any sensed sample <= e-stop limit - GUARD_TRIP_FRAC x margin, and the lead electrode's
    // sensed peak <= GUARD_LEAD_RATIO x the commanded lead peak. It only ever LOWERS the estimate (softer), never
    // touches the e-stop, and the next pulses' charge adaptation climbs back only as far as the ceilings allow.
    {
        float any_max = 0;
        for (int e = 0; e < 4; e++) any_max = std::max(any_max, stats.current_max[e]);
        float lead_pk = 0;
        for (int k = 0; k < n_samples; k++) lead_pk = std::max(lead_pk, -i_meas[k][cur_lead_e]);
        // g_any / g_lead: how far this pulse's estimate could scale before each ceiling (currents are linear in it)
        const float any_ceiling = current_limit - GUARD_TRIP_FRAC * ESTOP_CURRENT_LIMIT_MARGIN;
        const float g_any = (any_ceiling > 0 && any_max > 0) ? any_ceiling / any_max : INFINITY;
        const float g_lead = (cur_a1 >= GUARD_MIN_AMPS && lead_pk > 0) ? GUARD_LEAD_RATIO * cur_a1 / lead_pk : INFINITY;
        if (cur_a1 >= GUARD_MIN_AMPS) {
            diag_pk_ratio[channel & 1] = 0.9f * diag_pk_ratio[channel & 1] + 0.1f * (lead_pk / cur_a1);
        }
        const float g = std::min(g_any, g_lead);
        const int r = cur_route, b = cur_bin;
        if (g < 1) {
            // v6 guard: this pulse was over a ceiling
            guard_count++;
            if (g_any < 1) {
                guard_any_count++;
                // v8: where: the sample with the largest sensed current on any electrode
                int k_max = 0;
                float c_max = -1;
                for (int k = 0; k < n_samples; k++) {
                    for (int e = 0; e < 4; e++) {
                        if (std::abs(i_meas[k][e]) > c_max) { c_max = std::abs(i_meas[k][e]); k_max = k; }
                    }
                }
                if (k_max < 3) any_start_count++;
                else if (phase_of[k_max] == 1) any_lead_count++;
                else if (phase_of[k_max] == 2) any_return_count++;
                else any_other_count++;
            }
            if (g_lead < 1) guard_lead_count++;
        }
        if (g < INFINITY) {
            // v6 capped at the ceiling after an overshoot; v7 holds at GUARD_HOLD of it whenever the adaptation above
            // has climbed (or the pair already sits) past that. The limit is on THIS width's interpolated estimate,
            // so both bins are scaled by the same factor (their ratio kept): capping each bin at the value would clip
            // the wider bin, which sits above it by design, and pull a pair on a resistor below its right value.
            // Factor < 1 only: lowers, never raises.
            const float cap = std::max(2 * MODEL_RESISTANCE_MIN, cur_r * g * GUARD_HOLD);
            const float r_after = expf((1 - cur_frac) * logf(r_bin[r][b]) + cur_frac * logf(r_bin[r][b + 1]));
            if (r_after > cap) {
                if (g >= 1) hold_count++;
                const float k = cap / r_after;
                r_bin[r][b] = std::max(2 * MODEL_RESISTANCE_MIN, r_bin[r][b] * k);
                r_bin[r][b + 1] = std::max(2 * MODEL_RESISTANCE_MIN, r_bin[r][b + 1] * k);
            }
            r_last[r] = expf((1 - cur_frac) * logf(r_bin[r][b]) + cur_frac * logf(r_bin[r][b + 1]));
        }
    }

    // v5: series-RC term. Least-squares fit of the per-sample loop-current error against the running commanded
    // charge, both phases:  (l_meas - l_cmd) ~= beta * q / T1.  With the drive v = R i + S q on a load R_t + C,
    // l = (R/R_t) i + ((S - 1/C)/R_t) q, so beta ~= sigma - sigma_true and the correction is -beta. The loop
    // current is read on the electrode that is cathodic in that sample (one-way sense).
    if (cur_t1 > 0) {
        float num = 0, den = 0, q = 0;
        int used = 0;
        for (int k = 0; k < n_samples; k++) {
            float c = i_cmd[k][ex];                 // loop current, x direction
            float q_mid = q + 0.5f * c / fs;
            q += c / fs;
            if (phase_of[k] == 0 || std::abs(c) <= MIN_CURRENT_FOR_UPDATE) continue;
            float l_meas = (c < 0) ? i_meas[k][ex] : -i_meas[k][ey];
            float qn = q_mid / cur_t1;
            num += (l_meas - c) * qn;
            den += qn * qn;
            used++;
        }
        if (used >= 4 && den > 1e-9f) {
            float step = std::clamp(-SIGMA_GAIN * num / den, -SIGMA_STEP_MAX, SIGMA_STEP_MAX);
            const int r = cur_route, b = cur_bin;
            sig_bin[r][b] = std::clamp(sig_bin[r][b] + step * (1 - cur_frac), 0.f, SIGMA_MAX);
            sig_bin[r][b + 1] = std::clamp(sig_bin[r][b + 1] + step * cur_frac, 0.f, SIGMA_MAX);
            sig_last[r] = std::clamp((1 - cur_frac) * sig_bin[r][b] + cur_frac * sig_bin[r][b + 1], 0.f, SIGMA_MAX);
        }
    }
}

#endif
