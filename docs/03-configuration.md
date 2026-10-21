# 03 — Environment and configuration

Settings are read by `app/config.py` using `python-dotenv`. All project-relative paths are resolved from the directory containing `main.py`, **not** the terminal's current working directory.

## First-time setup (Ubuntu WSL)

```bash
cd /path/to/de-project-2
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
curl --version
cp .env.example .env  # Only when .env does not exist
```

The Python dependencies are `pandas`, `beautifulsoup4`, and `python-dotenv`. The `curl` executable is an external program, not a pip package; it must be installed separately if missing.

**Never overwrite an existing `.env` just to update the code.** Never commit tokens or local credentials stored in `.env`.

## `.env` reference

```dotenv
API_URL=https://api.tiki.vn/product-detail/api/v1/products
CSV_FILE=product_ids.csv
BATCH_SIZE=1000
REQUEST_DELAY=1
REQUEST_TIMEOUT=15
CHECKPOINT_EVERY=50
SECURITY_RETRY_DELAY=120
MAX_SECURITY_RETRIES=2
MAX_NETWORK_RETRIES=2
RETRY_BASE_DELAY=2
```

The last two retry settings are supported by `config.py`; they may be absent from older `.env.example` files because they also have built-in defaults.

| Variable | Default | Explanation |
|---|---:|---|
| `API_URL` | product-detail endpoint | URL prefix for one product ID |
| `CSV_FILE` | `product_ids.csv` | File containing an `id` column; relative paths use project root |
| `BATCH_SIZE` | `1000` | Number of **input IDs** per output batch; do not change mid-run |
| `REQUEST_DELAY` | `1` | Minimum seconds between starts of cURL attempts |
| `REQUEST_TIMEOUT` | `15` | Per-request cURL maximum time, in seconds |
| `CHECKPOINT_EVERY` | `50` | Save partial state every N completed IDs within the batch |
| `SECURITY_RETRY_DELAY` | `120` | Seconds to wait after security challenge before retry |
| `MAX_SECURITY_RETRIES` | `2` | Additional security attempts after the first failure |
| `MAX_NETWORK_RETRIES` | `2` | Additional transient network/5xx attempts |
| `RETRY_BASE_DELAY` | `2` | Exponential backoff starts at 2 seconds, then 4 seconds |

Settings validation enforces positive batch size, checkpoint interval and request timeout; retry counts and delays cannot be negative.

## Input data rules

CSV example:

```csv
id
1391347
214008432
```

Keep the **same ordered list of IDs** for the lifetime of a checkpoint. Changing the order or modifying product IDs may cause the fingerprint integrity check to reject resume. A missing or malformed CSV stops execution; it is not treated as a transient HTTP failure.

## Git ignore recommendations

Ensure the project-root `.gitignore` includes:

```gitignore
.env
.venv/
__pycache__/
*.pyc
/output/
/logs/
/checkpoint.json
/checkpoint.json.tmp
/.pipeline.lock
/debug_response.html
```

If any such files were already tracked, removing them from the Git index (with `git rm --cached`) is separate from adding `.gitignore` and does not erase them locally.
