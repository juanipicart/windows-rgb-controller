# rgbctl

A personal RGB lighting controller for Windows, written in Python as a
replacement for SignalRGB. It syncs effects across every RGB device in the PC
and runs in the background with a tray icon.

> **Status:** built for one specific PC. It works on other Windows machines,
> but needs manual setup; see [docs/portability-plan.md](docs/portability-plan.md)
> for the plan to make it install anywhere.

## Features

- **Effects:** static, breathing, rainbow, wave (synced or flowing across
  devices), color wave, comet, twinkle, heartbeat, fire.
- **Any color:** native Windows color picker or hex/RGB entry.
- **Speed and brightness** controls that apply smoothly to running effects.
- **Per-device on/off**, e.g. keep the mouse dark while everything else runs.
- **Control window** (left-click the tray icon), a **tray menu**, and a
  **CLI**, all kept in sync.
- **Background service** that starts at login, survives sleep/resume, and
  reconnects when devices or OpenRGB come and go.

## How it works

The engine renders effects onto a single virtual LED strip spanning every
device, which is what lets a wave flow from one device into the next. Devices
come from backends:

| Backend | Devices |
|---|---|
| Native: Corsair Lighting Node Core | fans on the Lighting Node (e.g. 6x QL120) |
| Native: Cooler Master CK721 | keyboard, using its built-in animations |
| [OpenRGB](https://openrgb.org) SDK | everything else: RAM, motherboard, ARGB headers, mice... |

Native drivers claim their devices first; OpenRGB handles the rest.

The CK721 stores lighting settings in flash and can't take live frames, so it
switches to its closest built-in animation instead of following frame by
frame. Writes are deduplicated and rate-limited to protect the flash. It
follows effect and color, but not speed.

## Requirements

- Windows 10/11
- Python 3.11+
- [OpenRGB](https://openrgb.org) running with its SDK server enabled
  (default port 6742), for any device without a native driver
- Other RGB software (iCUE, Armoury Crate, SignalRGB, MSI Center...) closed
  or disabled for the devices rgbctl controls; two programs driving the same
  device fight over it

## Install

```powershell
git clone https://github.com/juanipicart/windows-rgb-controller.git
cd windows-rgb-controller
python -m venv .venv
.venv\Scripts\pip install -e .
```

Edit `rgbctl.toml` for your hardware (see below), then:

```powershell
.venv\Scripts\rgbctl list              # check devices are found
.venv\Scripts\rgbctl start             # start the background service
.venv\Scripts\rgbctl install-startup   # start it at every login
```

## Configuration

`rgbctl.toml` in the working directory (or pass `--config`):

- `[openrgb]`: host and port of the OpenRGB SDK server.
- `[native.lighting_node]`: enable the Corsair driver, and set the LED count
  per channel.
- `[native.ck721]`: enable the keyboard driver.
- `[[zones]]`: LED counts for addressable headers. These report 0 LEDs until
  sized, so they need setting by hand. Exact counts matter for waves.

## Usage

**Tray icon:** left-click opens the control window; right-click shows a menu
with effects, color, speed, brightness, devices and quit.

**CLI** (talks to the running service when there is one):

```powershell
rgbctl status                      # what's running
rgbctl set "#ff8800"               # every device one color (name, hex or r,g,b)
rgbctl set off
rgbctl effect wave --sync          # same pattern on every device
rgbctl effect comet red --speed 2
rgbctl device off g502             # any unique part of a device name
rgbctl device list
rgbctl stop
```

Settings persist in `%APPDATA%\rgbctl\state.json`; the log is at
`%LOCALAPPDATA%\rgbctl\rgbctl.log`.

## Project layout

```
src/rgbctl/
  effects.py      effects and the serializable effect spec
  engine.py       frame loop, device layout, speed and per-device off
  session.py      opens backends; native drivers claim devices first
  daemon.py       background service and local control port (6743)
  tray.py         tray icon and menu
  window.py       control window (Tk)
  cli.py          command line
  backends/       OpenRGB, Corsair Lighting Node, Cooler Master CK721
docs/             plans and notes
```
