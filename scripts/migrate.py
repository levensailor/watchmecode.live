"""Apply the current schema to the platform store and every workspace.

The app also migrates lazily: a workspace whose recorded schema_version differs from the
current one is migrated on its first request. Run this after a deploy to do it up front.

Usage: uv run python scripts/migrate.py [--force]
"""

from __future__ import annotations

import argparse
import json
import logging

from app.config import get_settings
from app.logging_config import setup_logging
from app.repository import Repository
from app.tenancy import TenantRegistry

logger = logging.getLogger("migrate")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--force", action="store_true", help="re-run the schema even when versions match")
    args = parser.parse_args()

    settings = get_settings()
    setup_logging(settings.log_dir)
    default_repo = Repository(settings)
    default_repo.init_schema()
    registry = TenantRegistry(settings, default_repo)
    if not registry.enabled:
        logger.info("Single-workspace mode (ROOT_DOMAIN unset): default schema is up to date")
        print(json.dumps([{"slug": settings.tenant_slug, "schema": settings.db_schema, "migrated": True}]))
        return
    registry.init()
    print(json.dumps(registry.migrate_all(force=args.force), indent=2))


if __name__ == "__main__":
    main()
