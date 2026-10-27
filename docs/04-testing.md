# 04 — Automated testing

The project uses Python's standard-library **`unittest`**. Tests mock network operations and use temporary directories; they do **not** send HTTP requests to Tiki.

## Run tests

Run from the **repository root** in Ubuntu WSL:

```bash
# Entire suite; verbose results for each test
.venv/bin/python -m unittest discover -s tests -v

# Only the integrity test module
.venv/bin/python -m unittest discover -s tests -p 'test_integrity.py' -v

# Only the retry tests
.venv/bin/python -m unittest discover -s tests -p 'test_retry.py' -v

# Concurrency safety and sequential/parallel output comparison
.venv/bin/python -m unittest discover -s tests -p 'test_parallel.py' -v

# Only auto-restart behavior tests
.venv/bin/python -m unittest discover -s tests -p 'test_autorestart.py' -v

# Only error-log separation tests
.venv/bin/python -m unittest discover -s tests -p 'test_failure_logging.py' -v
```

Syntax: `-m unittest` runs the testing module, `discover` finds test files, `-s tests` sets the search directory, `-p` filters file names and `-v` shows per-test status.

## What is tested?

| File | Cases |
|---|---|
| `test_integrity.py` | CSV fingerprint/order, partial-versus-checkpoint consistency, corrupted/missing output, atomic write errors, batch finalization/recovery, HTML cleanup, Ctrl+C and HTTP failures |
| `test_retry.py` | Server/timeout/cURL startup recovery, permanent/security failures without network retry, retry budget and exponential backoff |
| `test_parallel.py` | Shared pacing including retries, out-of-order resume, main-thread-only writes, security cancellation/cooldown, worker failure, interruption, bounded concurrency and sequential/parallel output equivalence |
| `test_autorestart.py` | Main exit statuses, supervisor restart signaling, security pause behavior, SIGTERM checkpoint save, rejection of a second process |
| `test_failure_logging.py` | HTTP 404 only in `failed_404.jsonl`, other failures in `failed_other.jsonl`, append behavior |
| `test_config.py` | Environment and direct-construction validation, CSV paths, valid zero delays/retry counts |
| `test_health.py` | Stalls, bounded cooldown/retry budgets, service states, invalid telemetry, low disk, telemetry write failure and checkpoint isolation |

The parallel tests use synchronization barriers to verify overlapping requests, bounded concurrency, and identical ordered output for `MAX_WORKERS=1` and `MAX_WORKERS=3`, without a machine-dependent speed threshold. The shared request-spacing test still measures time because pacing is its behavior under test. No live API requests are made.

## Refactoring notes

- Merged server and timeout retry-success tests into named subtests, including cURL startup failure. Permanent/security response cases share a no-retry test; retry exhaustion also checks the backoff schedule.
- Removed the duplicate pipeline retry/resume test from `test_retry.py`. The auto-restart test retains the saved prefix, same-ID resume, completion cursor, and final product checks; parallel tests retain out-of-order failure recovery.
- Merged the standalone log-append test into the 404 routing test, which already checks that both appended records survive in order.
- Replaced the latency benchmark with synchronized concurrency/output checks. Retained crash-window recovery, cancellation, interruption, main-thread persistence, and telemetry regressions because they protect distinct failure paths.

Verification: the full suite passed before (50 tests) and after (47 test methods,
with additional subtests). A standard-library `trace` comparison against the
pre-refactor tests recorded 567 versus 580 executed production-source lines,
with no previously executed lines lost. This is a line-execution comparison,
not branch-coverage measurement.

## How mocking works

For example, tests replace `subprocess.run` with a fake cURL result:

```python
from unittest.mock import patch

@patch("app.client.subprocess.run")
def test_example(mock_run):
    mock_run.return_value.returncode = 0
    mock_run.return_value.stdout = '{"id":101,"name":"ok"}\n200'
    mock_run.return_value.stderr = ""
    # Call ProductClient with a temporary test Settings object.
```

This allows repeatable testing of network errors without hitting the production API. Test data lives in temporary directories instead of the real `output/` and `checkpoint.json` files.

## When to run the suite

- After changing `app/` code, especially `client.py`, `storage.py`, or `pipeline.py`.
- Before restarting a running systemd service to deploy new code.
- Before changing the checkpoint format, retry rules or error-log format.

A passing offline suite **does not validate** actual `systemd` behavior, Windows reboot behavior, real API availability, or long-duration stability. Those require separate operational checks.

For a live deployment, avoid running a second manual pipeline. The tests themselves are safe to run while the service is active because they mock external operations.
