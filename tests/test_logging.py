import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app

LINE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}-0[45]:00 \[INFO\] main\.py:\d+ log_requests - GET /api/health -> 200"
)


def test_request_is_logged_to_file_and_stdout(capsys, settings):
    client = TestClient(create_app(settings))
    client.get("/api/health")
    log_file = Path(settings.log_dir) / "app.log"
    file_lines = [line for line in log_file.read_text().splitlines() if "/api/health" in line]
    assert file_lines and LINE.match(file_lines[-1]), file_lines
    stdout_lines = [line for line in capsys.readouterr().out.splitlines() if "/api/health" in line]
    assert stdout_lines and LINE.match(stdout_lines[-1]), stdout_lines


def test_root_logger_has_rotating_file_and_stdout_handlers(app):
    ours = [h for h in logging.getLogger().handlers if getattr(h, "_watchmecode", False)]
    assert len(ours) == 2
    rotating = next(h for h in ours if isinstance(h, RotatingFileHandler))
    assert rotating.maxBytes == 1_048_576
    assert rotating.backupCount == 3
    assert all(h.level == logging.INFO for h in ours)
