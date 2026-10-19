#!/usr/bin/env bash
# Install a systemd unit for the current WSL Ubuntu user. Does not start it.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
PYTHON_PATH="$PROJECT_DIR/.venv/bin/python"
CURRENT_USER="$(id -un)"
SERVICE_NAME="de-project-2-etl.service"

if [[ "$EUID" -eq 0 ]]; then
    echo "Run as your normal Linux user (script will use sudo when needed)." >&2
    exit 1
fi
if [[ ! -x "$PYTHON_PATH" || ! -f "$PROJECT_DIR/main.py" ]]; then
    echo "Missing .venv/bin/python or main.py in: $PROJECT_DIR" >&2
    exit 1
fi
if [[ "$PROJECT_DIR" == *' '* ]]; then
    echo "This installer expects a project path without spaces." >&2
    exit 1
fi
if [[ "${1:-}" != "--dry-run" && "$(ps -p 1 -o comm= | tr -d '[:space:]')" != "systemd" ]]; then
    echo "systemd is not PID 1. Enable it in /etc/wsl.conf and restart WSL first." >&2
    exit 1
fi

UNIT_CONTENT=$(cat <<EOF
[Unit]
Description=Tiki product ETL pipeline
Wants=network-online.target
After=network-online.target
StartLimitIntervalSec=0

[Service]
Type=simple
User=$CURRENT_USER
WorkingDirectory=$PROJECT_DIR
ExecStart=$PYTHON_PATH -u $PROJECT_DIR/main.py
Restart=on-failure
RestartSec=300
RestartPreventExitStatus=78
TimeoutStopSec=60
KillSignal=SIGTERM
Environment=PYTHONUNBUFFERED=1
UMask=0077

[Install]
WantedBy=multi-user.target
EOF
)

if [[ "${1:-}" == "--dry-run" ]]; then
    printf '%s\n' "$UNIT_CONTENT"
    exit 0
fi

# Do not start a second copy if the pipeline is already running manually.
if pgrep -u "$CURRENT_USER" -f "[p]ython.*$PROJECT_DIR/main.py" >/dev/null; then
    echo "A manual pipeline process seems active. Stop it before starting the service." >&2
fi
printf '%s\n' "$UNIT_CONTENT" | sudo tee "/etc/systemd/system/$SERVICE_NAME" >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"
echo "Installed and enabled $SERVICE_NAME. It is NOT started yet."
echo "To start: sudo systemctl start $SERVICE_NAME"
echo "To view logs: journalctl -u $SERVICE_NAME -f -o cat"
