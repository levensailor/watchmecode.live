"""Content-hash versions for cache-busting static assets (mtimes aren't reliable on serverless deploys)."""

from __future__ import annotations

import hashlib
from pathlib import Path

_digests: dict[tuple[str, int, int], str] = {}


def content_version(*paths: Path) -> str:
    digest = hashlib.sha256()
    for path in paths:
        if not path.exists():
            continue
        stat = path.stat()
        key = (str(path), stat.st_mtime_ns, stat.st_size)
        if key not in _digests:
            _digests[key] = hashlib.sha256(path.read_bytes()).hexdigest()
        digest.update(_digests[key].encode())
    return digest.hexdigest()[:12]
