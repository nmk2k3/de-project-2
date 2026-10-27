# 08 — Recovery, errors and safe troubleshooting

## Failure policy

| Condition | Where it is logged | Pipeline behavior | systemd behavior |
|---|---|---|---|
| HTTP 404 | `failed_404.jsonl` | Record and advance; no retry | Service keeps running |
| Non-retryable HTTP error / transform failure | `failed_other.jsonl` | Record and advance | Service keeps running |
| Temporary cURL failure / HTTP 5xx | `failed_other.jsonl` if all retries fail | Retry with exponential backoff; persist current cursor if exhausted | Exit 75; restart after 5 min |
| Security HTML, 403, 429 | `failed_other.jsonl` | Pause 120s and retry bounded times; if still blocked, persist current cursor and stop | Exit 0; **no automatic restart** |
| Clean `Ctrl+C` / `systemctl stop` | Journal for progress message | Save partial state and cursor | Intentional service stop does not auto-restart |
| SIGKILL / power outage | Journal may show crash; Python cannot log the final event | Recover from last durable checkpoint at next boot | Restart on failure, or at next WSL startup |
| Corrupt checkpoint / CSV mismatch | stderr/journal | Stop rather than risk incorrect data | Exit 78, restart prevented |

## Understand repeated work

- `CHECKPOINT_EVERY=50` means up to about 49 recently processed input IDs may need replay after an uncatchable crash. A clean stop saves immediately.
- Checkpoint cursor is **zero-based**; the next item is `next_index + 1` in terminal progress.
- `*.partial.json` contains products plus metadata needed to continue a partially processed batch. A final `products_NNN.json` contains just a product list.
- JSONL failure logs are **events**, not a unique-ID list. The same ID can appear multiple times when a request is attempted again.
- Resume validates the ordered input fingerprint, batch size, selected partial metadata and previous batch output. A mismatch is an error to investigate, not an invitation to delete checkpoint files.

## Diagnose common problems

### `ModuleNotFoundError: No module named 'dotenv'` or `'bs4'`

Use the project venv, not an arbitrary system Python:

```bash
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -v
```

### No service logs / service not running

```bash
sudo systemctl status de-project-2-etl.service --no-pager
sudo journalctl -u de-project-2-etl.service -n 50 --no-pager
sudo systemctl cat de-project-2-etl.service
```

Check that the service's `ExecStart`, `User` and `WorkingDirectory` match the real repository and venv. Do not assume the installer-created `tiki-etl.service` is the same as your existing unit.

### BytePlus detected

Inspect the latest journal message and (if present) `debug_response.html`. The pipeline intentionally stops after its configured, bounded security retries. Do **not** defeat the challenge by repeatedly restarting; resolve access eligibility and limits before a manual restart.

### Temporary Internet failure

The client retries known transient cURL exit codes and HTTP 5xx with configured backoff. If unsuccessful, it saves the same ID as the next cursor and exits 75, so systemd can retry after 300s. The progress and error logs can be inspected without starting another pipeline.

### Windows restarted but WSL did not resume

In **Windows PowerShell** check `wsl -l -v` and the Task Scheduler task `Tiki ETL Auto Start`; verify its `At log on` trigger, action `wsl.exe -d Ubuntu --exec /usr/bin/sleep infinity`, and whether the user has logged in. WSL startup and `systemd` startup are distinct steps.

### Batch output or checkpoint mismatch

**Do not delete `checkpoint.json` or `.partial.json` as a first response.** Back up the project state, inspect the error and ordered CSV, and verify batch size. Resetting state can re-fetch processed IDs and overwrite batch files.

## Useful read-only commands

```bash
cat checkpoint.json
ls -lh output/ | tail -n 10
tail -n 20 logs/failed_404.jsonl
tail -n 20 logs/failed_other.jsonl
sudo journalctl -u de-project-2-etl.service -n 50 --no-pager
```

## Deployment checklist

1. Preserve `.env`, `data/product_ids.csv`, `checkpoint.json`, `output/`, and `logs/`.
2. Back up source files before replacing them.
3. Gracefully stop the **existing** service during deployment.
4. Run `unittest` and confirm tests pass.
5. Verify the correct unit name and `ExecStart` path.
6. Start the existing service and follow the journal to confirm resume.
7. Do not start the pipeline manually while its systemd service is active.
