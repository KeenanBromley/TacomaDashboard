# TacomaDashboard

TacomaDashboard turns a Raspberry Pi into a truck dashboard for a 2011 Toyota Tacoma. It is designed for Raspberry Pi OS with the standard desktop so you can use the touchscreen, color interface, and fullscreen layout.

## What It Does

The project is a Python/Tkinter application that:

- Connects to a Bluetooth or USB OBD-II adapter using `python-obd`.
- Displays speed, RPM, coolant temperature, engine load, intake temperature, instant MPG, average MPG, and estimated range.
- Tracks trip distance, trip time, and fuel usage between fills.
- Saves fuel state to disk so range estimates survive power cycles.
- Controls three relay outputs for accessory lights from the touchscreen UI.
- Starts in fullscreen mode and hides the cursor for a clean dash-style display.
- Runs on Raspberry Pi OS Lite with a minimal X server for the fastest boot to dashboard time.

## How It Works

1. The Raspberry Pi boots into Raspberry Pi OS desktop.
2. The configured user logs in automatically.
3. The desktop autostart entry launches `start_dashboard.sh`.
4. `start_dashboard.sh` disables screen blanking, suppresses terminal noise, and starts `dashboard.py`.
5. The app opens a fullscreen Tkinter window.
6. A background thread polls the OBD-II adapter about twice per second.
7. Fuel usage is estimated from MAF readings and written to `fuel_state.json` every 30 seconds.
8. Touchscreen buttons toggle the relay outputs and switch between the live view and trip stats.

## Raspberry Pi Wiring

The project uses BCM GPIO numbering. The relay outputs are defined in `dashboard.py`:

| Light / Relay | BCM GPIO | Raspberry Pi Physical Pin |
| --- | --- | --- |
| Pod lights | GPIO 17 | Pin 11 |
| Amber bar | GPIO 18 | Pin 12 |
| White bar | GPIO 27 | Pin 13 |

Notes:

- The relay board is configured as active-low. GPIO `LOW` turns a relay on, and GPIO `HIGH` turns it off.
- Wire the relay module input pins to the GPIO pins above, plus 5V power and ground as required by your relay board.
- Use proper automotive fusing and relay-rated wiring for the light circuits.
- This repo currently controls the relays from the touchscreen buttons. If you want physical switch inputs later, that can be added on top of the same GPIO wiring.

## Installation

Run the setup script on a Raspberry Pi OS desktop install after cloning the repo:

```bash
chmod +x setup_raspberry_pi.sh
./setup_raspberry_pi.sh
```

The script will:

- Install the required Raspberry Pi system packages for the dashboard.
- Create a local Python virtual environment.
- Install the Python dependencies.
- Configure desktop autologin and a desktop autostart entry so the dashboard starts automatically at boot.

Before running the script, edit [`.env`](.env) to set the Pi hostname and the user that should autologin on boot.

## Running Manually

If you want to launch it yourself instead of using the boot flow:

```bash
source .venv/bin/activate
python dashboard.py
```

Controls in the dashboard:

- `1` toggles pod lights.
- `2` toggles the amber bar.
- `3` toggles the white bar.
- `t` switches between gauges and trip stats.
- `r` resets trip statistics.
- `f` marks the tank full.
- `n` toggles the visual theme.
- `q` or `Esc` exits.

## Raspberry Pi OS Notes

For the cleanest desktop startup, make sure the standard Raspberry Pi OS desktop is set to autologin for the user in [`.env`](.env).

For screen tuning, you can optionally set display values in `/boot/config.txt` such as `disable_overscan=1` and the correct HDMI mode for your panel.

The setup script also applies boot quieting with `quiet splash loglevel=0 logo.nologo vt.global_cursor_default=0`, `disable_splash=1`, and `boot_delay=0`.

If you are not using Bluetooth OBD or mDNS discovery, set `PI_DISABLE_UNUSED_SERVICES=1` in [`.env`](.env) before running the installer to disable `bluetooth`, `hciuart`, and `avahi-daemon`.

## Files

- `dashboard.py` - main dashboard application.
- `requirements.txt` - Python package list for the virtual environment.
- `setup_raspberry_pi.sh` - dependency install and startup setup script.
