"""Product site (apex): marketing pages, pricing, signup, and the workspace picker."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app import plans
from app.assets import content_version
from app.config import PROJECT_ROOT, Settings
from app.languages import LAB_LANGUAGE_IDS, LANGUAGES
from app.tenancy import apex_url, current_site, tenant_url

logger = logging.getLogger(__name__)

TEMPLATES = Environment(
    loader=FileSystemLoader(PROJECT_ROOT / "templates"),
    autoescape=select_autoescape(["html"]),
    trim_blocks=True,
    lstrip_blocks=True,
)

router = APIRouter(include_in_schema=False)

NAV = (
    ("features", "Features", "/features"),
    ("labs", "Labs", "/labs"),
    ("integrations", "Integrations", "/integrations"),
    ("pricing", "Pricing", "/pricing"),
    ("customers", "Customers", "/customers"),
    ("security", "Security", "/security"),
)

FEATURES: tuple[dict[str, Any], ...] = (
    {
        "group": "Interviewing",
        "items": (
            ("Join in one click", "Candidates join with a 6-digit PIN or a signed invite link. No account, no download.", "join.webp"),
            ("A shared IDE, in real time", "Explorer, tabs, editor, console, and preview with equal control. Edits sync in about a second.", "ide-light.webp"),
            ("10 languages and a web playground", "Python, JavaScript, TypeScript, Java, C#, C++, C, Go, PHP, Shell, plus HTML/CSS/JS with a sandboxed live preview.", "preview.webp"),
            ("Run with automatic installs", "Write import requests and press Run. Packages install on demand, so nobody burns five minutes on setup.", "run.webp"),
        ),
    },
    {
        "group": "Review",
        "items": (
            ("Recording and playback", "Press Record and replay every keystroke, file change, and run at 1x, 2x, or 4x. Scrub to any moment.", "playback.gif"),
            ("Candidate portal", "Sessions grouped by candidate. Reopen any interview to review the final code and every run.", "portal.webp"),
        ),
    },
    {
        "group": "Assessments",
        "items": (
            ("Timed take-homes", "Reusable assessments with a countdown and a Submit button. Work is submitted when time runs out.", "take-home.webp"),
            ("Works on any screen", "Light and dark themes, and a layout that holds up on a phone.", "mobile.webp"),
        ),
    },
    {
        "group": "Team and integrations",
        "items": (
            ("Your own workspace", "Every customer gets a subdomain with its own members, roles, API keys, and database schema.", "workspace.webp"),
            ("Ashby and ModernLoop", "Scheduled interviews become sessions automatically; results, code, and playback go back to the candidate.", "integrations.webp"),
        ),
    },
)

LABS: tuple[dict[str, str], ...] = (
    {"name": "SQL lab", "status": "Shipped", "body": "Seed tables from CSV files, write queries, and get formatted result tables. Great for analyst, data, and backend roles.", "language": "sql"},
    {"name": "React lab", "status": "Shipped", "body": "React components with a live preview and no build step. JSX-free with htm, so it runs instantly in the sandboxed preview.", "language": "react"},
    {"name": "System design whiteboard", "status": "Shipped", "body": "A shared Excalidraw board saved with the session, for architecture and senior interviews.", "language": "whiteboard"},
    {"name": "Sandbox runner", "status": "Shipped", "body": "Every language in an isolated Firecracker microVM on Vercel Sandbox, with installs cached for the session.", "language": ""},
    {"name": "Project and API labs", "status": "Next", "body": "Multi-file repos with a terminal, hidden tests, and a server on an exposed port.", "language": ""},
    {"name": "Linux, Docker, and cloud labs", "status": "Enterprise", "body": "Broken-system SRE scenarios, Kubernetes, and short-lived AWS accounts.", "language": ""},
)

TESTIMONIALS: tuple[dict[str, str], ...] = (
    # Illustrative quotes from design-partner conversations; replace with attributed customer quotes before launch.
    {"quote": "Candidates were coding within ten seconds of joining. Nobody asked how to install anything.", "who": "Engineering manager", "org": "Series B fintech"},
    {"quote": "Playback at 4x changed our debriefs. We watch the five minutes that mattered instead of arguing from memory.", "who": "Head of talent", "org": "Developer tools company"},
    {"quote": "Ashby creates the session and posts the results back. Our coordinators stopped copy-pasting links.", "who": "Recruiting operations lead", "org": "Healthcare platform"},
)

FAQ: tuple[tuple[str, str], ...] = (
    ("What counts as an interview credit?", "One credit when a candidate joins a live interview or starts a take-home. Interviewers practicing on their own never use credits, and rejoining the same session is free. Sandbox labs use 2 credits."),
    ("Do I need a credit card for the trial?", "No. You get 14 days of Team features and 15 credits. When you choose a plan, everything you created during the trial stays."),
    ("How many interviewers can I add?", "As many as you like on every plan. You can also let anyone with your company's Google domain sign in."),
    ("What happens if we go over our credits?", "Interviews keep working. Extra credits are billed at your plan's overage rate at the end of the period."),
    ("Is our data isolated from other customers?", "Yes. Each workspace has its own database schema and subdomain. Enterprise workspaces run on a dedicated instance with their own database and region."),
    ("Can we cancel anytime?", "Yes, from the billing portal. Your workspace stays readable after cancellation so you can export code and recordings."),
)

COMPETITORS: tuple[tuple[str, str, str, str], ...] = (
    ("watchmecode.live Team", "$249/mo", "50", "Included"),
    ("CoderPad Team", "$400/mo", "30", "Enterprise only"),
    ("HackerRank Pro", "$419/mo", "30", "Included"),
    ("CodeSignal Grow", "$479/mo", "35", "Basic"),
)

PAGES = {
    "home": "home.html",
    "features": "features.html",
    "labs": "labs.html",
    "integrations": "integrations.html",
    "pricing": "pricing.html",
    "customers": "customers.html",
    "security": "security.html",
    "signup": "signup.html",
    "login": "login.html",
}


def asset_version() -> str:
    static = PROJECT_ROOT / "static"
    return content_version(static / "site.css", static / "site.js")


def context(request: Request, page: str, **extra: Any) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    registry = request.app.state.tenants
    default = registry.default_tenant() if registry.enabled else None
    return {
        "settings": settings,
        "page": page,
        "nav": NAV,
        "app_name": settings.app_name,
        "tagline": settings.app_tagline,
        "apex": apex_url(settings) if settings.multi_tenant else "",
        "root_domain": settings.root_domain or "watchmecode.live",
        "multi": registry.enabled,
        "legacy_app_url": tenant_url(settings, default) if default else "",
        "tenant_template": tenant_url(settings, {"slug": "__slug__"}) if settings.multi_tenant else "",
        "google_enabled": settings.google_configured,
        "plans": plans.PLANS,
        "trial_days": settings.trial_days,
        "features": FEATURES,
        "labs": LABS,
        "testimonials": TESTIMONIALS,
        "faq": FAQ,
        "competitors": COMPETITORS,
        "languages": [lang.label for lang in LANGUAGES.values() if lang.id not in LAB_LANGUAGE_IDS],
        "year": datetime.now(UTC).year,
        "version": asset_version(),
        "canonical": f"{apex_url(settings) if settings.multi_tenant else ''}{'/' if page == 'home' else '/' + page}",
        **extra,
    }


def render(request: Request, page: str, **extra: Any) -> HTMLResponse:
    template = TEMPLATES.get_template(f"site/{PAGES[page]}")
    return HTMLResponse(template.render(context(request, page, **extra)))


def serves_site(request: Request) -> bool:
    """The product site lives on the apex; single-workspace servers also expose it for preview."""
    registry = request.app.state.tenants
    return not registry.enabled or current_site.get() == "apex"


def _page(name: str):
    def handler(request: Request):
        if not serves_site(request):
            settings: Settings = request.app.state.settings
            return RedirectResponse(f"{apex_url(settings)}/{name}", status_code=302)
        if name in ("signup", "login") and not request.app.state.tenants.enabled:
            return RedirectResponse("/auth/google" if name == "login" else "/", status_code=302)
        return render(request, name, error=request.query_params.get("error"), tenant=request.query_params.get("tenant"))

    handler.__name__ = f"site_{name}"
    return handler


for _name in PAGES:
    if _name != "home":
        router.add_api_route(f"/{_name}", _page(_name), methods=["GET"], response_class=HTMLResponse)
