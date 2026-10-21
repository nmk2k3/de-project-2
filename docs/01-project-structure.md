# 01 — Project structure

```text
de-project-2/
├── main.py                          # CLI entry point / process exit status / single-instance lock
├── app/
│   ├── __init__.py
│   ├── config.py                    # Settings dataclass; read and validate .env
│   ├── client.py                    # cURL requests, pacing, error metadata and network retries
│   ├── transform.py                 # Product field selection; HTML description cleanup
│   ├── storage.py                   # Atomic JSON, checkpoints, integrity checks and failure logs
│   └── pipeline.py                  # Orchestrates IDs, batches, retries, save/resume
├── tests/
│   ├── test_integrity.py            # Data consistency, file recovery, transform, resume
│   ├── test_retry.py                # HTTP/network retry and resume on exhaustion
│   ├── test_autorestart.py          # Process outcomes, SIGTERM, single-instance lock
│   └── test_failure_logging.py      # Separate HTTP 404 from other failure logs
├── scripts/
│   └── install_service.sh           # Builds/enables systemd unit (does NOT start it)
├── docs/                             # This documentation
├── requirements.txt                 # Python dependencies
├── .env.example                     # Documented default configuration template
├── .env                             # Local settings; not committed
├── .gitignore
├── product_ids.csv                  # Input: column named id
├── output/                          # Generated batch and partial JSON files
│   ├── products_001.json
│   └── products_002.partial.json    # Only while a batch is incomplete
├── logs/                             # Append-only failure logs
│   ├── failed_404.jsonl
│   └── failed_other.jsonl
├── checkpoint.json                  # Resume state and input fingerprint
├── .pipeline.lock                   # flock coordination, not a checkpoint
└── debug_response.html              # Last detected HTML challenge (if any)
```

Generated files are examples; their presence depends on how far the pipeline has run.

## Module responsibilities

| Module | Main entry points | What it owns |
|---|---|---|
| `main.py` | `main()` | SIGTERM handling, Linux `flock`, convert outcomes to exit codes |
| `app/config.py` | `Settings`, `load_settings()` | Absolute paths, environment variables, validation |
| `app/client.py` | `ProductClient.fetch()` | HTTP request pacing, cURL execution, errors and bounded transient retries |
| `app/transform.py` | `clean_description()`, `transform_product()` | Clean HTML and create normalized output object |
| `app/storage.py` | `save_progress()`, `finish_batch()`, `load_checkpoint()`, `load_batch()`, `log_failure()` | Durable writes, checkpoint state, consistency checks, JSONL logs |
| `app/pipeline.py` | `run_pipeline()` | Iterates IDs, handles security pauses, saves progress, chooses stop/continue |

## Input and output contracts

**Input:** a CSV with an `id` column, read as **string** (`dtype={"id": str}`) so IDs are not changed by numeric conversion.

**Output:** an array of successfully transformed products in `output/products_NNN.json`:

```json
[
  {
    "id": 123,
    "name": "Example product",
    "url_key": "example-product",
    "price": 99000,
    "description": "Description without HTML markup.",
    "images_url": ["https://example.invalid/image.jpg"]
  }
]
```

The record is illustrative, not a Tiki API response. A file covers **up to** `BATCH_SIZE` *input IDs*, so a batch containing HTTP 404s may have fewer successful product objects than the configured batch size. File names are based on the **input batch number**.

**Error outputs:** each JSONL line is one JSON object containing `id`, `batch`, `batch_position`, `global_position`, `stage`, `error_type`, `http_status`, `curl_exit_code`, and `reason`.

- `logs/failed_404.jsonl`: events with numeric `http_status == 404`.
- `logs/failed_other.jsonl`: network failures, 5xx, security challenges, transform errors and other non-404 errors.
- Historical `logs/failed.jsonl` (from older versions) is **not** automatically migrated.

## What belongs in Git?

Commit source code, `tests/`, `docs/`, `requirements.txt`, `scripts/`, `.env.example` and README files. Exclude the local `.env`, `.venv/`, `output/`, `logs/`, `checkpoint.json`, `checkpoint.json.tmp`, `.pipeline.lock`, `debug_response.html` and `__pycache__/`.

Do not change the input CSV content or `BATCH_SIZE` during an in-progress run without planning a deliberate checkpoint migration or a fresh run.
