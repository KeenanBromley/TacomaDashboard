#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$PROJECT_DIR/.venv"
LOGIN_USER="${SUDO_USER:-${USER}}"
BASH_PROFILE_FILE="$HOME/.bash_profile"
GETTY_OVERRIDE_DIR="/etc/systemd/system/getty@tty1.service.d"
GETTY_OVERRIDE_FILE="$GETTY_OVERRIDE_DIR/override.conf"

echo "Installing Raspberry Pi system packages..."
sudo apt-get update
sudo apt-get install -y python3 python3-pip python3-venv python3-rpi.gpio

echo "Creating Python virtual environment..."
python3 -m venv "$VENV_DIR"

echo "Installing Python dependencies..."
"$VENV_DIR/bin/pip" install --upgrade pip
"$VENV_DIR/bin/pip" install -r "$PROJECT_DIR/requirements.txt"

echo "Configuring tty1 autologin for the console dashboard..."
if ! grep -qF 'TacomaDashboard console launch' "$BASH_PROFILE_FILE" 2>/dev/null; then
	cat >> "$BASH_PROFILE_FILE" <<EOF
# TacomaDashboard console launch
if [[ -z "\${DISPLAY:-}" ]] && [[ "\${XDG_VTNR:-}" == "1" ]]; then
	exec "$VENV_DIR/bin/python" "$PROJECT_DIR/dashboard.py"
fi
EOF
fi

sudo mkdir -p "$GETTY_OVERRIDE_DIR"
sudo tee "$GETTY_OVERRIDE_FILE" >/dev/null <<EOF
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin $LOGIN_USER --noclear %I linux
EOF

sudo systemctl daemon-reload

echo "Setup complete. Reboot the Pi to start the console dashboard on tty1."