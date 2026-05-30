# TacomaDashboard

TacomaDashboard turns a Raspberry Pi into an in-truck dashboard for a 2011 Toyota Tacoma. It combines live OBD-II vehicle data, a terminal dashboard, trip and fuel tracking, and relay control for auxiliary lighting.

This project is designed to run well on Raspberry Pi OS Lite. It boots straight into a console app on tty1, so boot time stays low and fewer background services run.

## What It Does

The dashboard is a Python console application that:

- Connects to a Bluetooth or USB OBD-II adapter using `python-obd`.
- Displays speed, RPM, coolant temperature, engine load, intake temperature, instant MPG, average MPG, and estimated range.
- Tracks trip distance, trip time, and fuel usage between fills.
- Saves fuel state to disk so the range estimate survives power cycles.
- Controls three relay outputs for accessory lights from the keyboard.
- Uses the full terminal screen as a dash-style display.
- Launches directly from a lightweight console login on Raspberry Pi OS Lite.

## How It Works

1. The Raspberry Pi boots into Raspberry Pi OS Lite.
2. A console autologin starts on tty1.
3. The login shell immediately launches `dashboard.py`.
4. The app takes over the terminal and renders a live dashboard.
5. A background thread polls the OBD-II adapter about twice per second.
6. Live sensor values are pushed into the terminal UI.
7. Fuel usage is estimated from MAF readings and stored in `fuel_state.json` every 30 seconds.
8. Number keys toggle the relay outputs that power the truck lights.

## Raspberry Pi Wiring

The code uses BCM GPIO numbering. The relay outputs are already defined in `dashboard.py`:

| Light / Relay | BCM GPIO | Raspberry Pi Physical Pin |
| --- | --- | --- |
| Pod lights | GPIO 17 | Pin 11 |
| Amber bar | GPIO 18 | Pin 12 |
| White bar | GPIO 27 | Pin 13 |

Notes:

- The relay board is configured as active-low. GPIO `LOW` turns a relay on, and GPIO `HIGH` turns it off.
- Wire the relay module input pins to the GPIO pins above, plus 5V power and ground as required by your relay board.
- Use proper automotive fusing and relay-rated wiring for the light circuits.
- The keyboard controls in the console app toggle the relays; this repo does not yet read separate physical switch inputs.

## Installation

Run the setup script on the Raspberry Pi OS Lite install after cloning the repo:

```bash
chmod +x setup_raspberry_pi.sh
./setup_raspberry_pi.sh
```

The script will:

- Install the required Raspberry Pi system packages for a console-only app.
- Create a local Python virtual environment.
- Install the Python dependencies.
- Configure tty1 autologin so the dashboard launches without a desktop.

## Running Manually

If you want to launch it yourself instead of using the Lite OS autostart flow:

```bash
source .venv/bin/activate
python dashboard.py
```

The app already uses the full terminal. Press `q` or `Escape` to exit while testing.

## Files

- `dashboard.py` - main dashboard application.
- `requirements.txt` - Python package list for the virtual environment.
- `setup_raspberry_pi.sh` - dependency install and autostart setup script.
