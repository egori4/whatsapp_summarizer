"""Application logging for WhatsApp Technical Digest v2."""
from __future__ import annotations

import logging
from logging.handlers import TimedRotatingFileHandler
import os
from pathlib import Path


def setup_logging(log_path: str | Path, backup_days: int) -> None:
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass

    if not path.exists():
        fd = os.open(path, os.O_WRONLY | os.O_CREAT, 0o600)
        os.close(fd)
    try:
        path.chmod(0o600)
    except OSError:
        pass

    root = logging.getLogger()
    marker = str(path.resolve())
    for handler in root.handlers:
        if getattr(handler, "_digest_log_path", None) == marker:
            return

    handler = TimedRotatingFileHandler(
        path,
        when="midnight",
        backupCount=max(0, int(backup_days)),
        encoding="utf-8",
        utc=True,
    )
    handler._digest_log_path = marker  # type: ignore[attr-defined]
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s"
    ))
    root.addHandler(handler)
    if root.level == logging.NOTSET or root.level > logging.INFO:
        root.setLevel(logging.INFO)
