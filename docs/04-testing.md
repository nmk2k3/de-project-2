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

# Concurrency safety and mocked sequential/parallel performance comparison
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
| `test_retry.py` | HTTP 503 retry and success, timeout retry, HTTP 404 without retry, exhausted retry stops on the same ID |
| `test_parallel.py` | Shared pacing including retries, out-of-order resume, main-thread-only writes, security cancellation/cooldown, worker failure, interruption, bounded concurrency and sequential/parallel output equivalence |
| `test_autorestart.py` | Main exit statuses, supervisor restart signaling, security pause behavior, SIGTERM checkpoint save, rejection of a second process |
| `test_failure_logging.py` | HTTP 404 only in `failed_404.jsonl`, other failures in `failed_other.jsonl`, append behavior |

The parallel test module prints a reproducible offline benchmark: nine mocked responses with 120ms latency and the same 20ms global request spacing for both `MAX_WORKERS=1` and `MAX_WORKERS=3`. It checks identical output bytes, the concurrency bound, and a conservative latency improvement. Timing includes local persistence overhead and varies by machine; it is not a production throughput guarantee. No live API requests are made.

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
