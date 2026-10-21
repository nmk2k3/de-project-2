# 05 — Commands for running and monitoring the ETL

Use **Ubuntu WSL** for Linux commands unless the section says **Windows PowerShell**. Run commands from the project root when they reference `.venv/`, `.env`, `logs/`, or `checkpoint.json`.

## 1. Development / manual run

```bash
cd /path/to/de-project-2
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -v

# Only if the ETL systemd service is stopped:
.venv/bin/python -u main.py
```

`-u` enables unbuffered Python output so progress appears immediately. `Ctrl+C` stops a **manually launched process** and the pipeline attempts to save its checkpoint. This is different from using `Ctrl+C` while merely following `journalctl`.

**Never start a manual process while the systemd service is running.** The `.pipeline.lock` file is intended to reject concurrent pipeline instances, but avoiding concurrent starts is still good operating practice.

## 2. Installed systemd service — this user's machine

The active service has the name **`de-project-2-etl.service`**. Use that name in these commands:

```bash
# Inspect service configuration and state
sudo systemctl status de-project-2-etl.service --no-pager
sudo systemctl show de-project-2-etl.service -p ActiveState -p MainPID -p Restart -p RestartUSec
sudo systemctl cat de-project-2-etl.service

# Follow log in real time
sudo journalctl -u de-project-2-etl.service -f -o cat

# Last 50 journal lines
sudo journalctl -u de-project-2-etl.service -n 50 --no-pager

# Stop intentionally — does not trigger on-failure auto-restart
sudo systemctl stop de-project-2-etl.service

# Start from its saved checkpoint
sudo systemctl start de-project-2-etl.service

# Restart after a code/config change
sudo systemctl restart de-project-2-etl.service

# Enable or disable automatic startup when WSL boots
sudo systemctl enable de-project-2-etl.service
sudo systemctl disable de-project-2-etl.service
```

`journalctl -f` can be exited with `Ctrl+C` **without stopping** the ETL service.

> **Service-name distinction:** The example installer currently creates `tiki-etl.service`, **not** `de-project-2-etl.service`. Use the correct name for the installed service. Do not start both units on the same project directory.

## 3. Inspect progress and failures

```bash
# Current durable resume point
cat checkpoint.json

# Recent HTTP 404 records
tail -n 20 logs/failed_404.jsonl

# Recent errors other than HTTP 404
tail -n 20 logs/failed_other.jsonl

# Follow new non-404 errors live
tail -f logs/failed_other.jsonl

# Count error events (NOT necessarily unique product IDs)
wc -l logs/failed_404.jsonl logs/failed_other.jsonl

# View completed JSON batch files
ls -lh output/products_*.json
```

`failed_*.jsonl` files are created **only after that type of error occurs**; `tail`/`wc` can report 'No such file' before the first matching event. Logs are append-only and may contain repeated attempts for the same ID.

The checkpoint `next_index` is zero-based, so the next terminal line normally reports global position `next_index + 1`. Periodic saves mean in-memory progress may be ahead of this durable cursor.

## 4. Apply code changes safely

```bash
# Do not delete checkpoint/output/log files
sudo systemctl stop de-project-2-etl.service

# Update source files, then run offline tests
.venv/bin/python -m unittest discover -s tests -v

sudo systemctl start de-project-2-etl.service
sudo journalctl -u de-project-2-etl.service -n 30 --no-pager
```

Stopping the service cleanly sends SIGTERM, which the main process handles as an interrupt and saves progress. If the process has been forcibly killed, resume uses the last durable checkpoint instead.

## 5. Windows PowerShell

```powershell
wsl -l -v
Get-ScheduledTask -TaskName "Tiki ETL Auto Start"
(Get-ScheduledTask -TaskName "Tiki ETL Auto Start").Triggers |
  Select-Object @{Name="Type";Expression={$_.CimClass.CimClassName}}, Enabled
```

The Task Scheduler entry launches the WSL distribution at Windows logon. It is not a substitute for the `systemd` service inside Ubuntu.
