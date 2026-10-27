"""Process entry point for the ETL service and manual runs.

Exit 0: finished, Ctrl+C, or paused by an access/security challenge.
Exit 75: temporary network/server failure; supervisor may restart later.
Exit 78: permanent configuration/state problem; fix before restarting.
Exit 143: SIGTERM; progress saved, supervisor may restart unless explicitly stopped.
"""
import signal
import sys

from app.config import load_settings
from app.pipeline import PipelineResult, run_pipeline
from app.runtime import AlreadyRunningError, pipeline_lock

_sigterm_requested = False


def _handle_sigterm(signum, frame):
    """Reuse pipeline's Ctrl+C handling to checkpoint before exiting."""
    global _sigterm_requested
    _sigterm_requested = True
    raise KeyboardInterrupt


def main() -> int:
    signal.signal(signal.SIGTERM, _handle_sigterm)

    try:
        settings = load_settings()
        with pipeline_lock(settings.root):
            outcome = run_pipeline(settings)

    except AlreadyRunningError as exc:
        print(str(exc), file=sys.stderr, flush=True)
        return 78
    except (ValueError, FileNotFoundError, KeyError) as exc:
        print(f"Fatal configuration/checkpoint/data error: {exc}",
              file=sys.stderr, flush=True)
        return 78

    if _sigterm_requested:
        return 143
    if outcome == PipelineResult.TRANSIENT_EXHAUSTED:
        return 75
    # BytePlus pause is an intentional STOP: don't auto-retry indefinitely.
    return 0


if __name__ == "__main__":
    sys.exit(main())
