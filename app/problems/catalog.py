"""Company problem lists from a public repository of CSVs (one folder per company, one CSV per time window).

The listing comes from jsDelivr, which has no per-IP API quota, and the CSVs from raw.githubusercontent.com.
Nothing is vendored: the repository has no license, so lists are fetched on demand and cached in memory.
"""

from __future__ import annotations

import csv
import io
import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from app.config import Settings
from app.integrations.http import make_client

logger = logging.getLogger(__name__)

LISTING_TTL_SECONDS = 24 * 60 * 60
LIST_TTL_SECONDS = 6 * 60 * 60
MAX_CSV_BYTES = 2_000_000
SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
CSV_NAME = re.compile(r"^(\d+)\.\s+(.+)\.csv$")
# Problems tagged only for these topics are SQL or shell exercises, not code-language problems.
EXCLUDED_TOPICS = frozenset({"Database", "Shell"})
DIFFICULTIES = ("EASY", "MEDIUM", "HARD")


@dataclass(frozen=True)
class Window:
    id: str
    label: str
    order: int


WINDOWS = {
    "Thirty Days": Window("thirty-days", "Last 30 days", 1),
    "Three Months": Window("three-months", "Last 3 months", 2),
    "Six Months": Window("six-months", "Last 6 months", 3),
    "More Than Six Months": Window("more-than-six-months", "Older than 6 months", 4),
    "All": Window("all", "All time", 5),
}


class CatalogError(RuntimeError):
    """The problem lists couldn't be loaded; the message is safe to show interviewers."""


def window_for(filename: str) -> tuple[Window, str] | None:
    match = CSV_NAME.match(filename)
    if not match or match.group(2) not in WINDOWS:
        return None
    return WINDOWS[match.group(2)], filename


def slug_from_link(link: str) -> str | None:
    match = re.match(r"^https://leetcode\.com/problems/([^/?#]+)/?$", link.strip())
    slug = match.group(1).lower() if match else ""
    return slug if SLUG.match(slug) else None


def _acceptance_percent(raw: str) -> float | None:
    try:
        value = float(raw)
    except ValueError:
        return None
    # The lists store acceptance as percent / 10,000 (Two Sum, about 58%, appears as 0.0058).
    if value <= 0.01:
        value *= 10_000
    elif value <= 1:
        value *= 100
    return round(value, 1) if 0 <= value <= 100 else None


def parse_csv(text: str) -> list[dict[str, Any]]:
    """Rows as {slug, title, difficulty, frequency, acceptance, topics}; malformed rows are skipped."""
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in csv.DictReader(io.StringIO(text)):
        slug = slug_from_link(row.get("Link") or "")
        title = (row.get("Title") or "").strip()
        difficulty = (row.get("Difficulty") or "").strip().upper()
        if not slug or not title or difficulty not in DIFFICULTIES or slug in seen:
            continue
        topics = [t.strip() for t in (row.get("Topics") or "").split(",") if t.strip()]
        if topics and set(topics) <= EXCLUDED_TOPICS:
            continue
        try:
            frequency = round(float(row.get("Frequency") or 0), 1)
        except ValueError:
            frequency = 0.0
        seen.add(slug)
        rows.append({
            "slug": slug,
            "title": title[:200],
            "difficulty": difficulty.capitalize(),
            "frequency": frequency,
            "acceptance": _acceptance_percent(row.get("Acceptance Rate") or ""),
            "topics": topics[:12],
        })
    return rows


class Catalog:
    """Fetches and caches the company listing and the per-company lists."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._lock = threading.Lock()
        self._listing: tuple[float, dict[str, dict[str, str]]] | None = None
        self._lists: dict[tuple[str, str], tuple[float, list[dict[str, Any]]]] = {}

    @property
    def source_url(self) -> str:
        return f"https://github.com/{self.settings.problems_repo}"

    def _get(self, transport: httpx.BaseTransport | None, url: str) -> httpx.Response:
        with make_client(self.settings, transport, base_url="") as client:
            response = client.get(url)
        if response.status_code >= 400:
            raise CatalogError(f"The problem lists are unavailable right now ({response.status_code}). Try again shortly.")
        return response

    def listing(self, transport: httpx.BaseTransport | None) -> dict[str, dict[str, str]]:
        """{company: {window_id: filename}} for every company folder."""
        now = time.monotonic()
        if self._listing and now - self._listing[0] < LISTING_TTL_SECONDS:
            return self._listing[1]
        settings = self.settings
        url = f"{settings.problems_listing_url}/{settings.problems_repo}@{settings.problems_ref}?structure=flat"
        try:
            files = self._get(transport, url).json().get("files") or []
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Problem listing failed: %s", exc.__class__.__name__)
            raise CatalogError("The problem lists are unavailable right now. Try again shortly.") from exc
        companies: dict[str, dict[str, str]] = {}
        for entry in files:
            parts = str(entry.get("name") or "").strip("/").split("/")
            if len(parts) != 2:
                continue
            found = window_for(parts[1])
            if found:
                companies.setdefault(parts[0], {})[found[0].id] = found[1]
        if not companies:
            raise CatalogError("The problem lists are unavailable right now. Try again shortly.")
        with self._lock:
            self._listing = (now, companies)
        return companies

    def companies(self, transport: httpx.BaseTransport | None) -> list[dict[str, Any]]:
        by_id = {w.id: w for w in WINDOWS.values()}
        result = []
        for name, windows in sorted(self.listing(transport).items(), key=lambda item: item[0].lower()):
            ordered = sorted((by_id[w] for w in windows), key=lambda w: w.order)
            result.append({"name": name, "windows": [{"id": w.id, "label": w.label} for w in ordered]})
        return result

    def problems(self, transport: httpx.BaseTransport | None, company: str, window: str) -> list[dict[str, Any]]:
        filename = self.listing(transport).get(company, {}).get(window)
        if not filename:
            raise LookupError("Unknown company or time window")
        key = (company, window)
        now = time.monotonic()
        cached = self._lists.get(key)
        if cached and now - cached[0] < LIST_TTL_SECONDS:
            return cached[1]
        settings = self.settings
        url = (
            f"{settings.problems_raw_url}/{settings.problems_repo}/{settings.problems_ref}/"
            f"{quote(company)}/{quote(filename)}"
        )
        try:
            response = self._get(transport, url)
        except httpx.HTTPError as exc:
            logger.warning("Problem list %s/%s failed: %s", company, window, exc.__class__.__name__)
            raise CatalogError("The problem list is unavailable right now. Try again shortly.") from exc
        if len(response.content) > MAX_CSV_BYTES:
            raise CatalogError("The problem list is too large to load.")
        rows = parse_csv(response.text)
        with self._lock:
            self._lists[key] = (now, rows)
        return rows

    def problem(self, transport: httpx.BaseTransport | None, company: str, window: str, slug: str) -> dict[str, Any]:
        """The row for `slug` from that company's list, so generation only runs for problems on a real list."""
        for row in self.problems(transport, company, window):
            if row["slug"] == slug:
                return row
        raise LookupError("That problem isn't on this list")
