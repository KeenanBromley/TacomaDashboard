# TacomaDashboard

TacomaDashboard turns a Raspberry Pi into a truck dashboard for a 2011 Toyota Tacoma. It uses Raspberry Pi OS desktop, a fullscreen Tkinter UI, and a root `@reboot` cron entry for startup.

## What It Does

The project is a Python/Tkinter application that:

- Connects to a Bluetooth or USB OBD-II adapter using `python-obd`.
- Displays speed, RPM, coolant temperature, engine load, intake temperature, instant MPG, average MPG, and estimated range.
- Tracks trip distance, trip time, and fuel usage between fills.
- Saves fuel state to disk so range estimates survive power cycles.
- Controls three relay outputs for accessory lights from the touchscreen UI.
- Starts in fullscreen mode and hides the cursor for a clean dash-style display.
- Runs on Raspberry Pi OS desktop and starts from a root `@reboot` cron job.
## How It Works

1. The Raspberry Pi boots into Raspberry Pi OS desktop.
2. The configured user logs in automatically.
3. A root `@reboot` cron job launches `dashboard.py` directly as root.
4. The app opens a fullscreen Tkinter window.
5. A background thread polls the OBD-II adapter about twice per second.
6. Fuel usage is estimated from MAF readings and written to `fuel_state.json` every 30 seconds.
7. Touchscreen buttons toggle the relay outputs and switch between the live view and trip stats.

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

Install the required packages and the `obd` library with `sudo`:

```bash
sudo apt-get update
sudo apt-get install -y --no-install-recommends python3 python3-pip python3-tk python3-rpi.gpio x11-xserver-utils
sudo python3 -m pip install --upgrade pip
sudo python3 -m pip install --break-system-packages obd
```

Add the startup line with the root crontab editor:

```bash
sudo crontab -e
```

Then add a line like this, replacing the repo path with your own values:

```cron
@reboot sleep 10 && DISPLAY=:0 /usr/bin/python3 /home/YOUR_USER/TacomaDashboard/dashboard.py >> /home/YOUR_USER/TacomaDashboard/dashboard.log 2>&1```

Because this line lives in root's crontab, the dashboard runs as root. The current dashboard is still a Tkinter app, so it will still need a desktop session available to display on the touchscreen.

## Running Manually

If you want to launch it yourself instead of using the boot flow:

```bash
python3 dashboard.py
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

For the cleanest startup, make sure the standard Raspberry Pi OS desktop is set to autologin for the user that will run the dashboard.

For screen tuning, you can optionally set display values in `/boot/config.txt` such as `disable_overscan=1` and the correct HDMI mode for your panel.

Boot time can usually be improved further with these options:

1. Disable unused services:

```bash
sudo systemctl disable bluetooth
sudo systemctl disable hciuart
sudo systemctl disable avahi-daemon
sudo systemctl disable triggerhappy
```

2. If this Pi is only driving the touchscreen and not doing heavy graphics work, reduce GPU memory in `/boot/firmware/config.txt` or `/boot/config.txt`:

```text
gpu_mem=64
```

3. Keep the splash screen disabled and the kernel output quiet. In `/boot/firmware/config.txt` or `/boot/config.txt`:

```text
disable_splash=1
boot_delay=0
```

And in `/boot/firmware/cmdline.txt` or `/boot/cmdline.txt`, keep the options on the same line as the existing boot arguments:

```text
quiet splash loglevel=0 logo.nologo vt.global_cursor_default=0
```

4. Make sure LightDM autologin is enabled so the login screen does not add delay:

```ini
[Seat:*]
autologin-user=YOUR_USER
autologin-user-timeout=0
```

5. If the dashboard does not need to wait as long on startup, shorten the sleep in the crontab entry gradually. Start with `sleep 10`, then try `sleep 5` if the dashboard still launches reliably.

```cron
@reboot sleep 10 && DISPLAY=:0 /usr/bin/python3 /home/YOUR_USER/TacomaDashboard/dashboard.py >> /home/YOUR_USER/TacomaDashboard/dashboard.log 2>&1
```

6. A faster SD card or a USB SSD can make a big difference on a Pi. An A1/A2-rated card is a good minimum, and SSD storage is usually the best upgrade if the hardware supports it.

## Files

- `dashboard.py` - main dashboard application.
- `requirements.txt` - Python package list for the system install.
