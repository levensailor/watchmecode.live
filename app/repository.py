"""Storage behind one repository: SQLite locally, Postgres (Supabase) in production."""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.config import PROJECT_ROOT, Settings
from app.pins import generate_pin

logger = logging.getLogger(__name__)

SQL_DIR = PROJECT_ROOT / "sql"
LIBPQ_QUERY_PARAMS = {"sslmode", "sslrootcert", "connect_timeout", "application_name", "options", "target_session_attrs"}
PIN_ATTEMPTS = 25
SESSION_COLUMNS = "id, pin, candidate_name, interviewer_email, language, status, created_at, updated_at"
RUN_COLUMNS = "id, entry_path, command, stdout, stderr, exit_code, duration_ms, created_at"
FILE_COLUMNS = "id, path, content, updated_at"


class PinExhaustedError(RuntimeError):
    pass


class PathConflictError(ValueError):
    pass


class Cursor(Protocol):
    def execute(self, sql: str, params: tuple | list = ()) -> Any: ...
    def fetchone(self) -> Any: ...
    def fetchall(self) -> list[Any]: ...


def utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def sanitize_postgres_url(url: str) -> str:
    """Drop query parameters libpq rejects (e.g. Supabase's `supa=` marker)."""
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query) if k in LIBPQ_QUERY_PARAMS]
    return urlunsplit(parts._replace(query=urlencode(query)))


class _Conn:
    """Thin wrapper that normalizes placeholders and rows across drivers."""

    def __init__(self, raw: Any, postgres: bool) -> None:
        self.raw = raw
        self.postgres = postgres

    def _sql(self, sql: str) -> str:
        return sql.replace("?", "%s") if self.postgres else sql

    def all(self, sql: str, params: tuple | list = ()) -> list[dict[str, Any]]:
        cur = self.raw.execute(self._sql(sql), params)
        return [dict(row) for row in cur.fetchall()]

    def one(self, sql: str, params: tuple | list = ()) -> dict[str, Any] | None:
        cur = self.raw.execute(self._sql(sql), params)
        row = cur.fetchone()
        return dict(row) if row is not None else None

    def run(self, sql: str, params: tuple | list = ()) -> int:
        cur = self.raw.execute(self._sql(sql), params)
        return cur.rowcount


class Repository:
    def __init__(self, settings: Settings) -> None:
        self.postgres = settings.uses_postgres
        self._pg_url = sanitize_postgres_url(settings.database_url) if self.postgres else ""
        self._sqlite_path = settings.sqlite_path
        if self.postgres:
            import psycopg  # noqa: F401  (fail fast if the driver is missing)

            self._integrity_errors: tuple[type[Exception], ...] = (psycopg.IntegrityError,)
        else:
            Path(self._sqlite_path).parent.mkdir(parents=True, exist_ok=True)
            self._integrity_errors = (sqlite3.IntegrityError,)

    @property
    def kind(self) -> str:
        return "postgres" if self.postgres else "sqlite"

    @contextmanager
    def connect(self) -> Iterator[_Conn]:
        if self.postgres:
            import psycopg
            from psycopg.rows import dict_row

            with psycopg.connect(self._pg_url, row_factory=dict_row, prepare_threshold=None, connect_timeout=10) as raw:
                yield _Conn(raw, postgres=True)
        else:
            raw = sqlite3.connect(self._sqlite_path, timeout=10)
            raw.row_factory = sqlite3.Row
            raw.execute("PRAGMA foreign_keys = ON")
            try:
                with raw:
                    yield _Conn(raw, postgres=False)
            finally:
                raw.close()

    def init_schema(self) -> None:
        script = (SQL_DIR / ("schema_postgres.sql" if self.postgres else "schema.sql")).read_text()
        if self.postgres:
            with self.connect() as conn:
                conn.raw.execute(script)
        else:
            raw = sqlite3.connect(self._sqlite_path)
            try:
                raw.execute("PRAGMA journal_mode = WAL")
                raw.executescript(script)
            finally:
                raw.close()
        logger.info("Schema ready (%s)", self.kind)

    # Events -----------------------------------------------------------------

    def _event(
        self,
        conn: _Conn,
        session_id: str,
        kind: str,
        actor: str,
        client_id: str | None,
        path: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        now = utcnow()
        conn.run(
            "INSERT INTO events (session_id, kind, path, data, actor, client_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (session_id, kind, path, json.dumps(data) if data is not None else None, actor, client_id, now),
        )
        conn.run("UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id))

    def latest_cursor(self, session_id: str, conn: _Conn | None = None) -> int:
        def query(c: _Conn) -> int:
            row = c.one("SELECT COALESCE(MAX(id), 0) AS cursor FROM events WHERE session_id = ?", (session_id,))
            return int(row["cursor"]) if row else 0

        if conn is not None:
            return query(conn)
        with self.connect() as c:
            return query(c)

    def changes(self, session_id: str, since: int, limit: int = 500) -> tuple[int, list[dict[str, Any]]]:
        with self.connect() as conn:
            if since < 0:
                return self.latest_cursor(session_id, conn), []
            rows = conn.all(
                "SELECT id, kind, path, data, actor, client_id, created_at FROM events "
                "WHERE session_id = ? AND id > ? ORDER BY id LIMIT ?",
                (session_id, since, limit),
            )
        for row in rows:
            row["id"] = int(row["id"])
            row["data"] = json.loads(row["data"]) if row["data"] else None
        cursor = rows[-1]["id"] if rows else since
        return cursor, rows

    # Sessions ---------------------------------------------------------------

    def create_session(
        self,
        candidate_name: str,
        interviewer_email: str,
        language: str,
        files: dict[str, str],
        pin_factory: Callable[[], str] = generate_pin,
    ) -> dict[str, Any]:
        for attempt in range(1, PIN_ATTEMPTS + 1):
            pin = pin_factory()
            session_id = uuid.uuid4().hex
            now = utcnow()
            try:
                with self.connect() as conn:
                    taken = conn.one("SELECT 1 AS taken FROM sessions WHERE pin = ? AND status = 'active'", (pin,))
                    if taken:
                        logger.info("PIN collision on attempt %d; retrying", attempt)
                        continue
                    conn.run(
                        "INSERT INTO sessions (id, pin, candidate_name, interviewer_email, language, created_at, updated_at, status) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, 'active')",
                        (session_id, pin, candidate_name, interviewer_email, language, now, now),
                    )
                    for path, content in files.items():
                        conn.run(
                            "INSERT INTO files (id, session_id, path, content, updated_at) VALUES (?, ?, ?, ?, ?)",
                            (uuid.uuid4().hex, session_id, path, content, now),
                        )
            except self._integrity_errors:
                logger.info("PIN race on attempt %d; retrying", attempt)
                continue
            logger.info("Created session %s for candidate %r", session_id, candidate_name)
            session = self.get_session(session_id)
            assert session is not None
            return session
        raise PinExhaustedError("Could not allocate a unique PIN")

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            return conn.one(f"SELECT {SESSION_COLUMNS} FROM sessions WHERE id = ?", (session_id,))

    def get_active_session_by_pin(self, pin: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            return conn.one(f"SELECT {SESSION_COLUMNS} FROM sessions WHERE pin = ? AND status = 'active'", (pin,))

    def list_sessions(self, interviewer_email: str, candidate_name: str | None = None) -> list[dict[str, Any]]:
        sql = f"SELECT {SESSION_COLUMNS} FROM sessions WHERE interviewer_email = ?"
        params: list[Any] = [interviewer_email]
        if candidate_name:
            sql += " AND LOWER(candidate_name) LIKE ?"
            params.append(f"%{candidate_name.lower()}%")
        sql += " ORDER BY LOWER(candidate_name), created_at DESC"
        with self.connect() as conn:
            return conn.all(sql, params)

    def set_status(self, session_id: str, status: str, actor: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            conn.run("UPDATE sessions SET status = ? WHERE id = ?", (status, session_id))
            self._event(conn, session_id, "session", actor, None, data={"status": status})
        return self.get_session(session_id)

    # Files ------------------------------------------------------------------

    def list_files(self, session_id: str, conn: _Conn | None = None) -> list[dict[str, Any]]:
        sql = f"SELECT {FILE_COLUMNS} FROM files WHERE session_id = ? ORDER BY path"
        if conn is not None:
            return conn.all(sql, (session_id,))
        with self.connect() as c:
            return c.all(sql, (session_id,))

    def upsert_file(self, session_id: str, path: str, content: str, actor: str, client_id: str | None) -> dict[str, Any]:
        now = utcnow()
        with self.connect() as conn:
            row = conn.one(
                "INSERT INTO files (id, session_id, path, content, updated_at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT (session_id, path) DO UPDATE SET content = excluded.content, updated_at = excluded.updated_at "
                f"RETURNING {FILE_COLUMNS}",
                (uuid.uuid4().hex, session_id, path, content, now),
            )
            assert row is not None
            self._event(conn, session_id, "file_upsert", actor, client_id, path=path, data={"content": content})
        return row

    @staticmethod
    def _matching(files: list[dict[str, Any]], path: str) -> list[str]:
        prefix = path.rstrip("/") + "/"
        return [f["path"] for f in files if f["path"] == path or f["path"].startswith(prefix)]

    def delete_path(self, session_id: str, path: str, actor: str, client_id: str | None) -> list[str]:
        with self.connect() as conn:
            targets = self._matching(self.list_files(session_id, conn), path)
            for target in targets:
                conn.run("DELETE FROM files WHERE session_id = ? AND path = ?", (session_id, target))
            if targets:
                self._event(conn, session_id, "file_delete", actor, client_id, path=path, data={"paths": targets})
        return targets

    def rename_path(self, session_id: str, old: str, new: str, actor: str, client_id: str | None) -> dict[str, str]:
        with self.connect() as conn:
            files = self.list_files(session_id, conn)
            targets = self._matching(files, old)
            if not targets:
                return {}
            existing = {f["path"] for f in files} - set(targets)
            mapping = {t: new + t[len(old):] if t != old else new for t in targets}
            if any(dest in existing for dest in mapping.values()):
                raise PathConflictError("A file already exists at the destination")
            now = utcnow()
            for src, dest in mapping.items():
                conn.run(
                    "UPDATE files SET path = ?, updated_at = ? WHERE session_id = ? AND path = ?",
                    (dest, now, session_id, src),
                )
            self._event(conn, session_id, "file_rename", actor, client_id, path=old, data={"mapping": mapping})
        return mapping

    # Runs -------------------------------------------------------------------

    def add_run(self, session_id: str, result: dict[str, Any], actor: str, client_id: str | None) -> dict[str, Any]:
        now = utcnow()
        with self.connect() as conn:
            row = conn.one(
                "INSERT INTO runs (session_id, entry_path, command, stdout, stderr, exit_code, duration_ms, created_at) "
                f"VALUES (?, ?, ?, ?, ?, ?, ?, ?) RETURNING {RUN_COLUMNS}",
                (
                    session_id,
                    result["entry_path"],
                    result["command"],
                    result["stdout"],
                    result["stderr"],
                    result["exit_code"],
                    result["duration_ms"],
                    now,
                ),
            )
            assert row is not None
            row["id"] = int(row["id"])
            self._event(conn, session_id, "run", actor, client_id, path=result["entry_path"], data=row)
        return row

    def list_runs(self, session_id: str, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.all(
                f"SELECT {RUN_COLUMNS} FROM runs WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                (session_id, limit),
            )
        for row in rows:
            row["id"] = int(row["id"])
        return rows
