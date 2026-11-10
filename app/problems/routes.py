"""Interviewer endpoints for the company problem library."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.auth import require_user
from app.languages import LanguageId
from app.problems.catalog import CatalogError
from app.problems.generate import PROBLEM_LANGUAGES, ProblemGenerationError, generate_problem
from app.repository import Repository
from app.schemas import ProblemCatalogOut, ProblemGenerate, ProblemListOut, ProblemOut, ProblemRef

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/problems", tags=["problems"])


def _repo(request: Request) -> Repository:
    return request.app.state.repo


def _transport(request: Request):
    return getattr(request.app.state, "http_transport", None)


def _source(request: Request, ref: ProblemRef) -> dict[str, Any]:
    try:
        return request.app.state.problem_catalog.problem(_transport(request), ref.company, ref.window, ref.slug)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That problem isn't on this company's list.") from exc
    except CatalogError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc


def _check_language(language: LanguageId) -> None:
    if language not in PROBLEM_LANGUAGES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Company problems are available for code languages only.")


def generate_cached(
    request: Request, repo: Repository, user: dict, source: dict[str, Any], language: LanguageId
) -> dict[str, Any]:
    """The workspace's cached problem for `source`, generating it (within the hourly limit) when missing."""
    settings = request.app.state.settings
    since = (datetime.now(UTC) - timedelta(hours=1)).isoformat(timespec="milliseconds")
    if repo.count_problem_generations(user["email"], since) >= settings.problem_generations_per_hour:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"You've generated {settings.problem_generations_per_hour} new problems in the last hour. "
            "Pick one you've already previewed, or try again later.",
        )
    try:
        problem = generate_problem(settings, _transport(request), source, language)
    except ProblemGenerationError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    return repo.save_cached_problem(
        source["slug"], language.value, problem["name"], problem["summary"], problem["files"], problem["notes"],
        problem["model"], user["email"],
    )


def problem_for_session(request: Request, repo: Repository, user: dict, ref: ProblemRef, language: LanguageId) -> dict:
    """Cached problem for a new session; only reads the list when it has to generate."""
    _check_language(language)
    cached = repo.get_cached_problem(ref.slug, language.value)
    return cached or generate_cached(request, repo, user, _source(request, ref), language)


@router.get("/companies", response_model=ProblemCatalogOut)
def catalog(request: Request, user: dict = Depends(require_user)) -> dict:
    catalog = request.app.state.problem_catalog
    try:
        companies = catalog.companies(_transport(request))
    except CatalogError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    languages = [language for language in LanguageId if language in PROBLEM_LANGUAGES]
    return {"source_url": catalog.source_url, "companies": companies, "languages": languages}


@router.get("", response_model=ProblemListOut)
def problem_list(
    request: Request,
    company: str = Query(min_length=1, max_length=120),
    window: str = Query(min_length=1, max_length=40),
    user: dict = Depends(require_user),
) -> dict:
    try:
        problems = request.app.state.problem_catalog.problems(_transport(request), company, window)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown company or time window.") from exc
    except CatalogError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    return {"company": company, "window": window, "problems": problems}


@router.post("/generate", response_model=ProblemOut)
def generate(
    body: ProblemGenerate, request: Request, user: dict = Depends(require_user), repo: Repository = Depends(_repo)
) -> dict:
    _check_language(body.language)
    source = _source(request, body)
    cached = repo.get_cached_problem(body.slug, body.language.value)
    problem = cached or generate_cached(request, repo, user, source, body.language)
    return {
        "name": problem["name"],
        "summary": problem["summary"],
        "language": body.language,
        "files": problem["files"],
        "notes": problem["notes"],
        "source": source,
        "cached": cached is not None,
    }
