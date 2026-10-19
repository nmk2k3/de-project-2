# Tiki Product ETL — Documentation

Technical documentation for the Python ETL pipeline that reads product IDs from a CSV, requests product details from Tiki, transforms records, and writes batch JSON files with durable checkpoints.

**Current implementation:** Python 3, pandas, Beautiful Soup, cURL, `python-dotenv`, `unittest`, and `systemd` on Ubuntu WSL 2. This documentation describes the **post–Phase 4 code with split failure logs** (`failed_404.jsonl` / `failed_other.jsonl`).

## Documentation index

| Document | Contents |
|---|---|
| [01 — Project structure](01-project-structure.md) | Directory tree, module responsibilities, data artifacts |
| [02 — Pipeline flow](02-pipeline-flow.md) | Extract → transform → load, retries, checkpoint and resume |
| [03 — Configuration](03-configuration.md) | `.env` settings, dependencies and validation |
| [04 — Testing](04-testing.md) | Run all/individual offline tests and understand coverage |
| [05 — Operational commands](05-commands.md) | Run, stop, monitor, inspect errors and checkpoints |
| [06 — ETL service setup](06-service-setup.md) | Ubuntu WSL `systemd` + Windows Task Scheduler |
| [07 — Installer script explained](07-install-service-script.md) | `scripts/install_service.sh`, line-by-line behavior and unit settings |
| [08 — Recovery & troubleshooting](08-recovery.md) | Network failures, BytePlus pauses, power loss and safe recovery |

## Quick start

From the repository root in **Ubuntu WSL**:

```bash
# First-time Python setup
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env  # Do this once; preserve an existing .env

# Offline regression tests (no Tiki requests)
.venv/bin/python -m unittest discover -s tests -v

# Manual run — only when the systemd ETL service is stopped
.venv/bin/python -u main.py
```

For the **already installed service** used in this project:

```bash
sudo systemctl status de-project-2-etl.service --no-pager
sudo journalctl -u de-project-2-etl.service -f -o cat
```

> **Service-name warning:** The supplied installer currently hardcodes `tiki-etl.service`, whereas the service running on the user's WSL machine is named `de-project-2-etl.service`. Do **not** execute the installer blindly on an already configured machine: it would install a **different service**. See [Service setup](06-service-setup.md).

## Operating principles

- Maintain the approved server-side access limits; `REQUEST_DELAY=1` is an observed configuration, **not** a published Tiki API allowance.
- HTTP 404 is recorded as a failed ID. Temporary network/server failures retry with bounded backoff; exhausted retries preserve the ID for a future run.
- Security challenges cause a bounded pause/retry, then an intentional stop if the challenge persists. Do not treat a security challenge as a missing product.
- Checkpoint files and JSON outputs are local runtime state: do **not** delete them during deployment.
- Automated tests use mocks and temporary directories. A passing test suite does not prove that live API access, WSL uptime or a reboot will always succeed.

Start with [Project structure](01-project-structure.md) and [Pipeline flow](02-pipeline-flow.md) if you are new to the codebase.
