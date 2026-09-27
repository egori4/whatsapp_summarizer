"""Per-workflow local process locking."""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
from typing import Iterator


class WorkflowBusyError(RuntimeError):
    pass


@contextmanager
def workflow_lock(database_path: str | Path, workflow_id: str) -> Iterator[Path]:
    root = Path(database_path).parent / "locks"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        root.chmod(0o700)
    except OSError:
        pass

    path = root / f"{workflow_id}.lock"
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            os.fchmod(fd, 0o600)
        except OSError:
            pass
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise WorkflowBusyError(
                f"workflow '{workflow_id}' is already running"
            ) from exc
        yield path
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
