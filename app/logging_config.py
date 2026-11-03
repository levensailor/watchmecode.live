"""Root logging: one rotating file handler and one stdout handler, EST timestamps."""

from __future__ import annotations

import logging
import os
import sys
import tempfile
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from zoneinfo import ZoneInfo

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(filename)s:%(lineno)d %(funcName)s - %(message)s"
LOG_FILENAME = "app.log"
EASTERN = ZoneInfo("America/New_York")


class EstFormatter(logging.Formatter):
    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        return datetime.fromtimestamp(record.created, EASTERN).isoformat(timespec="milliseconds")


def _writable_log_dir(preferred: str) -> Path:
    for candidate in (Path(preferred), Path(tempfile.gettempdir()) / "logs"):
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            if os.access(candidate, os.W_OK):
                return candidate
        except OSError:
            continue
    raise RuntimeError("No writable log directory available")


def setup_logging(log_dir: str) -> Path:
    """Attach exactly two INFO handlers to the root logger and return the log file path."""
    directory = _writable_log_dir(log_dir)
    log_path = directory / LOG_FILENAME
    formatter = EstFormatter(LOG_FORMAT)

    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_watchmecode", False):
            root.removeHandler(handler)
            handler.close()

    file_handler = RotatingFileHandler(log_path, maxBytes=1_048_576, backupCount=3, encoding="utf-8")
    stream_handler = logging.StreamHandler(sys.stdout)
    for handler in (file_handler, stream_handler):
        handler.setLevel(logging.INFO)
        handler.setFormatter(formatter)
        handler._watchmecode = True  # type: ignore[attr-defined]
        root.addHandler(handler)

    root.setLevel(logging.INFO)
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return log_path
