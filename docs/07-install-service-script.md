# 07 — `scripts/install_service.sh` explained

**Exact filename:** `scripts/install_service.sh` (**singular** `service`), not `install_services.sh`.

This script **generates a systemd unit file**, copies it under `/etc/systemd/system/`, reloads systemd and **enables** the unit for startup. It does **not** automatically start the pipeline.

## A. How to inspect it

```bash
cat scripts/install_service.sh
bash scripts/install_service.sh --dry-run
```

The dry-run prints the unit without calling `sudo` or changing systemd.

## B. Script sections explained

| Code / section | What it does |
|---|---|
| `#!/usr/bin/env bash` | Use Bash to interpret the file |
| `set -euo pipefail` | Fail on errors, undefined variables, and failing commands in pipelines |
| `BASH_SOURCE[0]` and `dirname` | Determine the real directory containing the installer |
| `PROJECT_DIR="$(cd ... && pwd -P)"` | Resolve the project root from the script's parent directory |
| `PYTHON_PATH="$PROJECT_DIR/.venv/bin/python"` | Point systemd at the project's own virtual environment |
| `CURRENT_USER="$(id -un)"` | Use the Linux account running the installer as the service account |
| `SERVICE_NAME="tiki-etl.service"` | **Hardcoded unit name** the installer will create; verify before deploying |
| `EUID` check | Reject running the entire script as root; it uses `sudo` only for systemd operations |
| `-x "$PYTHON_PATH"`, `-f "$PROJECT_DIR/main.py"` | Stop if virtual-environment Python or the entry point is missing |
| Check project path for spaces | Reject unsupported paths with spaces (simple unquoted ExecStart rendering) |
| `ps -p 1 -o comm=` | Require `systemd` to be PID 1, except for dry-run |
| `UNIT_CONTENT=$(cat <<EOF ... EOF)` | Construct a unit file from the current user and absolute paths |
| `--dry-run` condition | Print generated unit and exit without installing |
| `pgrep ...` | Warn if a matching Python pipeline seems active; **does not abort installation** |
| `sudo tee /etc/systemd/system/...` | Write the generated unit with root privileges |
| `sudo systemctl daemon-reload` | Make systemd reload unit definitions |
| `sudo systemctl enable ...` | Enable service for distro startup; **does not start it now** |

The paths are computed from the script's location, so you can launch it from another working directory, as long as the installer remains inside the project's `scripts/` folder.

## C. Generated systemd unit

The script produces the following template (paths and username are substituted on your machine):

```ini
[Unit]
Description=Tiki product ETL pipeline
Wants=network-online.target
After=network-online.target
StartLimitIntervalSec=0

[Service]
Type=simple
User=<linux-user>
WorkingDirectory=<absolute-project-dir>
ExecStart=<absolute-project-dir>/.venv/bin/python -u <absolute-project-dir>/main.py
Restart=on-failure
RestartSec=300
RestartPreventExitStatus=78
TimeoutStopSec=60
KillSignal=SIGTERM
Environment=PYTHONUNBUFFERED=1
UMask=0077

[Install]
WantedBy=multi-user.target
```

### `[Unit]` — dependencies and identification

- `Description`: friendly service description in `systemctl status`.
- `Wants=network-online.target` and `After=network-online.target`: request/sequence startup after systemd's network-online target. **This does not guarantee Internet or Tiki availability**; Python's network retry handles temporary loss.
- `StartLimitIntervalSec=0`: disable systemd's rate limit on repeated start attempts; delay still comes from `RestartSec=300`.

### `[Service]` — how the pipeline runs

- `Type=simple`: the process is considered started when Python begins executing.
- `User`: execute as the installer user rather than root.
- `WorkingDirectory`: set the project's directory for file access.
- `ExecStart`: start the virtual-environment interpreter with `-u main.py`.
- `Restart=on-failure`: retry after crashes and nonzero exits, except prevented statuses and explicit stops.
- `RestartSec=300`: wait **300 seconds (5 minutes)** before restarting.
- `RestartPreventExitStatus=78`: do not restart if state/configuration is invalid or a second instance was detected.
- `TimeoutStopSec=60`: allow up to 60 seconds for graceful shutdown; systemd may then force termination.
- `KillSignal=SIGTERM`: request a graceful stop so the Python handler can checkpoint.
- `Environment=PYTHONUNBUFFERED=1`: flush logs promptly; similar purpose to Python `-u`.
- `UMask=0077`: new files are private to the service user by default.

### `[Install]` — startup integration

`WantedBy=multi-user.target` allows `systemctl enable` to arrange startup when the Ubuntu WSL distribution boots. Windows must still launch the distribution; see [service setup](06-service-setup.md).

## D. Important naming mismatch on this machine

Your already-running unit is:

```text
de-project-2-etl.service
```

But this script has:

```bash
SERVICE_NAME="tiki-etl.service"
```

**Do not execute it unchanged on the existing deployment.** Doing so creates a separate unit file and enabling/running both could target the same `checkpoint.json` and `output/`. Before reinstallation, inspect the current unit:

```bash
sudo systemctl cat de-project-2-etl.service
```

If you deliberately want the installer to manage the **existing** unit name, first back up/review the existing unit, stop the service during maintenance, change `SERVICE_NAME` to `de-project-2-etl.service`, run the install script, and check the generated unit. Do not change the service name just for documentation or routine ETL monitoring.

## E. Installer versus service management

```bash
bash scripts/install_service.sh --dry-run  # Review only
bash scripts/install_service.sh            # Create + enable tiki-etl.service
sudo systemctl start tiki-etl.service      # Start (separate action)
sudo systemctl stop tiki-etl.service       # Gracefully stop
sudo systemctl disable --now tiki-etl.service  # Stop + disable
```

These are **fresh-install examples**. On your already configured machine, substitute `de-project-2-etl.service` for direct `systemctl` operations; do **not** run the installer again merely to start/restart the existing service.
