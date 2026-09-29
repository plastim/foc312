# FOC-Stim firmware fork (base: diglet48/FOC-Stim v1.3.2)

Upstream: https://github.com/diglet48/FOC-Stim, tag `v1.3.2`, commit
`6e394aabe4f745ce2b67fdb22cacaea0b8cc5bcd` (2026-05-25). This is the stock firmware on the test V4.

- The first commit that adds this folder is the **pristine** import: the whole upstream tree except `.git/` and
  `3d files/`. Every later commit is ours, so `git diff <pristine-commit> -- firmware/` is the full fork diff.
- Upstream ships **no LICENSE file** (default: all rights reserved).
- **Permission to publish (2026-09-28):** PlaStim asked diglet48 (the FOC-Stim author) about open-sourcing the host
  engine and publishing this fork. diglet48's reply, verbatim: "Sounds pretty cool, feel free to upload your fork to
  github." That covers uploading the fork to GitHub; it is not a license for the upstream code (none was stated).
  Publish it as a GitHub fork of diglet48/FOC-Stim so attribution and the upstream history stay attached. The stock
  release images in `release/` are upstream binaries: leave them out of the public fork.
- Build: see `BUILD.md`. Notes on how the stock firmware works: `NOTES.md`. Bench test before any body use:
  `TEST-PLAN.md`. **Flash only with PlaStim's explicit go for that flash** (CLAUDE.md).
