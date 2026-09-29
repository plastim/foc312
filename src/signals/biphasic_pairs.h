// stim-engine fork: biphasic pulses on two independent channels (OUTPUT_BIPHASIC_PAIRS). Each channel drives an
// electrode pair chosen live by its route axis (default A = electrodes 1-2, B = 3-4; any two of the four, e.g.
// A = 2-3, B = 4-1). See firmware/NOTES.md for the design and its limits.
#ifndef FOCSTIM_BIPHASIC_PAIRS
#define FOCSTIM_BIPHASIC_PAIRS

#include "bsp/bsp.h"

#if defined(BSP_ENABLE_FOURPHASE)

#include <functional>

#include "foc_error.h"
#include "vec.h"
#include "signals/output_limits.h"

class BiphasicPairs {
public:
    static constexpr int MAX_SAMPLES = 120;     // 2.4 ms at 50 kHz: 20 lead + 10 gap + 80 return (4 x 400 us) + 3 tail = 113
    static constexpr float MIN_CURRENT_FOR_UPDATE = 0.02f;   // primary amps; below this the one-way sense is noise

    // Phase shapes (v2). Widths are fractional: each 20 us sample carries the exact integral of the shape over
    // the part of the phase it covers, so charge (and flux) vary continuously with width, and the last sample of
    // a phase may be partial. Amplitude = the shape's peak current.
    // v7 adds SHAPE_TRIANGLE (linear up to the middle and down) and SHAPE_TAPER, a continuous family with a
    // flat-top fraction `param`: quarter-sine edges of (1 - param) / 2 of the width each, flat in between; param 0 is
    // exactly the half-sine (rounded), 1 is square. On the shape axis: 0..3 as the enum, 4.0..5.0 = taper 0..1.
    enum Shape { SHAPE_ROUNDED = 0, SHAPE_SQUARE = 1, SHAPE_SOFT_SQUARE = 2, SHAPE_TRIANGLE = 3, SHAPE_TAPER = 4 };
    static constexpr float SOFT_EDGE_SAMPLES = 1.0f;        // soft square: 20 us linear edges (triangle below 40 us)
    // integral from 0 to t of the unit-peak phase shape of width w (all in samples); param: SHAPE_TAPER's flat top
    static float shape_integral(int shape, float t, float w, float param = 0);

    struct PulseParams {
        float amplitude;            // primary-side (driving) current of the leading phase, amps, >= 0
        float phase_width_s;        // leading phase width
        float gap_s;                // inter-phase gap
        float asymmetry;            // return width / leading width, 1..4 (return amplitude scaled so charge is equal)
        int shape;                  // Shape (same shape for both phases)
        float shape_param;          // SHAPE_TAPER: flat-top fraction 0..1 (v7)
        int ex, ey;                 // the channel's route: electrode indices 0..3, ex != ey
        bool swap_polarity;         // false: electrode ex is cathodic in the leading phase
        float estop_current_limit;  // primary-side amps; any sample above this on any channel = e-stop
    };

    struct PulseStats {
        float current_max[4];       // |measured primary current| per channel during this pulse
        float v_drive_requested;
        float v_drive_actual;
        float volt_seconds;         // per-transformer flux of one phase (the peak flux of the pulse)
        float v_bus_min;
        float v_bus_max;
        float q_cmd_lead;           // commanded charge per phase, primary amp*seconds (lead == return, always)
        float q_cmd_return;
        float q_meas_lead;          // measured on the electrode that is cathodic in each phase (one-way sense)
        float q_meas_return;
        float amplitude_scale;      // 1 = as commanded; < 1 = limited by v_drive or transformer saturation
        int samples;
    };

    void init(std::function<void(FOCError)> emergency_stop_fn);

    // Plays one pulse for channel 0 (A) or 1 (B) on electrodes params.ex / params.ey. Blocking; the caller has
    // already called begin_route(), enabled those two outputs, waited for the driver/triac and re-zeroed the
    // current sense (same as stock).
    void play_pulse(int channel, PulseParams params, OutputLimits limits);

    // Loop resistance estimate of an electrode pair, primary-referred ohms (both electrodes + skin + output
    // stage), as last used on that pair (at its last pulse width). Telemetry only.
    float route_resistance(int ex, int ey) const { return r_last[route_index(ex, ey)]; }
    // v5 series-RC term of a pair as last used (dimensionless sigma = S * T1 / R). Telemetry only.
    float route_sigma(int ex, int ey) const { return sig_last[route_index(ex, ey)]; }

    // v5: SERIES-RC feedforward. Skin + pad act like R in series with C: the lead phase charges C and in the
    // return phase C's voltage aids the drive, so a resistive feedforward over-drives the return (v4 trip report,
    // route 21, soft square 130 us: return 1.78x the command). Each pair and width bin also keeps
    // sigma = S * T1 / R (S = elastance, T1 = lead width), and the drive per sample is
    //     v_x - v_y = R * i + S * q,   q = running integral of the commanded loop current
    // With sigma >= 0 this adds drive late in the lead phase and pulls the return-phase drive toward the lead's
    // polarity (q returns to 0 by charge balance); sigma is clamped to 0..SIGMA_MAX. The return drive can never push
    // harder in the return direction than the resistive one, but with sigma * (lead charge) > R * (return current)
    // -- e.g. asymmetry 3 -- it drives in the lead's direction early in the return phase. That is right on a real RC
    // load; on a resistive one it is bounded by the drive-voltage limit (computed from the shaped drive) and the
    // per-sample over-current e-stop, and the fit pulls sigma back to 0 there.
    // sigma is fitted pulse by pulse from the per-sample current error against q (least squares, both phases),
    // step SIGMA_GAIN, clamped to SIGMA_STEP_MAX per pulse. R still adapts from the lead phase charge (v4).
    // Designed and tuned in firmware/sim/biphasic_rc_sim.py (NOTES.md, v5 section).
    static constexpr float SIGMA_MAX = 1.5f;
    static constexpr float SIGMA_GAIN = 0.5f;
    static constexpr float SIGMA_STEP_MAX = 0.25f;

    // v6 peak guard (see model_update): after each pulse the estimate is capped so that the pulse's sensed currents
    // would stay <= (e-stop limit - GUARD_TRIP_FRAC x margin) on any channel and <= GUARD_LEAD_RATIO x the commanded
    // lead peak on the lead electrode (the latter only at >= GUARD_MIN_AMPS, above the sense noise). Lowers only.
    static constexpr float GUARD_TRIP_FRAC = 0.5f;
    static constexpr float GUARD_LEAD_RATIO = 1.5f;
    static constexpr float GUARD_MIN_AMPS = 0.03f;
    uint32_t guard_count = 0;       // pulses that were over a guard ceiling (telemetry)

    // v7 climb hold: the charge adaptation may raise an estimate only to GUARD_HOLD of the guard's ceilings (the
    // currents are linear in the estimate, so each pulse says how far it could go). v6 climbed past the ceiling and
    // was knocked back every pulse or two where a pair's sensed peak / charge ratio is high (PlaStim, EMS 1 at 130 us:
    // ~50 guard events a second, 2026-09-28; firmware/sim/lc_filter_sim.py reproduces it on skin loads). With the
    // hold the pulses sit steadily just under the ceiling. Lowers only: never above what v6 allowed.
    static constexpr float GUARD_HOLD = 0.95f;
    uint32_t guard_lead_count = 0;  // v7 diagnostics: guard events by ceiling (a pulse can count in both)
    uint32_t guard_any_count = 0;
    uint32_t hold_count = 0;        // pulses where the hold stopped the climb (no guard event)
    float diag_pk_ratio[2]{};       // per channel, smoothed: lead electrode's sensed peak / commanded lead peak
    float diag_rho[2]{};            // per channel, smoothed: sensed / commanded lead charge (what the adaptation sees)
    // v8: where the "any" ceiling was crossed (the sample holding the largest sensed current): in the lead phase, the
    // return phase, the first 3 samples (left over from before this pulse), or the gap / tail
    uint32_t any_lead_count = 0, any_return_count = 0, any_start_count = 0, any_other_count = 0;
    uint32_t reverse_seed_count = 0;    // v8: directions started from their reverse (telemetry)

    // v9 PREDICTIVE peak guard. v6/v7 correct the estimate AFTER a pulse, for the amplitude and width just played; a
    // rising amplitude, a jump to another width bin or a shape switch gets there first (box 2, PlaStim, dry pads,
    // 2026-09-29: taper 0.4 at 0.46 A tripped at 1.27x, a swinging soft-square pattern at 1.46x; the stock rule
    // allows 1 + 0.12 / a1, only 1.26 at 0.46 A, and on skin the settled sensed/command ratio is ~1.25-1.29 for every
    // shape). Per route x width bin x shape class the box learns G = the largest sensed current per volt of peak
    // drive (up at once, down by G_DECAY per pulse; a key never measured starts from the route's largest G x
    // G_SEED_MARGIN, else from G0_RATIO x the command at the model's drive, and takes its first measurement as it
    // is). Before each pulse the DRIVE (not the command, not the e-stop limit) is scaled by
    //     k = min(1, V9_HOLD x (e-stop limit - GUARD_TRIP_FRAC x margin) / (G x planned peak drive))
    // as the v6/v7 guard trims it through the estimate. On a trimmed pulse the charge adaptation may lower the
    // estimate but not raise it. Designed in sim/v9_sim.py: 0 trips where v8 tripped 1-20 times, the same charge.
    static constexpr int NSHAPE_CLASSES = 3;   // rounded (and taper <= 0.1), triangle, steep (soft, square, taper)
    static constexpr float V9_HOLD = 0.95f;
    static constexpr float G_DECAY = 0.05f;
    static constexpr float G_SEED_MARGIN = 1.5f;
    static constexpr float G0_RATIO = 1.5f;
    static int shape_class(int shape, float param) {
        if (shape == SHAPE_ROUNDED || (shape == SHAPE_TAPER && param <= 0.1f)) return 0;
        if (shape == SHAPE_TRIANGLE) return 1;
        return 2;
    }
    uint32_t trim_count = 0;           // v9: pulses the predictive guard trimmed (telemetry)
    float diag_trim[2]{1, 1};          // v9: per channel, smoothed trim factor k (1 = untouched)

    // v3: the estimate is WIDTH-AWARE. Skin is partly capacitive, so the loop "resistance" a pulse sees rises
    // with its width; one number per pair (v1/v2) learned on wide pulses over-drove narrow ones after a width
    // jump (over-current e-stop on PlaStim's leg, 2026-09-26). Each pair keeps NBINS estimates at log-spaced widths
    // and a pulse uses the log-interpolation of its two neighbours; the update is shared between them the same
    // way. A bin that was never measured starts from the nearest NARROWER measured bin of that pair (narrower =
    // lower impedance = the feedforward under-drives: softer, never harder), else from the minimum.
    // A pair idle for STALE_MS restarts at half its estimates. Call before building each pulse.
    float begin_route(int ex, int ey, float width_samples, uint32_t now_ms);
    void reset_routes();

    static constexpr int NBINS = 7;
    static constexpr float BIN_WIDTH[NBINS] = {2.f, 3.f, 4.5f, 6.5f, 9.5f, 14.f, 20.f};   // samples (40..400 us)

    static constexpr uint32_t STALE_MS = 1000;
    // v8: one model per DIRECTED route (12 and 21 are two), 0..11. v1-v7 shared one per pair, so a live polarity flip
    // (or A on 12 with B on 21) drove the new direction with the other direction's estimate: PlaStim's leg,
    // 2026-09-28, B flipped to 21 on the pair A played as 12: return phase 1.68x the command on electrode 1, trip.
    // The pair's two current senses and pads differ, so the two directions need not agree.
    static constexpr int NROUTES = 12;
    static int route_index(int ex, int ey) {
        static constexpr int8_t idx[4][4] = {{-1, 0, 1, 2}, {3, -1, 4, 5}, {6, 7, -1, 8}, {9, 10, 11, -1}};
        return idx[ex & 3][ey & 3];
    }
    // v8: a direction that is new or stale while its reverse is in use starts from the reverse's estimates times
    // REVERSE_SEED (softer), so a flip is felt at once (the adaptation climbs back within a few pulses) instead of
    // starting from the minimum. The trip above was 2 % over the limit at 1.0x the reverse.
    static constexpr float REVERSE_SEED = 0.7f;

    // rms primary current per channel since the last reset (half-wave sense corrected, like stock)
    Vec4f estimate_rms_current(float dt_seconds);
    void reset_totals();

    void interrupt_fn();

    PulseStats stats{};

    // running totals (reset by the caller)
    float total_current_squared[4]{};
    float total_current_max[4]{};
    float total_volt_seconds = 0;

    // leaky running sum of measured (lead - return) charge per channel, primary amp*seconds. Reporting only:
    // the transformers cannot pass DC, and the commanded phases are equal by construction.
    float net_charge_measured[2]{};

private:
    void model_update(int channel);

    std::function<void(FOCError)> emergency_stop_fn;
    float r_bin[NROUTES][NBINS]{};
    float sig_bin[NROUTES][NBINS]{};    // v5 series-RC term per route and width bin, 0..SIGMA_MAX
    float sig_last[NROUTES]{};
    bool bin_seen[NROUTES][NBINS]{};
    uint32_t route_last_ms[NROUTES]{};
    bool route_seen[NROUTES]{};
    float r_last[NROUTES]{};
    // the pulse being built / played: its pair, bin pair (b, b + 1) and log-interpolation fraction
    int cur_route = 0, cur_bin = 0;
    float cur_frac = 0, cur_r = 0, cur_sig = 0;
    float cur_a1 = 0;               // commanded lead peak of the pulse being played, after limits (v6 guard)
    int cur_lead_e = 0;             // electrode that is cathodic in its lead phase (v6 guard)
    float cur_t1 = 0;               // lead width of the pulse being played, seconds (v5 sigma fit)
    // v9: peak per volt per route x width bin x shape class; the key and trim of the pulse being played
    float g_peak[NROUTES][NBINS][NSHAPE_CLASSES]{};
    bool g_seen[NROUTES][NBINS][NSHAPE_CLASSES]{};
    int cur_gbin = 0, cur_class = 0;
    float cur_k = 1;                // the drive trim of this pulse (1 = untouched)
    float cur_vpk = 0;              // its peak drive as played, volts (after limits and trim)
    // over-current trip report (written by the ISR on the tripping sample)
    float trip_current[4]{};
    int trip_sample = -1;

    // precomputed pulse, played by the PWM interrupt at STIM_PWM_FREQ
    float i_cmd[MAX_SAMPLES][4];
    float v_cmd[MAX_SAMPLES][4];
    float i_meas[MAX_SAMPLES][4];
    uint8_t phase_of[MAX_SAMPLES];  // 1 = lead, 2 = return, 0 = gap / tail
    int n_samples = 0;
    int ex_ = 0, ey_ = 1;           // electrodes of the pulse being played

    volatile int isr_index = 0;
    volatile bool isr_finished = false;
    volatile bool current_limit_exceeded = false;
    float current_limit = 0;
};

#endif
#endif
