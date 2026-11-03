# Deployment

## Vercel

| Setting | Value |
| --- | --- |
| Project | `levensailors-projects/watchmecode-live` |
| Framework preset | FastAPI, entrypoint `api.index:app` (set in `pyproject.toml`) |
| Region | `iad1` |
| Production | `main` branch → [watchmecode.live](https://watchmecode.live) |
| Previews | Every other branch gets its own URL |

Pushing to GitHub deploys automatically. For a non-interactive CLI deploy, use `VERCEL_TOKEN`, `VERCEL_ORG_ID`, and `VERCEL_PROJECT_ID`.

### Environment variables

| Name | Production | Preview |
| --- | --- | --- |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_SCOPES`, `SESSION_SECRET` | yes | yes |
| `GOOGLE_REDIRECT_URI` = `https://watchmecode.live/auth/google/callback` | yes | derived from the request URL |
| `PUBLIC_URL` | yes | no |
| `POSTGRES_URL` and the other Supabase keys | injected by the integration | injected |

Add one with:

```bash
printf '%s' "$VALUE" | vercel env add NAME production
vercel env add NAME preview "" --value "$VALUE" --yes
```

### Google OAuth client

Authorize these redirect URIs on the Google client:

- `https://watchmecode.live/auth/google/callback`
- `http://127.0.0.1:8000/auth/google/callback`

Preview URLs change on every deploy, so Google sign-in works on production and locally. Candidates can join previews with a PIN.

### Database

The Supabase resource `supabase-orange-park` from the Vercel Marketplace is connected to the project. The app creates the `watchmecode` schema and its tables on startup, with row-level security enabled. To create them by hand, run [`sql/schema_postgres.sql`](https://github.com/levensailor/watchmecode.live/blob/main/sql/schema_postgres.sql) after `CREATE SCHEMA watchmecode; SET search_path TO watchmecode;`.

### Runtimes on Vercel

| Language | On Vercel |
| --- | --- |
| Python | Runs; imports auto-install with `pip install --target /tmp/...` |
| Shell | Runs |
| HTML/CSS/JS | Renders in the browser preview |
| JavaScript, TypeScript, Java, C#, C++, C, Go, PHP | Clear "not installed on this server" message |

Self-host on a machine with the toolchains on `PATH` to run all ten. With uv installed, Python uses `uv run --with`. JavaScript uses `npm install`, plus `nvm use` when nvm is present.

## Read the Docs

`.readthedocs.yaml` builds this MkDocs site. To publish, import `levensailor/watchmecode.live` at [readthedocs.org/dashboard/import](https://readthedocs.org/dashboard/import/). Builds then run on every push.

## CI

`.github/workflows/ci.yml` installs uv and Node, then runs `uv sync` and `uv run pytest` on every push and pull request.
