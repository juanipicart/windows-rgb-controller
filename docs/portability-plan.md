# Portability plan

Goal: rgbctl installs and runs on any Windows PC, not just the one it was
built on. Phases are roughly in order; each one leaves the software working,
so it's fine to stop after any of them.

## Where things stand

- Windows only (color picker, window DPI setting and startup shortcut use
  Windows APIs).
- Runs from the project folder with a Python virtual environment; no
  installer.
- Needs OpenRGB installed and running as a server for every device without a
  native driver.
- `rgbctl.toml` describes one specific PC (fan LED counts, ASUS header names,
  AIO ring).
- Other RGB software has to be stopped by hand (on the original PC: ASUS
  LightingService disabled, OpenRGB's Lighting Node detector disabled).
- Native drivers (Corsair Lighting Node Core, Cooler Master CK721 on firmware
  1.48) are only tested on one unit each. Missing hardware is already handled:
  drivers that find nothing are skipped.

## Phase 1: Stop depending on this folder

- Move the config to `%APPDATA%\rgbctl\`, next to `state.json`. A missing
  config is fine: everything gets detected.
- Make the hardware-specific config entries optional rather than the default.
  Each PC writes its own during setup (Phase 2).
- Add the first automated tests: spec normalization, effects, device keys and
  the CK721 command bytes, so later changes can't silently break the current
  setup.

## Phase 2: Setup inside the app

- OpenRGB check on first launch: if it's unreachable, explain how to install
  it and run it as a server.
- LED counter for addressable (ARGB) headers. They can't report how many LEDs
  are connected (OpenRGB shows 0), so a wizard lights LEDs one at a time and
  the user clicks "that's the last one". The count is saved to the config.
- Device order for the flowing effects (wave, color wave, comet): drag to
  reorder instead of the current fixed order.
- A Settings section in the window for the above, instead of editing TOML.

## Phase 3: Detect conflicts

- At startup, detect running RGB software that would fight rgbctl and name it:
  iCUE, Armoury Crate / LightingService, SignalRGB, MSI Center (Mystic Light),
  G Hub, Razer Synapse.
- Detect when OpenRGB also controls a device a native driver owns (as the
  Lighting Node did on the original PC) and show the fix.

## Phase 4: Native driver safety

- Run a native driver only on known USB IDs, plus the known firmware version
  for the CK721.
- Unknown model or firmware: off by default, with an explicit "enable at your
  own risk" switch. This matters most for the CK721, whose lighting writes go
  to flash memory.

## Phase 5: Packaging

- PyInstaller: one folder with `rgbctl.exe`, Python and all dependencies
  bundled.
- Inno Setup installer: Start Menu entry, start-with-Windows option, and an
  uninstaller that also removes the startup entry.
- Version number and an About box.
- Without a code-signing certificate (roughly $100-400/year), Windows
  SmartScreen warns on install ("Run anyway" works). Fine for friends,
  off-putting for strangers.

## Phase 6: Test on a second machine

- Clean Windows VM: install, first run, uninstall, and running with no RGB
  hardware at all.
- A real second PC if available: hardware detection and conflicts.

## Open decisions

1. **Audience:** just our own PCs, friends, or a public release? This sets how
   much polish Phases 2 and 5 need, and whether code signing and auto-updates
   are worth it.
2. **OpenRGB:** should users install it themselves, or should the installer
   include it? Including it is friendlier, but OpenRGB is GPL-licensed, which
   brings obligations when redistributing it. It also needs its own low-level
   driver for RAM and motherboard access.
3. **CLI in the packaged build:** keeping it is cheap, but means shipping both
   a console and a windowless executable.

Recommendation: start with Phases 1 and 2. They make the current setup cleaner
too and don't depend on the open decisions.
