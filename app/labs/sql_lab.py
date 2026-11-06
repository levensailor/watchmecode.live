"""SQL lab runner: load every CSV in the workspace as a SQLite table, run a .sql file, print result tables.

Runs as a standalone script inside the session workspace (locally or in a sandbox):
    python sql_lab.py query.sql
"""

from __future__ import annotations

import csv
import re
import sqlite3
import sys
from pathlib import Path

MAX_ROWS = 200
MAX_CELL = 60
IDENTIFIER = re.compile(r"[^A-Za-z0-9_]+")


def identifier(name: str) -> str:
    cleaned = IDENTIFIER.sub("_", name).strip("_") or "col"
    return f"_{cleaned}" if cleaned[0].isdigit() else cleaned


def column_type(values: list[str]) -> str:
    present = [v for v in values if v != ""]
    if not present:
        return "TEXT"
    try:
        for v in present:
            int(v)
        return "INTEGER"
    except ValueError:
        pass
    try:
        for v in present:
            float(v)
        return "REAL"
    except ValueError:
        return "TEXT"


def convert(value: str, kind: str):
    if value == "":
        return None
    if kind == "INTEGER":
        return int(value)
    if kind == "REAL":
        return float(value)
    return value


def load_csv(conn: sqlite3.Connection, path: Path) -> str:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))
    if not rows:
        return ""
    header = [identifier(h) for h in rows[0]]
    body = [r + [""] * (len(header) - len(r)) for r in rows[1:]]
    kinds = [column_type([r[i] for r in body]) for i in range(len(header))]
    table = identifier(path.stem)
    columns = ", ".join(f'"{name}" {kind}' for name, kind in zip(header, kinds, strict=True))
    conn.execute(f'CREATE TABLE "{table}" ({columns})')
    placeholders = ", ".join("?" for _ in header)
    conn.executemany(
        f'INSERT INTO "{table}" VALUES ({placeholders})',
        [[convert(r[i], kinds[i]) for i in range(len(header))] for r in body],
    )
    return f"{table}({', '.join(f'{n} {k.lower()}' for n, k in zip(header, kinds, strict=True))}) · {len(body)} rows"


def cell(value) -> str:
    text = "NULL" if value is None else str(value)
    return text if len(text) <= MAX_CELL else text[: MAX_CELL - 1] + "…"


def render(columns: list[str], rows: list[tuple]) -> str:
    shown = [[cell(v) for v in row] for row in rows[:MAX_ROWS]]
    widths = [max([len(c)] + [len(r[i]) for r in shown]) for i, c in enumerate(columns)]
    line = "+" + "+".join("-" * (w + 2) for w in widths) + "+"
    fmt = lambda values: "| " + " | ".join(v.ljust(w) for v, w in zip(values, widths, strict=True)) + " |"  # noqa: E731
    out = [line, fmt(columns), line, *(fmt(r) for r in shown), line]
    count = len(rows)
    out.append(f"{count} row{'s' if count != 1 else ''}" + (f" (showing {MAX_ROWS})" if count > MAX_ROWS else ""))
    return "\n".join(out)


def statements(script: str) -> list[str]:
    parts, buffer = [], ""
    for line in script.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            if buffer.strip():
                parts.append(buffer.strip())
            buffer = ""
    if buffer.strip():
        parts.append(buffer.strip())
    return parts


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: sql_lab.py <file.sql>", file=sys.stderr)
        return 2
    entry = Path(sys.argv[1])
    conn = sqlite3.connect(":memory:")
    tables = [load_csv(conn, p) for p in sorted(Path(".").rglob("*.csv")) if p.is_file()]
    tables = [t for t in tables if t]
    if tables:
        print("Tables:\n  " + "\n  ".join(tables) + "\n")
    sql = entry.read_text(encoding="utf-8")
    for index, statement in enumerate(statements(sql), start=1):
        try:
            cursor = conn.execute(statement)
        except sqlite3.Error as exc:
            first = statement.splitlines()[0][:80]
            print(f"Error in statement {index} ({first}): {exc}", file=sys.stderr)
            return 1
        if cursor.description:
            columns = [d[0] for d in cursor.description]
            print(render(columns, cursor.fetchall()) + "\n")
        elif cursor.rowcount >= 0:
            print(f"OK ({cursor.rowcount} row{'s' if cursor.rowcount != 1 else ''} affected)\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
