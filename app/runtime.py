"""Single-instance coordination for manual and service runs."""
from contextlib import contextmanager
import fcntl
from pathlib import Path
from typing import Iterator


class AlreadyRunningError(Exception):
    """Another process holds the pipeline lock."""


@contextmanager
def pipeline_lock(root: Path) -> Iterator[None]:
    # flock follows the descriptor and auto-releases on crashes or SIGKILL.
    with (root / ".pipeline.lock").open("a+") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise AlreadyRunningError(
                "Another pipeline instance is already running. Exiting."
            ) from error
        yield
