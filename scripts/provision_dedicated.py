"""Provision a dedicated Enterprise instance: Vercel project + Postgres (Supabase or Neon) + domain.

The instance runs this same repository with TENANT_MODE=dedicated. Interviewers still sign in at the
apex auth hub, which hands them off with an Ed25519 token the instance verifies using only the
public key. The hub learns about the instance from a `deployment=dedicated` tenant row.

Dry run by default. With --apply it calls the Vercel, Supabase, or Neon APIs using tokens from the
environment (VERCEL_TOKEN, VERCEL_TEAM_ID, SUPABASE_ACCESS_TOKEN + SUPABASE_ORG_ID, or NEON_API_KEY).
Generated secrets are written to .dedicated/<slug>.env (gitignored, mode 600) and never printed.

Usage:
  uv run python scripts/provision_dedicated.py --slug bigco --name "BigCo" --owner admin@bigco.com
  uv run python scripts/provision_dedicated.py --slug bigco --name "BigCo" --owner admin@bigco.com \
      --domain code.bigco.com --db supabase --allowed-domain bigco.com --apply
"""

from __future__ import annotations

import argparse
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote

import httpx
from cryptography.fernet import Fernet

from app.config import PROJECT_ROOT, get_settings
from app.handoff import public_key_b64
from app.repository import Repository
from app.tenancy import TenantRegistry, apex_url, slug_error

VERCEL_API = "https://api.vercel.com"
SUPABASE_API = "https://api.supabase.com"
NEON_API = "https://console.neon.tech/api/v2"
SUPABASE_REGIONS = {"iad1": "us-east-1", "sfo1": "us-west-1", "fra1": "eu-central-1", "lhr1": "eu-west-2", "syd1": "ap-southeast-2"}
NEON_REGIONS = {"iad1": "aws-us-east-1", "sfo1": "aws-us-west-2", "fra1": "aws-eu-central-1", "lhr1": "aws-eu-west-2", "syd1": "aws-ap-southeast-2"}


def repo_slug() -> tuple[str, str]:
    url = subprocess.run(["git", "remote", "get-url", "origin"], capture_output=True, text=True, cwd=PROJECT_ROOT).stdout.strip()
    path = url.removesuffix(".git").split("github.com")[-1].lstrip(":/")
    owner, _, repo = path.partition("/")
    if not owner or not repo:
        sys.exit("Could not read the GitHub origin remote.")
    return owner, repo


def need(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        sys.exit(f"Set {name} first.")
    return value


class Api:
    def __init__(self, base: str, token: str, params: dict | None = None) -> None:
        self.client = httpx.Client(base_url=base, headers={"Authorization": f"Bearer {token}"}, params=params or {}, timeout=60)

    def call(self, method: str, path: str, **kwargs) -> dict | list:
        response = self.client.request(method, path, **kwargs)
        if response.status_code >= 400:
            sys.exit(f"{method} {path} failed ({response.status_code}): {response.text[:300]}")
        return response.json() if response.content else {}


def create_supabase(name: str, region: str, password: str) -> str:
    api = Api(SUPABASE_API, need("SUPABASE_ACCESS_TOKEN"))
    project = api.call(
        "POST",
        "/v1/projects",
        json={"name": name, "organization_id": need("SUPABASE_ORG_ID"), "db_pass": password, "region": SUPABASE_REGIONS.get(region, "us-east-1")},
    )
    ref = project["id"]
    print(f"Supabase project {ref} created; waiting for it to become healthy…")
    for _ in range(60):
        status = api.call("GET", f"/v1/projects/{ref}").get("status")
        if status == "ACTIVE_HEALTHY":
            break
        time.sleep(10)
    else:
        sys.exit("Supabase project did not become healthy in 10 minutes; re-run with --database-url when it is.")
    poolers = api.call("GET", f"/v1/projects/{ref}/config/database/pooler")
    pooler = next((p for p in poolers if p.get("pool_mode") == "transaction"), poolers[0])
    return (
        f"postgresql://{pooler['db_user']}:{quote(password, safe='')}@{pooler['db_host']}:{pooler['db_port']}/"
        f"{pooler.get('db_name', 'postgres')}?sslmode=require"
    )


def create_neon(name: str, region: str) -> str:
    api = Api(NEON_API, need("NEON_API_KEY"))
    created = api.call("POST", "/projects", json={"project": {"name": name, "region_id": NEON_REGIONS.get(region, "aws-us-east-1")}})
    return created["connection_uris"][0]["connection_uri"]


def write_secrets(slug: str, values: dict[str, str]) -> Path:
    folder = PROJECT_ROOT / ".dedicated"
    folder.mkdir(exist_ok=True)
    path = folder / f"{slug}.env"
    path.write_text("".join(f"{k}={v}\n" for k, v in values.items()))
    path.chmod(0o600)
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--slug", required=True, help="workspace slug, e.g. bigco")
    parser.add_argument("--name", required=True, help="customer name")
    parser.add_argument("--owner", required=True, help="owner's Google email")
    parser.add_argument("--domain", help="instance domain (default <slug>.ROOT_DOMAIN)")
    parser.add_argument("--region", default="iad1", help="Vercel function region (iad1, sfo1, fra1, lhr1, syd1)")
    parser.add_argument("--db", choices=("supabase", "neon"), default="supabase")
    parser.add_argument("--database-url", help="use an existing Postgres instead of creating one")
    parser.add_argument("--allowed-domain", action="append", default=[], help="Google domain allowed to sign in (repeatable)")
    parser.add_argument("--apply", action="store_true", help="create the resources")
    args = parser.parse_args()

    settings = get_settings()
    slug = args.slug.strip().lower()
    if not settings.root_domain:
        sys.exit("Set ROOT_DOMAIN (and the production PLATFORM database env) so the hub can register the instance.")
    problem = slug_error(settings, slug)
    if problem:
        sys.exit(f"--slug: {problem}")
    domain = (args.domain or f"{slug}.{settings.root_domain}").lower()
    owner, repo = repo_slug()
    project_name = f"wmc-{slug}"
    hub = apex_url(settings)

    plan = [
        f"Vercel project {project_name} from github.com/{owner}/{repo} (region {args.region})",
        f"Postgres: {'existing --database-url' if args.database_url else args.db}",
        f"Env: TENANT_MODE=dedicated TENANT_SLUG={slug} PUBLIC_URL=https://{domain} AUTH_HUB_URL={hub} HANDOFF_PUBLIC_KEY=<hub key> + generated secrets",
        f"Domain {domain} on the project",
        "Production deployment from main",
        f"Hub tenant row: slug={slug} deployment=dedicated custom_domain={domain} plan=enterprise owner={args.owner}"
        + (f" allowed_domains={','.join(args.allowed_domain)}" if args.allowed_domain else ""),
    ]
    if not args.apply:
        print("Dry run. Would provision:")
        for step in plan:
            print(f"- {step}")
        print("Re-run with --apply to create them.")
        return

    vercel_params = {"teamId": os.environ["VERCEL_TEAM_ID"]} if os.environ.get("VERCEL_TEAM_ID") else {}
    vercel = Api(VERCEL_API, need("VERCEL_TOKEN"), vercel_params)

    secrets_out = {
        "SESSION_SECRET": secrets.token_urlsafe(48),
        "INTEGRATION_ENCRYPTION_KEY": Fernet.generate_key().decode(),
        "CRON_SECRET": secrets.token_urlsafe(32),
    }
    if args.database_url:
        database_url = args.database_url
    elif args.db == "supabase":
        db_password = secrets.token_urlsafe(24)
        secrets_out["DB_PASSWORD"] = db_password
        database_url = create_supabase(project_name, args.region, db_password)
    else:
        database_url = create_neon(project_name, args.region)
    secrets_out["DATABASE_URL"] = database_url

    project = vercel.call(
        "POST",
        "/v11/projects",
        json={"name": project_name, "framework": "fastapi", "gitRepository": {"type": "github", "repo": f"{owner}/{repo}"}},
    )
    print(f"Vercel project {project['id']} created")

    env = {
        "TENANT_MODE": "dedicated",
        "TENANT_SLUG": slug,
        "PUBLIC_URL": f"https://{domain}",
        "AUTH_HUB_URL": hub,
        "HANDOFF_PUBLIC_KEY": public_key_b64(settings),
        "APP_NAME": settings.app_name,
        **{k: v for k, v in secrets_out.items() if k != "DB_PASSWORD"},
    }
    vercel.call(
        "POST",
        f"/v10/projects/{project['id']}/env",
        params={"upsert": "true"},
        json=[{"key": k, "value": v, "type": "encrypted", "target": ["production", "preview"]} for k, v in env.items()],
    )
    vercel.call("PATCH", f"/v9/projects/{project['id']}", json={"serverlessFunctionRegion": args.region})
    vercel.call("POST", f"/v10/projects/{project['id']}/domains", json={"name": domain})
    deployment = vercel.call(
        "POST",
        "/v13/deployments",
        json={"name": project_name, "project": project["id"], "target": "production",
              "gitSource": {"type": "github", "org": owner, "repo": repo, "ref": "main"}},
    )
    print(f"Deployment {deployment.get('id')} started: https://{deployment.get('url')}")

    registry = TenantRegistry(settings, Repository(settings))
    registry.init()
    platform = registry.platform
    tenant = platform.get_tenant(slug) or platform.create_tenant(
        slug=slug, name=args.name, owner_email=args.owner, schema_name="dedicated", plan="enterprise", status="active",
        deployment="dedicated",
    )
    platform.update_tenant(
        tenant["id"], deployment="dedicated", custom_domain=domain, plan="enterprise", status="active",
        allowed_domains=args.allowed_domain,
    )
    path = write_secrets(slug, {**secrets_out, "VERCEL_PROJECT_ID": project["id"]})
    print(f"Registered {slug} with the hub. Secrets saved to {path.relative_to(PROJECT_ROOT)}.")
    print(f"Next: point DNS for {domain} at Vercel if it isn't on Vercel DNS, then sign in at {hub}/api/platform/go/{slug}")


if __name__ == "__main__":
    main()
