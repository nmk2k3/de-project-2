# 06 — ETL service setup: WSL Ubuntu + systemd + Windows

This describes a **new installation** and also how to inspect an **already configured** installation.

> **Important:** On the current machine, `de-project-2-etl.service` already exists and runs. The bundled installer creates `tiki-etl.service` by default. Do **not** run it as-is on that machine or you may have two different enabled services competing for the same ETL files. See the [installer explanation](07-install-service-script.md).

## Part A — Prepare the project on Ubuntu WSL

1. Verify WSL Ubuntu is running and the project contains `main.py`, `app/`, `product_ids.csv` and `scripts/install_service.sh`.
2. Ensure Python and cURL are available, and create the project's virtual environment.
3. Install Python requirements and prepare local environment configuration.

```bash
cd /path/to/de-project-2
python3 --version
curl --version
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env   # Only for a fresh setup; keep any existing .env
.venv/bin/python -m unittest discover -s tests -v
```

Verify `BATCH_SIZE`, `CSV_FILE` and `REQUEST_DELAY` before starting a new or resumed run. A saved checkpoint is bound to its input CSV order and batch size.

## Part B — Ensure `systemd` is PID 1

```bash
ps -p 1 -o comm=
```

If it prints `systemd`, continue. If not, edit `/etc/wsl.conf` in Ubuntu:

```ini
[boot]
systemd=true
```

Do not remove unrelated sections already in `wsl.conf`. In **Windows PowerShell**, run `wsl --shutdown`; reopen Ubuntu and verify PID 1 again. The shutdown stops WSL applications; plan this for a maintenance window.

Reference: https://learn.microsoft.com/en-us/windows/wsl/systemd

## Part C — Install the systemd unit (fresh system only)

```bash
# Show what the script would install, without touching systemd
bash scripts/install_service.sh --dry-run

# Install the unit and enable it for WSL startup
bash scripts/install_service.sh

# The bundled script installs tiki-etl.service:
sudo systemctl start tiki-etl.service
sudo systemctl status tiki-etl.service --no-pager
sudo journalctl -u tiki-etl.service -f -o cat
```

`install_service.sh` **installs and enables**, but does **not** start the ETL. Review its unit definition before installing: `ExecStart` must point to your actual `.venv/bin/python` and `main.py`.

For the **already configured** service, use this instead:

```bash
sudo systemctl cat de-project-2-etl.service
sudo systemctl status de-project-2-etl.service --no-pager
sudo journalctl -u de-project-2-etl.service -f -o cat
```

A new unit must not be enabled/run in parallel with the existing one. To reuse the existing service name in the installer, edit `SERVICE_NAME` in the script **only after reviewing the current installed unit**; this is a deliberate replacement operation, not a routine step.

## Part D — How auto-restart works

The script creates a unit with:

```ini
[Service]
Type=simple
ExecStart=/absolute/path/to/.venv/bin/python -u /absolute/path/to/main.py
Restart=on-failure
RestartSec=300
RestartPreventExitStatus=78
KillSignal=SIGTERM
TimeoutStopSec=60
```

The actual unit also configures user, working directory, environment and file permissions. The systemd behavior depends on **process exit status**:

| Outcome | Python status | Auto-restart? |
|---|---:|---|
| All IDs processed; normal finish | `0` | No |
| Intentional Ctrl+C; persistent BytePlus pause | `0` | No |
| Network/server retries exhausted | `75` | Yes, after 300 seconds |
| Fatal configuration/checkpoint error; another instance | `78` | No, prevented explicitly |
| Unexpected SIGTERM | `143` | Normally yes, except an intentional service stop |
| SIGKILL / crash | signal / nonzero | Yes, after 300 seconds |

On restart, the pipeline reads the checkpoint and current `.partial.json` files. Service restart does not erase them.

## Part E — Start WSL after Windows logon

An enabled `systemd` unit starts when its **Ubuntu WSL distribution** starts; Windows does not guarantee that the distribution will remain running on its own. On the current Windows setup, a Task Scheduler task called **`Tiki ETL Auto Start`** starts WSL at logon:

1. In **Windows PowerShell**, check `wsl -l -v` (the distribution name is `Ubuntu`).
2. Open **Task Scheduler** → **Task Scheduler Library** → **Create Task**.
3. **General:** task name `Tiki ETL Auto Start`, choose your Windows account; `Run only when user is logged on` is a straightforward choice.
4. **Triggers:** `At log on`, `Enabled`.
5. **Actions:** `Start a program`; program `C:\Windows\System32\wsl.exe`, arguments:

   ```text
   -d Ubuntu --exec /usr/bin/sleep infinity
   ```

6. **Settings:** do not stop the task merely after 3 days; do not launch a new instance while one is running.
7. **Conditions:** review battery/AC restrictions appropriate to your device; optionally disable the rule that stops when switching to battery.
8. Save and choose **Run** to verify the task launches. Check the Ubuntu service separately with `systemctl status`.

The `sleep infinity` command **keeps a WSL session alive**; it does *not* run `main.py`. `systemd` runs the ETL unit. Running the task successfully does not by itself prove the logon trigger or a reboot will succeed.

**Limitations:** No requests run while Windows is shut down or asleep. The task begins at **user logon**, not before. On startup, the Python ETL resumes from the last valid checkpoint. Service uptime depends on WSL/Windows power and session settings.

## Part F — Verify without disturbing a live collection

```bash
sudo systemctl is-enabled de-project-2-etl.service
sudo systemctl show de-project-2-etl.service -p ActiveState -p MainPID -p Restart -p RestartUSec
sudo journalctl -u de-project-2-etl.service -n 30 --no-pager
```

The project has previously verified auto-restart after a forced process kill and resume from checkpoint on its WSL machine. Repeating a forced-kill test during a multi-day real ETL run is not necessary and can require re-fetching recent IDs.
