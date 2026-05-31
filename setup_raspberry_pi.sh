#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$PROJECT_DIR/.venv"
STARTUP_SCRIPT="$HOME/start_dashboard.sh"
ENV_FILE="$PROJECT_DIR/.env"
if [[ -f "$ENV_FILE" ]]; then
	set -a
	source "$ENV_FILE"
	set +a
fi

LOGIN_USER="${PI_AUTOLOGIN_USER:-${SUDO_USER:-${USER}}}"
DESKTOP_AUTOSTART_FILE="$HOME/.config/autostart/tacomadashboard.desktop"
LIGHTDM_AUTLOGIN_DIR="/etc/lightdm/lightdm.conf.d"
LIGHTDM_AUTLOGIN_FILE="$LIGHTDM_AUTLOGIN_DIR/99-tacomadashboard.conf"
BOOT_CMDLINE_FILE="/boot/firmware/cmdline.txt"
BOOT_CONFIG_FILE="/boot/firmware/config.txt"
if [[ ! -f "$BOOT_CMDLINE_FILE" ]]; then
	BOOT_CMDLINE_FILE="/boot/cmdline.txt"
fi
if [[ ! -f "$BOOT_CONFIG_FILE" ]]; then
	BOOT_CONFIG_FILE="/boot/config.txt"
fi

echo "Installing Raspberry Pi system packages..."
sudo apt-get update
sudo apt-get install -y --no-install-recommends \
	python3 \
	python3-pip \
	python3-venv \
	python3-tk \
	python3-rpi.gpio \
	x11-xserver-utils

echo "Creating Python virtual environment..."
python3 -m venv "$VENV_DIR"

echo "Installing Python dependencies..."
"$VENV_DIR/bin/pip" install --upgrade pip
"$VENV_DIR/bin/pip" install --break-system-packages -r "$PROJECT_DIR/requirements.txt"

if [[ -f "$BOOT_CMDLINE_FILE" ]]; then
	current_cmdline="$(tr -d '\n' < "$BOOT_CMDLINE_FILE")"
	for token in quiet splash loglevel=0 logo.nologo vt.global_cursor_default=0; do
		if [[ "$current_cmdline" != *"$token"* ]]; then
			current_cmdline+=" $token"
		fi
	done
	printf '%s\n' "$current_cmdline" | sudo tee "$BOOT_CMDLINE_FILE" >/dev/null
fi

if [[ -f "$BOOT_CONFIG_FILE" ]]; then
	if ! grep -qE '^disable_splash=1$' "$BOOT_CONFIG_FILE"; then
		printf '\ndisable_splash=1\n' | sudo tee -a "$BOOT_CONFIG_FILE" >/dev/null
	fi
	if ! grep -qE '^boot_delay=0$' "$BOOT_CONFIG_FILE"; then
		printf 'boot_delay=0\n' | sudo tee -a "$BOOT_CONFIG_FILE" >/dev/null
	fi
fi

if [[ -n "${PI_HOSTNAME:-}" ]]; then
	sudo hostnamectl set-hostname "$PI_HOSTNAME"
	if grep -qE '^127\.0\.1\.1[[:space:]]' /etc/hosts; then
		sudo sed -i "s/^127\.0\.1\.1[[:space:]].*/127.0.1.1\t${PI_HOSTNAME}/" /etc/hosts
	else
		printf '127.0.1.1\t%s\n' "$PI_HOSTNAME" | sudo tee -a /etc/hosts >/dev/null
	fi
fi

echo "Creating desktop launcher..."
cat > "$STARTUP_SCRIPT" <<EOF
#!/usr/bin/env bash
set -euo pipefail

xset s off
xset -dpms
xset s noblank
unset WAYLAND_DISPLAY

exec "$VENV_DIR/bin/python" "$PROJECT_DIR/dashboard.py" >> "$PROJECT_DIR/dashboard.log" 2>&1
EOF

chmod +x "$STARTUP_SCRIPT"

echo "Configuring desktop autostart..."
mkdir -p "$(dirname "$DESKTOP_AUTOSTART_FILE")"
cat > "$DESKTOP_AUTOSTART_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=TacomaDashboard
Exec=$STARTUP_SCRIPT
X-GNOME-Autostart-enabled=true
EOF

sudo mkdir -p "$LIGHTDM_AUTLOGIN_DIR"
sudo tee "$LIGHTDM_AUTLOGIN_FILE" >/dev/null <<EOF
[Seat:*]
autologin-user=$LOGIN_USER
autologin-user-timeout=0
EOF

if command -v raspi-config >/dev/null 2>&1; then
	sudo raspi-config nonint do_boot_behaviour B4 >/dev/null 2>&1 || true
fi

if [[ "${PI_DISABLE_UNUSED_SERVICES:-0}" =~ ^(1|true|yes|on)$ ]]; then
	for service_name in bluetooth hciuart avahi-daemon; do
		sudo systemctl disable "$service_name" >/dev/null 2>&1 || true
	done
fi

sudo systemctl daemon-reload

echo "Setup complete. Reboot the Pi to boot into the touchscreen dashboard from Raspberry Pi OS desktop."