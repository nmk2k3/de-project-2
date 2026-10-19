"""Process entry point for the ETL service and manual runs.

Exit 0: finished, Ctrl+C, or paused by an access/security challenge.
Exit 75: temporary network/server failure; supervisor may restart later.
Exit 78: permanent configuration/state problem; fix before restarting.
Exit 143: SIGTERM; progress saved, supervisor may restart unless explicitly stopped.
"""
import fcntl
import signal
import sys

from app.config import load_settings
from app.pipeline import PipelineResult, run_pipeline

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
        # flock locks a file descriptor, not the filename; auto-releases on
        # a crash, power loss or SIGKILL. Prevent parallel manual/service runs.
        with (settings.root / ".pipeline.lock").open("a+") as lock_file:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                print("Another pipeline instance is already running. Exiting.",
                      file=sys.stderr, flush=True)
                return 78

            outcome = run_pipeline(settings)

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
