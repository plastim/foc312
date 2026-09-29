# foc312: PlaStim's fork of the FOC-Stim firmware

The FOC-Stim V4 firmware by [diglet48](https://github.com/diglet48/FOC-Stim), plus a mode for **ET-312-style
patterns**: two independent channels of true biphasic pulses, each on any pair of the four electrodes, with the
pulse shape, width, rate and polarity set live by the host. Driven by the
[PlaStim foc312 engine](https://github.com/plastim/foc312-engine) (PC) and the
[foc312 M5 remote](https://github.com/plastim/foc312-m5remote). The stock modes are unchanged: restim still works.

This firmware is also a **test step for PlaStim Sedecim**, a 16-electrode box in development. The per-pulse
current model, the guard and the pulse engine are proven here, on the FOC-Stim, before they move there.

> **Start here:** install the PC app with its
> **[installation guide](https://github.com/plastim/foc312-engine/blob/main/INSTALL.md)**. It flashes this firmware
> onto your box for you (signed releases, checked before flashing). You don't need anything from this repository.

## What this fork adds

- **Two-channel biphasic pulses** (`OUTPUT_BIPHASIC_PAIRS`): channel A and B each on any electrode pair, any
  polarity, independent rate / width / amplitude / asymmetry, switched live without a restart.
- **Pulse shapes:** rounded (half-sine), soft square, triangle, and a continuous taper from rounded to square
  (square itself is bench-only). The host keeps the charge per pulse equal across shapes.
- **An impedance model per electrode pair, per direction and per pulse width**, with a series-RC term, so the
  delivered current follows the command on real skin.
- **A peak guard with a climb hold:** after every pulse the model is capped so the next pulse stays well under the
  over-current stop; it only ever lowers the output.
- **Diagnostics:** trip reports (what was measured, where in the pulse, what was commanded) and live telemetry of the
  model and the guard.

## Safety

The stock safety envelope is kept or strengthened, never loosened: the boot self-test, the per-pulse over-current
emergency stop (latched until power-cycle), the 0.2 A body-current cap, the 4 s communications keepalive, and the
device-volume lock. The e-stop margin is never widened; the guards only lower. Every change is checked on the
simulator (`sim/`) and on the bench (`TEST-PLAN.md`, results in `TEST-LOG.md`) before it touches a body.
**This is not a medical device. You use it at your own risk.**

## Flashing

- **Easiest:** the PlaStim foc312 engine's hub (Boxes tab). It checks the signed
  [releases](https://github.com/plastim/foc312/releases) and verifies an image before flashing.
- **Or** restim's firmware updater, with the `.hex` from a release.
- **Switch any Wi-Fi client (the M5 remote) off first:** the box's ESP32 passes Wi-Fi traffic to the STM32, and a
  client reconnecting during a flash can corrupt it. A failed flash is recoverable: hold the STM32 boot button while
  switching the box on, then flash again. To go back to stock, flash diglet48's own release.

## Building

See `BUILD.md` (PlatformIO, pinned toolchain; the build is reproducible). The version comment
`stim-engine biphasic-pairs vN` is how hosts recognise this firmware.

## License and credits

The FOC-Stim hardware and firmware are diglet48's. The upstream repository ships no license file; this fork is
published with the author's permission (`VENDORED.md`). **PlaStim's changes are MIT-licensed** (`LICENSE-PLASTIM`).

Support the project: [Patreon](https://www.patreon.com/plastim) · [PlaStim store](https://plastim.net/)

---

## The original FOC-Stim README

FOC-Stim is a family of next-generation estim devices that generate current-controlled analogue waveforms.
It can be controlled with [Restim](https://github.com/diglet48/restim).

Unlike traditional devices, the FOC-Stim has no channels
but offers an any-to-any output topology.

# Version 1

![](docs/images/focstim-v1.jpg)

Version 1 has 3 outputs. It uses a three-phase motor drive development kit from ST (the [B-G431B-ESC1](https://www.st.com/en/evaluation-tools/b-g431b-esc1.html)) for control. It only support three-phase output.

It is fairly easy to build with basic soldering skills, all parts can be ordered from mouser. For more information see [docs/focstim-v1.md](docs/focstim-v1.md)

# Version 4

![](docs/images/focstim-v4-complete.jpg)

Version 4 uses a custom PCB. It improves the original by adding one extra output, a battery, and wireless comms.

See [docs/focstim-v4](docs/focstim-v4.md) for more info.
