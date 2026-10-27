# Lightweight health monitoring

## Process ownership and rollout

The bundled service uses `Restart=on-failure`, `RestartSec=300`,
`RestartPreventExitStatus=78`, SIGTERM and a 60-second stop timeout. The existing
single-instance `flock` prevents overlapping service/manual runs. Network retry
exhaustion exits 75; configuration/checkpoint errors exit 78; completion, Ctrl+C
and persistent BytePlus challenges exit 0. An explicit `systemctl stop` suppresses
restarts even though SIGTERM produces exit 143.

**systemd remains the only restart owner.** The checker never starts, stops,
signals or restarts a process. No crawler unit settings, checkpoint format or
output files are changed. Inspect the deployed policy rather than reinstalling:

```bash
systemctl cat de-project-2-etl.service
systemctl show de-project-2-etl.service -p ActiveState -p SubState -p MainPID -p Result -p ExecMainStatus -p Restart -p RestartUSec -p RestartPreventExitStatus -p TimeoutStopUSec
journalctl -u de-project-2-etl.service -n 50 --no-pager
journalctl -u de-project-2-etl.service -f -o cat
```

A running process only picks up tracking on its **next normal start**. Do not
interrupt a live crawl just to enable monitoring. A missing or PID-mismatched
status is UNKNOWN, not proof of a stall. The checker targets a system service;
manual runs still write telemetry but are not verified by this command.

## What is measured

`.crawler-health.json` is an independent, atomic, best-effort status snapshot.
Ordinary progress writes are throttled to once per ten seconds; initialization,
cooldown transitions and final outcomes are flushed immediately. No heartbeat
thread or extra dependency is needed. Write failures log a warning without
changing crawling or checkpoint behavior. The crawler never reads this file to
resume. `next_index` is an **in-memory processed cursor**, including ordinary
per-product failures, not a count of successful products or a durable checkpoint.

The checker combines systemd state, telemetry and available disk space on the
project, output and logs filesystems (including existing symlink targets):

| Condition | Result |
|---|---|
| Active crawl, recent cursor progress | OK |
| Bounded fetch/network retries | OK during conservative request budget |
| BytePlus cooldown | OK until the recorded cooldown deadline plus grace |
| Inactive/stopping service | STOPPED, not a stall; consult journal for exact reason |
| systemd starting/auto-restart wait | WAITING, not a stall |
| systemd failed, or recorded exception | FAILED |
| Active but stale telemetry / unchanged cursor beyond allowance | STALLED / NO_PROGRESS |
| Free space below threshold, even when stopped | LOW_DISK |
| Missing/corrupt/PID-mismatched status, unavailable service/systemd | UNKNOWN |

Default stale/progress threshold: **900 seconds**. Fetch allowance conservatively
includes all workers, request timeouts, shared pacing and exponential network
backoffs. A throttled snapshot gets an additional ten-second allowance. This
distinguishes expected retry time from a suspected hang without adding hooks to
worker threads. It does not identify an individual retry attempt. Cooldowns have
explicit deadlines; a frozen cooldown eventually alerts too. Intentionally repeated
security waits are allowed until the crawler's existing retry limit stops it.
No heartbeat can independently keep an unchanged cursor healthy indefinitely.

Use a 60-second grace by default. Alerts wait until **both** the configured
stale/progress threshold and expected wait allowance have expired. This favors
avoiding false positives, at the cost of slower detection. Clock jumps, unusually
slow input validation, batch serialization, filesystem I/O or worker shutdown can
affect detection; inspect the journal and tune thresholds before trusting alerts.
Wall-clock timestamps are used across processes. Backward clock jumps produce
UNKNOWN when visible in recorded timestamps. Status is local, not a remote probe.

## Run once

From the repository (no `.env` loading or crawler dependencies required):

```bash
python3 scripts/check_health.py
python3 scripts/check_health.py --stall-after 1800 --grace 120 --min-free-mib 2048
cat .crawler-health.json
df -h . output logs
```

Exit codes: **0** OK/STOPPED/WAITING, **2** suspected failure or low disk,
**3** unknown/unverifiable. Inactivity is intentionally not an alert; this does
not enforce an always-running schedule. Repeated restart waits can hide a crash
loop, so also review `journalctl` and `systemctl show ... -p NRestarts`.
For a differently named deployed unit, pass `--service your-unit.service`.

## Periodic checks with cron

Run as the crawler's Linux user so it can read the status file. Review and add
this entry using `crontab -e`, replacing the absolute repository path:

```cron
*/5 * * * * /usr/bin/python3 /absolute/path/de-project-2/scripts/check_health.py 2>&1 | /usr/bin/logger -t tiki-health
```

This records every result (including UNKNOWN) and deliberately takes **no recovery
action**. View results with:

```bash
journalctl -t tiki-health --since '1 hour ago' --no-pager
systemctl status cron --no-pager
crontab -l
```

Cron and systemd only run while Linux/WSL is awake. Observe results over real
network outages, normal pauses and batch commits before adding any alert routing.
Do not attach a restart command to nonzero health results. Investigate low disk
without deleting checkpoints, partial batches or output automatically.

## Offline verification

```bash
.venv/bin/python -m unittest discover -s tests -p 'test_health.py' -v
.venv/bin/python -m unittest discover -s tests -v
```

Tests simulate stale processes, no progress despite fresh telemetry, finite network
retry/cooldown allowances, low disk, intentional stops, service failures,
unavailable systemd, malformed telemetry, throttling and failed telemetry writes.
A simulated BytePlus run verifies cooldown state and the original resume cursor.
Existing tests protect restart exit codes, interruption, integrity and recovery.
No live failure injection or service restart is needed.
