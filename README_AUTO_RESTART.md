# Phase 4 — Auto Restart (WSL Ubuntu + Windows)

**Only apply this patch AFTER Phase 3.** No changes to request pacing, transformation, network retries or output format. It includes `main.py`, `app/pipeline.py`, `scripts/install_service.sh`, and `tests/test_autorestart.py`.

## 0. Back up and test BEFORE turning on the service

From the project root:

```bash
cp main.py main.py.before_phase4
cp app/pipeline.py app/pipeline.py.before_phase4
```

Copy/merge the patch into the project root. Keep `.env`, `checkpoint.json`, `product_ids.csv`, `output/`, `logs/` and the other Phase 3 files unchanged.

```bash
.venv/bin/python -m unittest discover -s tests -v
```

**Do not manually start `python main.py` while the systemd service is running.** A Linux `flock` file `.pipeline.lock` prevents concurrent runs. Add `.pipeline.lock` to `.gitignore`.

## 1. Why an exit status matters

The old Phase 3 pipeline returned normally even when all transient retries failed, so systemd could not tell a temporary failure apart from a completed job. Phase 4 reports these outcomes to the supervisor:

| Exit status | Meaning | `Restart=on-failure` |
|---|---|---|
| 0 | Finished, Ctrl+C, or BytePlus pause | No |
| 75 | Network/5xx retry exhausted | Yes, after 300s |
| 78 | Invalid configuration, corrupt checkpoint or another instance already running | No (`RestartPreventExitStatus=78`) |
| 143 | Unexpected SIGTERM, progress saved | Yes (but NOT on explicit `systemctl stop`) |
| SIGKILL / crash | Process died without saving | Yes, after 300s |

BytePlus continues using Phase 3's configured `SECURITY_RETRY_DELAY` and `MAX_SECURITY_RETRIES`. If still blocked, the process pauses with exit 0, instead of restarting endlessly into an access restriction.

A SIGKILL (`kill -9`), power outage, or Windows forced shutdown cannot be caught by Python: recovery uses the **last durable checkpoint**, usually at most `CHECKPOINT_EVERY - 1` IDs behind. Already-saved IDs are not lost, but a few recent requests can be repeated. On clean SIGTERM, progress is checkpointed immediately. `failed.jsonl` is append-only and may have repeated error entries for retried IDs.

## 2. Enable systemd in Ubuntu WSL if needed

```bash
ps -p 1 -o comm=
```

If output is `systemd`, proceed. Otherwise, in WSL:

```bash
sudo nano /etc/wsl.conf
```

Ensure the `[boot]` section contains:

```ini
[boot]
systemd=true
```

Preserve other existing `wsl.conf` entries/sections. In **Windows PowerShell**:

```powershell
wsl --shutdown
```

Reopen Ubuntu, then check again. See official Microsoft guidance: https://learn.microsoft.com/en-us/windows/wsl/systemd

## 3. Install and start the service (from project root in WSL)

The installer derives the Linux user and absolute project directory automatically. Its actions are limited to writing `/etc/systemd/system/tiki-etl.service`, reloading systemd and enabling the service. **It does not start the ETL automatically at installation time.**

```bash
bash scripts/install_service.sh --dry-run   # See unit definition
bash scripts/install_service.sh             # Install and enable
sudo systemctl start tiki-etl.service       # Explicitly start pipeline
```

Monitoring:

```bash
sudo systemctl status tiki-etl.service --no-pager
journalctl -u tiki-etl.service -f -o cat
```

Stop intentionally:

```bash
sudo systemctl stop tiki-etl.service
```

Restart after reviewing security check:

```bash
sudo systemctl start tiki-etl.service
```

Disable boot startup and stop:

```bash
sudo systemctl disable --now tiki-etl.service
```

**Do not run the service and a manual `main.py` process simultaneously.**

## 4. Windows power-off / restart: bring WSL up again

An enabled systemd unit starts when its **WSL distribution starts**. But systemd services **do not themselves keep a WSL instance alive** (Microsoft documentation). To keep WSL open for a multi-day ETL run and start it again after you log on to Windows, use Windows Task Scheduler:

1. In Windows PowerShell, run `wsl -l -v` to find the exact distribution name (e.g. `Ubuntu`).
2. Open **Task Scheduler** → **Create Task**.
3. **General**: select your Windows account; use **Run only when user is logged on** to start with (simplest reliable option).
4. **Triggers**: **At log on** for that account.
5. **Actions** → **Start a program**:
   - Program/script: `C:\Windows\System32\wsl.exe`
   - Arguments: `-d Ubuntu --exec /usr/bin/sleep infinity` (replace `Ubuntu` with the exact name shown by `wsl -l -v`).
6. **Conditions/Settings**: do not stop the task merely because it has been running for 3 days, and check any power-related limits relevant to your machine.
7. Save the task and trigger a run. The long-lived foreground sleep holds a WSL session open; systemd starts the enabled `tiki-etl.service` when that WSL distribution starts.

This configuration begins at **Windows user logon**, not necessarily before logon. It cannot process requests while the computer is powered off, shut down, asleep, or without a working network. It only **resumes from checkpoint after the system is back**. Actual behavior depends on Windows/WSL power and task settings.

Official reference: https://learn.microsoft.com/en-us/windows/wsl/systemd

## 5. Safe checks

Run the offline unit tests before deploying. Optionally check auto-restart behavior with a **small mock pipeline**; avoid killing a live multi-hour data collection process merely to test this, since SIGKILL can lose up to one checkpoint interval of work. This package's offline tests validate exit codes, locking, SIGTERM checkpointing, and same-ID recovery, but cannot simulate an actual Windows reboot or prove WSL remains active on your device.

During operation, inspect `journalctl` for errors and `checkpoint.json` for progress. If you see BytePlus or a fatal state error, fix the underlying issue before manually starting the service again.
