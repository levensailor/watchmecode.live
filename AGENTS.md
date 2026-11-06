## Learned User Preferences

- Commit and push to main after each change; add a CHANGELOG entry for features and keep README, CONTRIBUTING, and CHANGELOG current.
- Never print, hardcode, or commit secrets; read them from `.env` or Vercel env and pipe values without echoing them.
- Verify UI changes in the browser by exercising the feature, beyond a single screenshot.
- Use uv for all Python work.
- Ask before creating real billable or live-mode resources when intent is unclear; once the user confirms (e.g. "the key is live - go ahead"), proceed.
- Keep the marketing UI free of vanity stats (session/candidate counts, latency figures); show privacy and terms links in the footer.

## Learned Workspace Facts

- watchmecode.live is a FastAPI app (Python 3.12, uv) on Vercel project levensailors-projects/watchmecode-live, region iad1, entrypoint `api.index:app` via `[tool.vercel]` in pyproject; pushing to main auto-deploys.
- Vercel env var changes only take effect after a redeploy (`vercel redeploy <prod url> --target production`).
- Production is multi-tenant with `ROOT_DOMAIN=watchmecode.live`: the apex serves the Jinja product site (`templates/site/`) and the Google sign-in hub; `<slug>.watchmecode.live` serves workspaces in schema `t_<slug>`; legacy data is the `app` workspace (schema `watchmecode`); the `platform` schema holds tenants, memberships, usage, and Stripe events.
- Postgres is Supabase (supabase-orange-park) via `POSTGRES_URL`.
- Tests run with `uv run pytest`; `tests/conftest.py` pins single-tenant settings, and multi-tenant tests use the `multi_app` and `tenant_client` fixtures.
- Local multi-tenant dev: set `ROOT_DOMAIN=localhost PUBLIC_URL=http://localhost:8000` and browse `<slug>.localhost:8000`.
- The CodeMirror bundle is vendored (`cd frontend && npm run build` writes `static/vendor/codemirror.js`) and imported via an import map as `wmc/codemirror` with a content-hash version, so browsers never mix a new app with a stale bundle.
- The root `.gitignore` must use `/site/` (anchored) so `templates/site/` and `static/site/` stay tracked.
- The vercel SDK pulls in httpx2/httpcore2; `app/logging_config.py` quiets those loggers along with httpx/httpcore.
- The Excalidraw whiteboard must receive its first scene through `initialData`; an early `updateScene` gets wiped by async init and can save an empty board over a real one.
- Stripe live mode is configured idempotently by `scripts/stripe_setup.py` (prices by `wmc_*` lookup keys, meter `interview_credits`, webhook `/api/stripe/webhook`); `STRIPE_PRICES` and `STRIPE_WEBHOOK_SECRET` live in Vercel env.
