# Tiki ETL — Phase 2: Data Integrity & Offline Tests

This update builds on the modular refactor. It **does not add network retry or OS-level auto restart**. Existing cURL request pacing, 120-second BytePlus handling, JSON output, and append-only `logs/failed.jsonl` remain in place.

## Changes

1. **Input fingerprint:** the SHA-256 hash of the *ordered product ID list* is stored in new `checkpoint.json` and `products_NNN.partial.json` files. Restarting with reordered/replaced IDs now raises an error even when the CSV row count is unchanged.
2. **Crash-consistent JSON files:** write a temporary file, flush it, and atomically replace the destination. The completed batch is first stored as a fully processed `.partial.json`, then written as `products_NNN.json`, then committed to the checkpoint. A crash during finalization can be recovered without fetching that whole batch again.
3. **Validate recovery:** check metadata and that saved product IDs belong to the processed section. At restart, check the last completed JSON output exists, is readable, and contains plausible IDs. If a completed partial exists but the final JSON is absent, recover the final file.
4. **Offline regression tests:** tests do not call Tiki; they cover CSV changes, resume after interruption, checkpoint crash windows, invalid/corrupted outputs, HTML transform, and HTTP 404 failure logging.

## Install / update in WSL

**Before replacing any source code, stop the running process and back up the project.** Extract the archive's `app/`, `tests/`, `main.py`, `README.md`, `requirements.txt` into the **existing** project folder that already contains `product_ids.csv`. Keep existing `.env`, `checkpoint.json`, `output/`, and `logs/`. The ZIP does *not* contain these user data files and does not include a `.env` file.

For example, from the project folder:

```bash
# Back up checkpoint and outputs to a separate location of your choice first.
# Copy updated source files into this folder, leaving data untouched.
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -u main.py
```

12 offline tests should pass without any network traffic. Only the final `main.py` command requests Tiki API.

## Backwards compatibility

- Existing legacy `checkpoint.json` files lacking `ids_fingerprint` are accepted. **The original CSV order cannot be verified retroactively.** Keep the same CSV and ID order when resuming; the fingerprint will appear at the next successful save.
- Legacy `.partial.json` files without `ids_fingerprint` are accepted and validated against the currently loaded ID slice where possible.
- Do **not** change `BATCH_SIZE` for an in-progress dataset. Existing checkpoint protection is preserved.
- Abnormal termination between checkpoints may require re-fetching up to `CHECKPOINT_EVERY - 1` IDs. The append-only error log may include duplicate error attempts. The pipeline provides *at-least-once* processing within this small window, not an exactly-once guarantee.
- Neither automatic process restart nor a general network retry policy is included in this phase. Those will be separate future steps.

## File ownership

- `main.py`: entry point
- `app/config.py`: `.env` settings
- `app/client.py`: HTTP/cURL and existing pacing/security handling
- `app/transform.py`: product normalization
- `app/storage.py`: crash-safe JSON, validation, checkpoint and failure log
- `app/pipeline.py`: orchestration
- `tests/test_integrity.py`: standard-library `unittest` tests
