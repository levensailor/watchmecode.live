"""Storage behind one repository: SQLite locally, Postgres (Supabase) in production."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.collab import ChangeSetError, apply_changes, normalize_newlines, replacement, utf16_length
from app.config import PROJECT_ROOT, Settings
from app.pins import generate_pin

logger = logging.getLogger(__name__)

SQL_DIR = PROJECT_ROOT / "sql"
LIBPQ_QUERY_PARAMS = {"sslmode", "sslrootcert", "connect_timeout", "application_name", "options", "target_session_attrs"}
PIN_ATTEMPTS = 25
SCHEMA_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
SESSION_EXTRA_COLUMNS: tuple[tuple[str, str], ...] = (
    ("org_id", "TEXT"),
    ("mode", "TEXT NOT NULL DEFAULT 'live'"),
    ("title", "TEXT"),
    ("candidate_email", "TEXT"),
    ("time_limit_minutes", "INTEGER"),
    ("started_at", "TEXT"),
    ("submitted_at", "TEXT"),
)
SESSION_COLUMNS = "id, pin, candidate_name, interviewer_email, language, status, created_at, updated_at, " + ", ".join(
    name for name, _ in SESSION_EXTRA_COLUMNS
)
SESSION_UPDATABLE = {"started_at", "submitted_at", "title", "candidate_email"}
ORG_COLUMNS = "id, slug, name, created_by, created_at"
API_KEY_COLUMNS = "id, org_id, name, prefix, created_by, created_at, last_used_at, revoked_at"
INTEGRATION_COLUMNS = (
    "org_id, provider, credentials, webhook_secret, settings, enabled, created_at, updated_at, "
    "last_event_at, last_synced_at, last_error"
)
LINK_COLUMNS = (
    "id, org_id, session_id, provider, external_type, external_id, parent_id, application_id, candidate_id, "
    "data, created_at, updated_at"
)
TEMPLATE_COLUMNS = "id, org_id, name, description, language, files, time_limit_minutes, created_by, created_at, updated_at"
PROBLEM_COLUMNS = "slug, language, name, summary, files, notes, model, created_by, created_at"
SLUG_CHARS = re.compile(r"[^a-z0-9]+")
RUN_COLUMNS = "id, entry_path, command, stdout, stderr, exit_code, duration_ms, created_at"
FILE_COLUMNS = "id, path, content, updated_at, version"
COLLAB_HISTORY = 1000
COLLAB_MAX_UNITS = 500_000
RECORDING_COLUMNS = "id, session_id, created_by, started_at, ended_at, start_cursor, end_cursor, initial_files"
PLAYBACK_KINDS_SQL = "'file_upsert', 'file_delete', 'file_rename', 'run'"


class PinExhaustedError(RuntimeError):
    pass


class RecordingConflictError(RuntimeError):
    pass


class PathConflictError(ValueError):
    pass


class CollabConflictError(RuntimeError):
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
        if not SCHEMA_NAME.match(settings.db_schema):
            raise ValueError(f"Invalid DB_SCHEMA: {settings.db_schema!r}")
        self._schema = settings.db_schema
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

    def _search_path_sql(self) -> str:
        """Transaction-scoped so pooled (transaction-mode) connections never leak the setting."""
        return f'SET LOCAL search_path TO "{self._schema}"'

    @contextmanager
    def connect(self) -> Iterator[_Conn]:
        if self.postgres:
            import psycopg
            from psycopg.rows import dict_row

            with psycopg.connect(self._pg_url, row_factory=dict_row, prepare_threshold=None, connect_timeout=10) as raw:
                raw.execute(self._search_path_sql())
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

    SQLITE_SCRIPT = "schema.sql"
    POSTGRES_SCRIPT = "schema_postgres.sql"

    @classmethod
    def schema_version(cls) -> str:
        """Fingerprint of the schema scripts; tenants re-run init_schema when it changes."""
        digest = hashlib.sha256()
        for name in (cls.SQLITE_SCRIPT, cls.POSTGRES_SCRIPT):
            digest.update((SQL_DIR / name).read_bytes())
        return digest.hexdigest()[:16]

    @property
    def schema(self) -> str:
        return self._schema

    def init_schema(self) -> None:
        script = (SQL_DIR / (self.POSTGRES_SCRIPT if self.postgres else self.SQLITE_SCRIPT)).read_text()
        if self.postgres:
            with self.connect() as conn:
                conn.raw.execute(f'CREATE SCHEMA IF NOT EXISTS "{self._schema}"')
                conn.raw.execute(self._search_path_sql())
                conn.raw.execute(script)
        else:
            raw = sqlite3.connect(self._sqlite_path)
            try:
                raw.execute("PRAGMA journal_mode = WAL")
                raw.executescript(script)
                self._sqlite_upgrade(raw)
                raw.commit()
            finally:
                raw.close()
        logger.info("Schema ready (%s, %s)", self.kind, self._schema if self.postgres else self._sqlite_path)

    def _sqlite_upgrade(self, raw: sqlite3.Connection) -> None:
        existing = {row[1] for row in raw.execute("PRAGMA table_info(sessions)")}
        for name, ddl in SESSION_EXTRA_COLUMNS:
            if name not in existing:
                raw.execute(f"ALTER TABLE sessions ADD COLUMN {name} {ddl}")
        raw.execute("CREATE INDEX IF NOT EXISTS sessions_org ON sessions (org_id, created_at)")
        if "version" not in {row[1] for row in raw.execute("PRAGMA table_info(files)")}:
            raw.execute("ALTER TABLE files ADD COLUMN version INTEGER NOT NULL DEFAULT 0")

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
        *,
        org_id: str | None = None,
        mode: str = "live",
        title: str | None = None,
        candidate_email: str | None = None,
        time_limit_minutes: int | None = None,
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
                        "INSERT INTO sessions (id, pin, candidate_name, interviewer_email, language, created_at, updated_at, "
                        "status, org_id, mode, title, candidate_email, time_limit_minutes) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?)",
                        (
                            session_id, pin, candidate_name, interviewer_email, language, now, now,
                            org_id, mode, title, candidate_email, time_limit_minutes,
                        ),
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

    def list_sessions(
        self, interviewer_email: str, candidate_name: str | None = None, org_id: str | None = None
    ) -> list[dict[str, Any]]:
        sql = (
            f"SELECT {SESSION_COLUMNS} FROM sessions WHERE (interviewer_email = ? "
            "OR org_id IN (SELECT org_id FROM org_members WHERE email = ?))"
        )
        params: list[Any] = [interviewer_email, interviewer_email]
        if org_id:
            sql += " AND org_id = ?"
            params.append(org_id)
        if candidate_name:
            sql += " AND LOWER(candidate_name) LIKE ?"
            params.append(f"%{candidate_name.lower()}%")
        sql += " ORDER BY LOWER(candidate_name), created_at DESC"
        with self.connect() as conn:
            return conn.all(sql, params)

    def list_org_sessions(self, org_id: str, limit: int = 25) -> list[dict[str, Any]]:
        with self.connect() as conn:
            return conn.all(
                f"SELECT {SESSION_COLUMNS} FROM sessions WHERE org_id = ? ORDER BY created_at DESC LIMIT ?", (org_id, limit)
            )

    def can_access_session(self, session: dict[str, Any], email: str) -> bool:
        if session["interviewer_email"] == email:
            return True
        return bool(session.get("org_id")) and self.member_role(session["org_id"], email) is not None

    def update_session_fields(self, session_id: str, **fields: Any) -> dict[str, Any] | None:
        unknown = set(fields) - SESSION_UPDATABLE
        if unknown:
            raise ValueError(f"Fields not updatable: {sorted(unknown)}")
        if fields:
            assignments = ", ".join(f"{name} = ?" for name in fields)
            with self.connect() as conn:
                conn.run(f"UPDATE sessions SET {assignments} WHERE id = ?", (*fields.values(), session_id))
        return self.get_session(session_id)

    def set_status(self, session_id: str, status: str, actor: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            if status == "ended":
                active = self._active_recording(conn, session_id)
                if active:
                    self._stop_recording(conn, session_id, active["id"], actor)
            conn.run("UPDATE sessions SET status = ? WHERE id = ?", (status, session_id))
            self._event(conn, session_id, "session", actor, None, data={"status": status})
        return self.get_session(session_id)

    # Recordings -------------------------------------------------------------

    def _recording_row(self, conn: _Conn, row: dict[str, Any]) -> dict[str, Any]:
        end = row["end_cursor"] if row["end_cursor"] is not None else self.latest_cursor(row["session_id"], conn)
        counted = conn.one(
            f"SELECT COUNT(*) AS n FROM events WHERE session_id = ? AND id > ? AND id <= ? AND kind IN ({PLAYBACK_KINDS_SQL})",
            (row["session_id"], row["start_cursor"], end),
        )
        return {
            "id": row["id"],
            "session_id": row["session_id"],
            "created_by": row["created_by"],
            "started_at": row["started_at"],
            "ended_at": row["ended_at"],
            "event_count": int(counted["n"]) if counted else 0,
        }

    def _active_recording(self, conn: _Conn, session_id: str) -> dict[str, Any] | None:
        return conn.one(
            f"SELECT {RECORDING_COLUMNS} FROM recordings WHERE session_id = ? AND ended_at IS NULL "
            "ORDER BY started_at DESC LIMIT 1",
            (session_id,),
        )

    def active_recording(self, session_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = self._active_recording(conn, session_id)
            return self._recording_row(conn, row) if row else None

    def start_recording(self, session_id: str, actor: str) -> dict[str, Any]:
        with self.connect() as conn:
            if self._active_recording(conn, session_id):
                raise RecordingConflictError("A recording is already in progress")
            files = [{"path": f["path"], "content": f["content"]} for f in self.list_files(session_id, conn)]
            recording_id = uuid.uuid4().hex
            conn.run(
                "INSERT INTO recordings (id, session_id, created_by, started_at, start_cursor, initial_files) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (recording_id, session_id, actor, utcnow(), self.latest_cursor(session_id, conn), json.dumps(files)),
            )
            self._event(conn, session_id, "recording", actor, None, data={"state": "started", "id": recording_id})
            row = conn.one(f"SELECT {RECORDING_COLUMNS} FROM recordings WHERE id = ?", (recording_id,))
            assert row is not None
            logger.info("Recording %s started in session %s", recording_id, session_id)
            return self._recording_row(conn, row)

    def _stop_recording(self, conn: _Conn, session_id: str, recording_id: str, actor: str) -> None:
        conn.run(
            "UPDATE recordings SET ended_at = ?, end_cursor = ? WHERE id = ? AND session_id = ? AND ended_at IS NULL",
            (utcnow(), self.latest_cursor(session_id, conn), recording_id, session_id),
        )
        self._event(conn, session_id, "recording", actor, None, data={"state": "stopped", "id": recording_id})
        logger.info("Recording %s stopped in session %s", recording_id, session_id)

    def stop_recording(self, session_id: str, recording_id: str, actor: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.one(
                f"SELECT {RECORDING_COLUMNS} FROM recordings WHERE id = ? AND session_id = ?", (recording_id, session_id)
            )
            if not row:
                return None
            if row["ended_at"] is None:
                self._stop_recording(conn, session_id, recording_id, actor)
                row = conn.one(f"SELECT {RECORDING_COLUMNS} FROM recordings WHERE id = ?", (recording_id,))
            return self._recording_row(conn, row)

    def list_recordings(self, session_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.all(
                f"SELECT {RECORDING_COLUMNS} FROM recordings WHERE session_id = ? ORDER BY started_at DESC", (session_id,)
            )
            return [self._recording_row(conn, row) for row in rows]

    def get_playback(self, session_id: str, recording_id: str, limit: int = 50_000) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.one(
                f"SELECT {RECORDING_COLUMNS} FROM recordings WHERE id = ? AND session_id = ?", (recording_id, session_id)
            )
            if not row:
                return None
            end = row["end_cursor"] if row["end_cursor"] is not None else self.latest_cursor(session_id, conn)
            events = conn.all(
                "SELECT id, kind, path, data, actor, client_id, created_at FROM events "
                f"WHERE session_id = ? AND id > ? AND id <= ? AND kind IN ({PLAYBACK_KINDS_SQL}) ORDER BY id LIMIT ?",
                (session_id, row["start_cursor"], end, limit),
            )
            playback = self._recording_row(conn, row)
        for event in events:
            event["id"] = int(event["id"])
            event["data"] = json.loads(event["data"]) if event["data"] else None
        playback["initial_files"] = json.loads(row["initial_files"])
        playback["events"] = events
        return playback

    # Files ------------------------------------------------------------------

    def list_files(self, session_id: str, conn: _Conn | None = None) -> list[dict[str, Any]]:
        sql = f"SELECT {FILE_COLUMNS} FROM files WHERE session_id = ? ORDER BY path"
        if conn is not None:
            return conn.all(sql, (session_id,))
        with self.connect() as c:
            return c.all(sql, (session_id,))

    def upsert_file(self, session_id: str, path: str, content: str, actor: str, client_id: str | None) -> dict[str, Any]:
        """Whole-file write. Existing files get a logged replacement so collab editors can rebase onto it."""
        content = normalize_newlines(content)
        now = utcnow()
        with self.connect() as conn:
            existing = conn.one(
                f"SELECT {FILE_COLUMNS} FROM files WHERE session_id = ? AND path = ?", (session_id, path)
            )
            if existing is None:
                row = conn.one(
                    "INSERT INTO files (id, session_id, path, content, updated_at, version) VALUES (?, ?, ?, ?, ?, 0) "
                    f"RETURNING {FILE_COLUMNS}",
                    (uuid.uuid4().hex, session_id, path, content, now),
                )
            elif existing["content"] == content:
                row = existing
            else:
                version = int(existing["version"]) + 1
                changes = replacement(normalize_newlines(existing["content"]), content)
                updated = conn.run(
                    "UPDATE files SET content = ?, version = ?, updated_at = ? WHERE id = ? AND version = ?",
                    (content, version, now, existing["id"], existing["version"]),
                )
                if not updated:
                    raise CollabConflictError("The file changed while saving. Try again.")
                self._log_update(conn, session_id, path, version, client_id or "server", changes, now)
                row = {**existing, "content": content, "version": version, "updated_at": now}
            assert row is not None
            self._event(
                conn, session_id, "file_upsert", actor, client_id, path=path,
                data={"content": content, "version": int(row["version"])},
            )
        return row

    # Collaborative editing (CodeMirror collab) ----------------------------------

    def _log_update(
        self, conn: _Conn, session_id: str, path: str, version: int, client_id: str, changes: Any, now: str
    ) -> None:
        conn.run(
            "INSERT INTO doc_updates (session_id, path, version, client_id, changes, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, path, version, client_id, json.dumps(changes, separators=(",", ":")), now),
        )
        conn.run(
            "DELETE FROM doc_updates WHERE session_id = ? AND path = ? AND version <= ?",
            (session_id, path, version - COLLAB_HISTORY),
        )

    def collab_push(
        self,
        session_id: str,
        path: str,
        base_version: int,
        updates: list[dict[str, Any]],
        actor: str,
        client_id: str | None,
    ) -> dict[str, Any]:
        """Apply `updates` made on top of `base_version`. Returns {"ok", "version"}; ok is False when the
        client is behind and must pull, rebase, and push again."""
        now = utcnow()
        with self.connect() as conn:
            row = conn.one(f"SELECT {FILE_COLUMNS} FROM files WHERE session_id = ? AND path = ?", (session_id, path))
            if row is None:
                raise FileNotFoundError(path)
            current = int(row["version"])
            if base_version != current:
                return {"ok": False, "version": current}
            content = normalize_newlines(row["content"])
            version = current
            for update in updates:
                content = apply_changes(content, update["changes"])
            if utf16_length(content) > COLLAB_MAX_UNITS:
                raise ChangeSetError("The file is too large")
            try:
                for update in updates:
                    version += 1
                    self._log_update(conn, session_id, path, version, str(update["clientID"]), update["changes"], now)
                updated = conn.run(
                    "UPDATE files SET content = ?, version = ?, updated_at = ? WHERE id = ? AND version = ?",
                    (content, version, now, row["id"], current),
                )
            except self._integrity_errors:
                updated = 0
            if not updated:
                # Another push won the race for this version; the client pulls, rebases, and retries.
                conn.raw.rollback()
                return {"ok": False, "version": current + 1}
            self._event(
                conn, session_id, "file_upsert", actor, client_id, path=path,
                data={"content": content, "version": version},
            )
        return {"ok": True, "version": version}

    def collab_pull(self, session_id: str, path: str, since: int, limit: int = 500) -> dict[str, Any]:
        """Updates after `since`, or the whole document when the client is too far behind to catch up."""
        with self.connect() as conn:
            row = conn.one(f"SELECT {FILE_COLUMNS} FROM files WHERE session_id = ? AND path = ?", (session_id, path))
            if row is None:
                raise FileNotFoundError(path)
            current = int(row["version"])
            # Bounded by `current` so a push committing between these two reads can't leave a gap.
            rows = conn.all(
                "SELECT version, client_id, changes FROM doc_updates WHERE session_id = ? AND path = ? "
                "AND version > ? AND version <= ? ORDER BY version LIMIT ?",
                (session_id, path, since, current, limit + 1),
            ) if 0 <= since <= current else []
        contiguous = len(rows) == current - since and (not rows or int(rows[0]["version"]) == since + 1)
        if since > current or since < 0 or not contiguous or len(rows) > limit:
            return {"version": current, "updates": [], "resync": True, "content": normalize_newlines(row["content"])}
        return {
            "version": current,
            "updates": [
                {"version": int(r["version"]), "clientID": r["client_id"], "changes": json.loads(r["changes"])}
                for r in rows
            ],
            "resync": False,
        }

    @staticmethod
    def _matching(files: list[dict[str, Any]], path: str) -> list[str]:
        prefix = path.rstrip("/") + "/"
        return [f["path"] for f in files if f["path"] == path or f["path"].startswith(prefix)]

    def delete_path(self, session_id: str, path: str, actor: str, client_id: str | None) -> list[str]:
        with self.connect() as conn:
            targets = self._matching(self.list_files(session_id, conn), path)
            for target in targets:
                conn.run("DELETE FROM files WHERE session_id = ? AND path = ?", (session_id, target))
                conn.run("DELETE FROM doc_updates WHERE session_id = ? AND path = ?", (session_id, target))
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
                conn.run("UPDATE doc_updates SET path = ? WHERE session_id = ? AND path = ?", (dest, session_id, src))
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

    def run_count(self, session_id: str) -> int:
        with self.connect() as conn:
            row = conn.one("SELECT COUNT(*) AS n FROM runs WHERE session_id = ?", (session_id,))
        return int(row["n"]) if row else 0

    # Organizations ----------------------------------------------------------

    def create_org(self, name: str, creator_email: str) -> dict[str, Any]:
        base = SLUG_CHARS.sub("-", name.lower()).strip("-")[:40] or "org"
        now = utcnow()
        org_id = uuid.uuid4().hex
        for attempt in range(PIN_ATTEMPTS):
            slug = base if attempt == 0 else f"{base}-{uuid.uuid4().hex[:6]}"
            try:
                with self.connect() as conn:
                    conn.run(
                        "INSERT INTO organizations (id, slug, name, created_by, created_at) VALUES (?, ?, ?, ?, ?)",
                        (org_id, slug, name, creator_email, now),
                    )
                    conn.run(
                        "INSERT INTO org_members (org_id, email, role, created_at) VALUES (?, ?, 'admin', ?)",
                        (org_id, creator_email, now),
                    )
            except self._integrity_errors:
                continue
            logger.info("Created organization %s (%s)", org_id, slug)
            org = self.get_org(org_id)
            assert org is not None
            return org
        raise RuntimeError("Could not allocate an organization slug")

    def get_org(self, org_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            return conn.one(f"SELECT {ORG_COLUMNS} FROM organizations WHERE id = ?", (org_id,))

    def get_org_by_slug(self, slug: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            return conn.one(f"SELECT {ORG_COLUMNS} FROM organizations WHERE slug = ?", (slug,))

    def list_orgs_for(self, email: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            return conn.all(
                "SELECT o.id, o.slug, o.name, o.created_by, o.created_at, m.role FROM organizations o "
                "JOIN org_members m ON m.org_id = o.id WHERE m.email = ? ORDER BY LOWER(o.name)",
                (email,),
            )

    def member_role(self, org_id: str, email: str) -> str | None:
        with self.connect() as conn:
            row = conn.one("SELECT role FROM org_members WHERE org_id = ? AND email = ?", (org_id, email))
        return row["role"] if row else None

    def list_members(self, org_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            return conn.all(
                "SELECT email, role, created_at FROM org_members WHERE org_id = ? ORDER BY role, email", (org_id,)
            )

    def upsert_member(self, org_id: str, email: str, role: str) -> dict[str, Any]:
        with self.connect() as conn:
            conn.run(
                "INSERT INTO org_members (org_id, email, role, created_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (org_id, email) DO UPDATE SET role = excluded.role",
                (org_id, email, role, utcnow()),
            )
            row = conn.one("SELECT email, role, created_at FROM org_members WHERE org_id = ? AND email = ?", (org_id, email))
        assert row is not None
        return row

    def remove_member(self, org_id: str, email: str) -> bool:
        with self.connect() as conn:
            return conn.run("DELETE FROM org_members WHERE org_id = ? AND email = ?", (org_id, email)) > 0

    def admin_count(self, org_id: str) -> int:
        with self.connect() as conn:
            row = conn.one("SELECT COUNT(*) AS n FROM org_members WHERE org_id = ? AND role = 'admin'", (org_id,))
        return int(row["n"]) if row else 0

    # API keys ---------------------------------------------------------------

    def create_api_key(self, org_id: str, name: str, prefix: str, key_hash: str, created_by: str) -> dict[str, Any]:
        key_id = uuid.uuid4().hex
        with self.connect() as conn:
            conn.run(
                "INSERT INTO api_keys (id, org_id, name, prefix, key_hash, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (key_id, org_id, name, prefix, key_hash, created_by, utcnow()),
            )
            row = conn.one(f"SELECT {API_KEY_COLUMNS} FROM api_keys WHERE id = ?", (key_id,))
        assert row is not None
        return row

    def list_api_keys(self, org_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            return conn.all(
                f"SELECT {API_KEY_COLUMNS} FROM api_keys WHERE org_id = ? AND revoked_at IS NULL ORDER BY created_at DESC",
                (org_id,),
            )

    def revoke_api_key(self, org_id: str, key_id: str) -> bool:
        with self.connect() as conn:
            return (
                conn.run(
                    "UPDATE api_keys SET revoked_at = ? WHERE id = ? AND org_id = ? AND revoked_at IS NULL",
                    (utcnow(), key_id, org_id),
                )
                > 0
            )

    def org_for_api_key_hash(self, key_hash: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.one(
                "SELECT k.id AS key_id, k.created_by AS key_created_by, o.id, o.slug, o.name, o.created_by, o.created_at "
                "FROM api_keys k JOIN organizations o ON o.id = k.org_id WHERE k.key_hash = ? AND k.revoked_at IS NULL",
                (key_hash,),
            )
            if row:
                conn.run("UPDATE api_keys SET last_used_at = ? WHERE id = ?", (utcnow(), row["key_id"]))
        return row

    # Integrations -----------------------------------------------------------

    @staticmethod
    def _integration_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
        if row is None:
            return None
        row["settings"] = json.loads(row["settings"] or "{}")
        row["enabled"] = bool(row["enabled"])
        return row

    def get_integration(self, org_id: str, provider: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.one(
                f"SELECT {INTEGRATION_COLUMNS} FROM integrations WHERE org_id = ? AND provider = ?", (org_id, provider)
            )
        return self._integration_row(row)

    def list_integrations(self, org_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.all(f"SELECT {INTEGRATION_COLUMNS} FROM integrations WHERE org_id = ?", (org_id,))
        return [self._integration_row(r) for r in rows]

    def list_enabled_integrations(self, provider: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.all(
                f"SELECT {INTEGRATION_COLUMNS} FROM integrations WHERE provider = ? AND enabled = 1", (provider,)
            )
        return [self._integration_row(r) for r in rows]

    def upsert_integration(
        self,
        org_id: str,
        provider: str,
        credentials: str,
        webhook_secret: str | None,
        settings: dict[str, Any],
        enabled: bool = True,
    ) -> dict[str, Any]:
        now = utcnow()
        with self.connect() as conn:
            conn.run(
                "INSERT INTO integrations (org_id, provider, credentials, webhook_secret, settings, enabled, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (org_id, provider) DO UPDATE SET "
                "credentials = excluded.credentials, webhook_secret = excluded.webhook_secret, "
                "settings = excluded.settings, enabled = excluded.enabled, updated_at = excluded.updated_at, last_error = NULL",
                (org_id, provider, credentials, webhook_secret, json.dumps(settings), 1 if enabled else 0, now, now),
            )
        result = self.get_integration(org_id, provider)
        assert result is not None
        return result

    def delete_integration(self, org_id: str, provider: str) -> bool:
        with self.connect() as conn:
            return conn.run("DELETE FROM integrations WHERE org_id = ? AND provider = ?", (org_id, provider)) > 0

    def mark_integration(self, org_id: str, provider: str, **fields: str | None) -> None:
        allowed = {"last_event_at", "last_synced_at", "last_error"}
        if not fields or set(fields) - allowed:
            raise ValueError("Unsupported integration status fields")
        assignments = ", ".join(f"{name} = ?" for name in fields)
        with self.connect() as conn:
            conn.run(
                f"UPDATE integrations SET {assignments} WHERE org_id = ? AND provider = ?",
                (*fields.values(), org_id, provider),
            )

    # External links ---------------------------------------------------------

    @staticmethod
    def _link_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
        if row is not None:
            row["data"] = json.loads(row["data"] or "{}")
        return row

    def add_link(
        self,
        org_id: str,
        session_id: str,
        provider: str,
        external_type: str,
        external_id: str,
        *,
        parent_id: str | None = None,
        application_id: str | None = None,
        candidate_id: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        link_id = uuid.uuid4().hex
        now = utcnow()
        with self.connect() as conn:
            conn.run(
                f"INSERT INTO external_links ({LINK_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    link_id, org_id, session_id, provider, external_type, external_id, parent_id,
                    application_id, candidate_id, json.dumps(data or {}), now, now,
                ),
            )
        link = self.get_link_by_id(link_id)
        assert link is not None
        return link

    def get_link_by_id(self, link_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            return self._link_row(conn.one(f"SELECT {LINK_COLUMNS} FROM external_links WHERE id = ?", (link_id,)))

    def get_link(self, org_id: str, provider: str, external_type: str, external_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.one(
                f"SELECT {LINK_COLUMNS} FROM external_links WHERE org_id = ? AND provider = ? AND external_type = ? "
                "AND external_id = ?",
                (org_id, provider, external_type, external_id),
            )
        return self._link_row(row)

    def links_for_session(self, session_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.all(f"SELECT {LINK_COLUMNS} FROM external_links WHERE session_id = ?", (session_id,))
        return [self._link_row(r) for r in rows]

    def links_for_parent(self, org_id: str, provider: str, parent_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.all(
                f"SELECT {LINK_COLUMNS} FROM external_links WHERE org_id = ? AND provider = ? AND parent_id = ?",
                (org_id, provider, parent_id),
            )
        return [self._link_row(r) for r in rows]

    def links_for_candidate(self, org_id: str, provider: str, candidate_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.all(
                f"SELECT {LINK_COLUMNS} FROM external_links WHERE org_id = ? AND provider = ? AND candidate_id = ?",
                (org_id, provider, candidate_id),
            )
        return [self._link_row(r) for r in rows]

    def update_link_data(self, link_id: str, data: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.run(
                "UPDATE external_links SET data = ?, updated_at = ? WHERE id = ?", (json.dumps(data), utcnow(), link_id)
            )

    # Assessment templates -----------------------------------------------------

    @staticmethod
    def _template_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
        if row is not None:
            row["files"] = json.loads(row["files"] or "{}")
        return row

    def list_templates(self, org_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.all(
                f"SELECT {TEMPLATE_COLUMNS} FROM assessment_templates WHERE org_id = ? ORDER BY LOWER(name)", (org_id,)
            )
        return [self._template_row(r) for r in rows]

    def get_template(self, org_id: str, template_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.one(
                f"SELECT {TEMPLATE_COLUMNS} FROM assessment_templates WHERE org_id = ? AND id = ?", (org_id, template_id)
            )
        return self._template_row(row)

    def save_template(
        self,
        org_id: str,
        name: str,
        description: str,
        language: str,
        files: dict[str, str],
        time_limit_minutes: int | None,
        actor: str,
        template_id: str | None = None,
    ) -> dict[str, Any]:
        now = utcnow()
        with self.connect() as conn:
            if template_id:
                conn.run(
                    "UPDATE assessment_templates SET name = ?, description = ?, language = ?, files = ?, "
                    "time_limit_minutes = ?, updated_at = ? WHERE id = ? AND org_id = ?",
                    (name, description, language, json.dumps(files), time_limit_minutes, now, template_id, org_id),
                )
            else:
                template_id = uuid.uuid4().hex
                conn.run(
                    f"INSERT INTO assessment_templates ({TEMPLATE_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (template_id, org_id, name, description, language, json.dumps(files), time_limit_minutes, actor, now, now),
                )
        template = self.get_template(org_id, template_id)
        assert template is not None
        return template

    def delete_template(self, org_id: str, template_id: str) -> bool:
        with self.connect() as conn:
            return conn.run("DELETE FROM assessment_templates WHERE id = ? AND org_id = ?", (template_id, org_id)) > 0

    # Company problem library ---------------------------------------------------

    def get_cached_problem(self, slug: str, language: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.one(f"SELECT {PROBLEM_COLUMNS} FROM problem_cache WHERE slug = ? AND language = ?", (slug, language))
        if row is not None:
            row["files"] = json.loads(row["files"] or "{}")
            row["notes"] = json.loads(row["notes"] or "{}")
        return row

    def save_cached_problem(
        self,
        slug: str,
        language: str,
        name: str,
        summary: str,
        files: dict[str, str],
        notes: dict[str, Any],
        model: str,
        actor: str,
    ) -> dict[str, Any]:
        """Keeps the first copy when two interviewers generate the same problem at once."""
        with self.connect() as conn:
            conn.run(
                f"INSERT INTO problem_cache ({PROBLEM_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (slug, language) DO NOTHING",
                (slug, language, name, summary, json.dumps(files), json.dumps(notes), model, actor, utcnow()),
            )
        cached = self.get_cached_problem(slug, language)
        assert cached is not None
        return cached

    def count_problem_generations(self, actor: str, since: str) -> int:
        with self.connect() as conn:
            row = conn.one(
                "SELECT COUNT(*) AS n FROM problem_cache WHERE created_by = ? AND created_at >= ?", (actor, since)
            )
        return int(row["n"]) if row else 0
