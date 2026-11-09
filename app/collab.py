"""Server side of CodeMirror collab: apply serialized ChangeSets to file text.

CodeMirror measures positions in UTF-16 code units and joins lines with "\n", so text is normalized to
"\n" line breaks and changes are applied on the UTF-16 encoding. A ChangeSet's JSON form is a list where a
number keeps that many units and a list `[deleted, *inserted_lines]` replaces `deleted` units with the lines
joined by "\n".
"""

from __future__ import annotations

from typing import Any

MAX_UPDATES_PER_PUSH = 200
MAX_PARTS_PER_CHANGESET = 20_000


class ChangeSetError(ValueError):
    """The change set doesn't fit the document (wrong length or malformed)."""


def normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n") if "\r" in text else text


def utf16_length(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def apply_changes(text: str, changes: Any) -> str:
    if not isinstance(changes, list) or len(changes) > MAX_PARTS_PER_CHANGESET:
        raise ChangeSetError("changes must be a list")
    source = text.encode("utf-16-le")
    total = len(source) // 2
    out: list[bytes] = []
    pos = 0
    for part in changes:
        if isinstance(part, bool):
            raise ChangeSetError("malformed change")
        if isinstance(part, int):
            if part < 0 or pos + part > total:
                raise ChangeSetError("change set is longer than the document")
            out.append(source[pos * 2 : (pos + part) * 2])
            pos += part
        elif isinstance(part, list) and part and isinstance(part[0], int) and not isinstance(part[0], bool):
            deleted = part[0]
            lines = part[1:]
            if deleted < 0 or pos + deleted > total or not all(isinstance(line, str) for line in lines):
                raise ChangeSetError("malformed replacement")
            if lines:
                out.append("\n".join(lines).encode("utf-16-le"))
            pos += deleted
        else:
            raise ChangeSetError("malformed change")
    if pos != total:
        raise ChangeSetError("change set doesn't cover the whole document")
    try:
        return b"".join(out).decode("utf-16-le")
    except UnicodeDecodeError as exc:
        raise ChangeSetError("change splits a character") from exc


def replacement(old: str, new: str) -> list[Any]:
    """A ChangeSet (JSON) that turns `old` into `new`, keeping the shared prefix and suffix."""
    a, b = old.encode("utf-16-le"), new.encode("utf-16-le")
    la, lb = len(a) // 2, len(b) // 2
    prefix = 0
    while prefix < min(la, lb) and a[prefix * 2 : prefix * 2 + 2] == b[prefix * 2 : prefix * 2 + 2]:
        prefix += 1
    suffix = 0
    while (
        suffix < min(la, lb) - prefix
        and a[(la - suffix - 1) * 2 : (la - suffix) * 2] == b[(lb - suffix - 1) * 2 : (lb - suffix) * 2]
    ):
        suffix += 1
    # Never split a surrogate pair at either boundary.
    while prefix and 0xD800 <= int.from_bytes(a[(prefix - 1) * 2 : prefix * 2], "little") <= 0xDBFF:
        prefix -= 1
    while suffix and 0xDC00 <= int.from_bytes(a[(la - suffix) * 2 : (la - suffix + 1) * 2], "little") <= 0xDFFF:
        suffix -= 1
    inserted = b[prefix * 2 : (lb - suffix) * 2].decode("utf-16-le")
    parts: list[Any] = []
    if prefix:
        parts.append(prefix)
    deleted = la - prefix - suffix
    if deleted or inserted:
        parts.append([deleted, *inserted.split("\n")] if inserted else [deleted])
    if suffix:
        parts.append(suffix)
    return parts
