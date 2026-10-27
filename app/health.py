"""Best-effort operational telemetry; never used for checkpoint recovery."""
import json
import logging
import math
import os
import time


STATUS_FILE = ".crawler-health.json"
WRITE_INTERVAL = 10


class Progress:
    def __init__(self, settings):
        self.path = settings.root / STATUS_FILE
        # Include the shared pacing queue and every attempt/backoff in a window.
        attempts = settings.max_network_retries + 1
        self.fetch_budget = settings.max_workers * (
            attempts * (settings.request_timeout + settings.request_delay)
            + settings.retry_base_delay * (2 ** settings.max_network_retries - 1)
        )
        now = time.time()
        self.data = dict(pid=os.getpid(), state="starting", updated_at=now,
                         last_progress_at=now, next_index=None, expected_until=now,
                         fetch_budget=self.fetch_budget)
        self.last_write = 0
        self.warned = False

    def update(self, state, *, next_index=None, wait=0, force=True):
        now = time.time()
        if state == "fetching" and self.data["state"] in ("starting", "cooldown"):
            force = True  # Publish the new retry allowance before a long fetch.
        if next_index is not None and next_index != self.data["next_index"]:
            self.data.update(next_index=next_index, last_progress_at=now)
        self.data.update(state=state, updated_at=now, expected_until=now + wait)
        if not force and now - self.last_write < WRITE_INTERVAL:
            return
        temporary = self.path.with_suffix(".json.tmp")
        try:
            temporary.write_text(json.dumps(self.data) + "\n", encoding="utf-8")
            temporary.replace(self.path)
            self.last_write = now
        except OSError:
            # Monitoring must not turn a successful crawl into a failed crawl.
            if not self.warned:
                logging.warning("Cannot write crawler health status", exc_info=True)
                self.warned = True


def assess(service, status, *, now, free_bytes, min_free_bytes,
           stall_after=900, grace=60):
    """Return Nagios-style (code, message), without any process-control action."""
    if free_bytes < min_free_bytes:
        return 2, f"LOW_DISK: {free_bytes} bytes free (minimum {min_free_bytes})"
    active = service.get("ActiveState")
    if active == "failed":
        return 2, f"FAILED: systemd result={service.get('Result')} exit={service.get('ExecMainStatus')}"
    if active == "activating":
        return 0, "WAITING: systemd starting or waiting to restart (possibly network retry exhaustion)"
    if active in ("inactive", "deactivating"):
        # An explicit systemctl stop can leave a nonzero ExecMainStatus.
        return 0, "STOPPED: inactive/stopping; completion, operator stop or security pause (see journal)"
    if active != "active":
        return 3, "UNKNOWN: unavailable systemd state"
    try:
        if status["pid"] != int(service["MainPID"]):
            return 3, "UNKNOWN: status belongs to another process; wait for startup telemetry"
        state = status["state"]
        updated = float(status["updated_at"])
        progress = float(status["last_progress_at"])
        expected = float(status["expected_until"])
        budget = float(status["fetch_budget"])
        if not all(math.isfinite(value) for value in (updated, progress, expected, budget)) or budget < 0:
            raise ValueError("nonfinite timestamp")
        if state not in {"starting", "fetching", "processing", "cooldown",
                         "completed", "security_paused", "transient_exhausted",
                         "interrupted", "failed"}:
            raise ValueError("invalid state")
    except (KeyError, TypeError, ValueError):
        return 3, "UNKNOWN: missing or invalid crawler status"
    if updated > now + grace or progress > now + grace:
        return 3, "UNKNOWN: clock moved backwards; inspect timestamps"
    if state in ("fetching", "processing", "starting"):
        # A throttled snapshot may precede the next fetch by up to ten seconds.
        expected = max(expected, updated + budget + WRITE_INTERVAL)
    if state == "failed":
        return 2, "FAILED: crawler raised an exception; inspect journal"
    # A fresh heartbeat is not enough: the cursor must advance too. Explicit
    # bounded waits defer both checks, but cannot suppress an alert forever.
    if now > max(updated + stall_after, expected + grace):
        return 2, f"STALLED: stale telemetry in {state}, next_index={status.get('next_index')}"
    if now > max(progress + stall_after, expected + grace):
        return 2, f"NO_PROGRESS: cursor unchanged in {state}, next_index={status.get('next_index')}"
    detail = " (bounded network retries allowed)" if state == "fetching" else ""
    return 0, f"OK: {state}{detail}, next_index={status.get('next_index')}, progress_age={max(0, now - progress):.0f}s"
