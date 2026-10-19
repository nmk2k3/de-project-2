# Phase 3 — Network Retry (only)

This patch updates only `app/client.py`, `app/config.py` and `app/pipeline.py`; it adds a standalone offline test. It does NOT add auto-restart.

## Install

From your project root, BACK UP your customized app files, then extract `tiki_phase3_retry_patch.zip` into the project folder. Do not delete `.env`, `checkpoint.json`, `output/`, or `logs/`.

Add the following OPTIONAL values to your existing `.env`:

```dotenv
MAX_NETWORK_RETRIES=2
RETRY_BASE_DELAY=2
```

The defaults are already `2` and `2.0`, so these entries are only needed if you want to change them.

Run offline tests:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Run pipeline:

```bash
.venv/bin/python -u main.py
```

## What happens?

- HTTP 404: log once; no retry.
- HTTP 5xx and common transient cURL errors (timeout / connection reset / DNS failure): retry up to 2 more times, waiting 2s and then 4s (default).
- 403/429/BytePlus: untouched security-check behavior in `pipeline.py` (120s and a bounded retry count per existing configuration).
- After all transient retries fail: one `RetryExhaustedError` entry is appended to the failure log, partial batch + checkpoint saved, program stops. Run again when the connection is restored; resume retries the SAME ID.
- Request pacing (`REQUEST_DELAY`) is applied to every actual API attempt, including retries.

All tests are local/mock-based and do not call the Tiki API. Tests passed in the build environment.
