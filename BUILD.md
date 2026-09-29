# Building the FOC-Stim V4 firmware (laptop)

## What was installed (2026-09-26, user-local, nothing system-wide)

| Item | Where | Version |
|---|---|---|
| PlatformIO Core (pip, in a venv) | `%USERPROFILE%\tools\pio-venv` | 6.2.0, on py 3.13 |
| PlatformIO packages (platform, framework, toolchain) | `%USERPROFILE%\tools\platformio-core` (set by `PLATFORMIO_CORE_DIR`) | ststm32 19.6.0 · framework-arduinoststm32 4.21200.0 · toolchain-gccarmnoneeabi 1.120301.0 (GCC 12.3) |
| Libraries (per project, in `.pio/libdeps`, gitignored) | fetched by `pio run` | Nanopb 0.4.91, Adafruit SSD1306 / GFX / BusIO, STM32duino LSM6DSOX |

## Build

```bash
export PLATFORMIO_CORE_DIR="$HOME/tools/platformio-core"
cd firmware
~/tools/pio-venv/Scripts/pio.exe run -e focstim_v4        # V4 with 42TL004 transformers (the test box)
# -> .pio/build/focstim_v4/firmware.hex  (the post-script build_hex.py writes the .hex from the .elf)
```

## Why two version pins were needed (our only changes to upstream's build)

- **`platform = ststm32@19.6.0`.** Upstream leaves the platform unpinned. 20.0.0 (2026-08-31) ships a newer
  STM32duino core that moved `avr/pgmspace.h` (used by `lib/hdlc/CRC16_CCITT.h`), so the untouched source no longer
  compiles. 19.6.0 was the platform current when v1.3.2 was released (2026-05-25).
- **`nanopb/Nanopb@0.4.91`**, exact instead of `^0.4.91`. 0.4.92 builds too, but differs from the release binary.

## Reproducibility check (pristine v1.3.2)

Our build of the untouched source against the release asset `focstim_v4_firmware.hex` (v1.3.2):

- Image size is identical: **149,544 bytes**. PlatformIO reports Flash 149,004 B (56.8 %) and RAM 42,272 B (32.3 %).
- The printable strings are identical, except the toolchain's own build-host paths (`build/linux-x64` in the
  release, `build/win32-x64` in ours) and a single byte of adjacent data.
- The code is therefore the release, built from source on this laptop.

## Fork build (OUTPUT_BIPHASIC_PAIRS, 2026-09-26)

- Clean build: Flash **154,416 B (58.9 %)**, RAM **48,724 B (37.2 %)**. That is +5.4 KB flash and +6.3 KB RAM
  (mostly the 120-sample pulse buffers) over pristine.
- With live routing (AXIS_BIPHASIC_A/B_ROUTE): Flash **155,352 B (59.3 %)**, RAM **48,848 B (37.3 %)**; `focstim_v4`
  and `focstim_v4_42tl001` both build, 0 warnings in our code. (`disco_b_g431b_esc1` (V1) fails on `avr/pgmspace.h`
  in the vendored HDLC lib and `native_tests` needs a host gcc: neither is touched by the fork.)
- v2 (fractional widths + phase shapes, comment `stim-engine biphasic-pairs v2`): Flash **155,808 B (59.4 %)**,
  RAM **48,904 B (37.3 %)**; `focstim_v4` and `focstim_v4_42tl001` build, 0 warnings in our code.
- v3 (width-aware model, trip report): Flash **157,592 B (60.1 %)**, RAM **49,152 B (37.5 %)**, 0 warnings in our code.
- v5 (series-RC feedforward, σ fit): Flash **158,640 B (60.5 %)**, RAM **49,352 B (37.7 %)**; `focstim_v4` and `focstim_v4_42tl001` build, 0 warnings in our code.
- v6 (peak guard): Flash **158,960 B (60.6 %)**, RAM **49,364 B (37.7 %)**; `focstim_v4` and `focstim_v4_42tl001` build, 0 warnings in our code.
- Warnings: 0 in our code. The only warning in the whole build is the framework's own `Tone.cpp` `#warning`, which
  the pristine build has too.
- Each flashed image's SHA-256 goes in the TEST-PLAN log. The hex is not committed; rebuild from the commit.

## Flashing

**Not done here, and never without PlaStim's explicit go for that flash (CLAUDE.md).** The stock procedure is
`docs/focstim-v4-flashing.md`. Keep the release `focstim_v4_firmware.hex` (v1.3.2) on hand to restore.
