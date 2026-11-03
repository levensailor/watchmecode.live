# watchmecode.live (optimized)

## Project configuration

- Source control: GitHub, public repository. Create it with `gh repo create`, push `main`, and use GitHub Actions for CI. Authenticate with the existing `gh` CLI login (levensailor).
- Authentication: show the signed-in user's name and avatar at the top right with a dropdown that includes Log out.
- Google sign-in: OpenID Connect authorization-code flow with PKCE using GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REDIRECT_URI, GOOGLE_SCOPES.
- Target: Vercel, framework preset FastAPI, functions in iad1, provision Supabase through the Vercel Marketplace. Link with `vercel link`, set env vars with `vercel env add`, and deploy previews per branch. Use VERCEL_TOKEN only for non-interactive deploys.
- Stack: Python (Python 3.12+, dependencies managed with uv.); FastAPI (Async HTTP API with automatic OpenAPI docs; served by Uvicorn.); SQLite (Single-file database; ideal for local apps and prototypes.)
- Logging (Python `logging` module, one handler per destination on the root logger; timestamps in America/New_York):
  - File `logs/app.log`: RotatingFileHandler maxBytes=1048576 backupCount=3, level INFO, format Text (EST, file:line, function).
  - Stdout: StreamHandler(sys.stdout), level INFO, format Text (EST, file:line, function).
- Documentation files: README.md, CONTRIBUTING.md, CHANGELOG.md.
- Docs delivered as: Markdown, Webpage.
- Draw application flows and architecture in Mermaid.
- Publish a docs site with Read the Docs.
- Secrets and settings are provided in a gitignored `.env` (never hardcode or commit them; read them with python-dotenv or the platform's env store): `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI`, `GOOGLE_SCOPES`, `GOOGLE_REFRESH_TOKEN`, `VERCEL_TOKEN`, `VERCEL_ORG_ID`, `VERCEL_PROJECT_ID`
- Never hardcode names; use configuration. Commit and push after each change.

## UI design

**Bento Box** — modular tiles of different sizes, like a bento lunch box.

- **Palette:** Background #F5F5F7 (dark: #0B0B0F); tiles #FFFFFF (dark: #1C1C22); text #111111; one accent #6E56CF; give one hero tile a gradient (#6E56CF → #E5484D).
- **Typography:** 'Geist', 'Inter', system-ui. Big numbers and headlines 32–56 px weight 700; captions 12–13 px weight 500 in #6B7280.
- **Layout:** CSS grid with 4–6 columns and dense packing; tiles span 1×1, 2×1, 2×2. Gap 12–16 px; tile padding 20–24 px. Each tile holds exactly one idea or metric.
- **Shapes and shadows:** Radius 20–28 px on tiles, 12 px inside. Shadow 0 1px 2px rgba(0,0,0,.06), 0 8px 24px rgba(0,0,0,.06). 1 px hairline borders in dark mode.
- **Motion:** Tiles fade-and-rise on load (opacity 0→1, translateY 8px→0, 300 ms, 40 ms stagger). Hover scales 1.02 with ease-out 200 ms.

Density: Comfortable.
Generate a custom favicon that matches the theme and the app's purpose.

## Instructions

# Instructions

## Goal

Build **watchmecode.live**, a live code-share IDE for technical interviews, in the spirit of LeetCode, HackerRank, CoderPad, and CodePen. An interviewer creates a session for a named candidate and gets a 6-digit PIN. The candidate enters the PIN to join, and both people edit and run code together with equal control.

## 1. Scaffold the project

- Initialize the project with uv:
  - `uv init watchmecode && cd watchmecode`
  - `uv add fastapi uvicorn pydantic python-dotenv authlib itsdangerous httpx`
  - Add `pytest` as a dev dependency.
- Use this layout:
  - `api/index.py` exports the FastAPI `app` for Vercel.
  - `app/` holds the routers, models, database code, the code runner, and logging setup.
  - `static/` holds the HTML, CSS, JS, and favicon.
  - `tests/` holds the tests.
  - `sql/` holds the schema scripts.
  - `logs/` holds the log files.
- Add `vercel.json` with `{"rewrites": [{"source": "/(.*)", "destination": "/api/index"}]}`.
- Load every setting from `.env` with python-dotenv. Add `.env`, `.venv/`, `logs/`, `*.db`, `__pycache__/`, `.vercel/`, and `node_modules/` to `.gitignore`.
- Run locally with `uv run uvicorn api.index:app --reload`.

## 2. Set up logging

- Create `app/logging_config.py` with an `EstFormatter`. Its `formatTime` should return an ISO timestamp in `ZoneInfo("America/New_York")`.
- Use the format `%(asctime)s [%(levelname)s] %(filename)s:%(lineno)d %(funcName)s - %(message)s`.
- Attach exactly two handlers to the root logger, both at level INFO:
  - `RotatingFileHandler("logs/app.log", maxBytes=1_048_576, backupCount=3)`
  - `StreamHandler(sys.stdout)`
- Create `logs/` if it is missing. On Vercel, fall back to a writable path such as `/tmp/logs`.
- **Check:** a request writes a line to both stdout and `logs/app.log`. That line includes an EST offset, `file:line`, and the function name.

## 3. Set up Google sign-in for interviewers

- Register Authlib's Starlette OAuth client as shown in the reference:
  - `server_metadata_url="https://accounts.google.com/.well-known/openid-configuration"`
  - `client_kwargs={"scope": GOOGLE_SCOPES, "code_challenge_method": "S256"}`, which enables PKCE.
- Add Starlette `SessionMiddleware`. Its secret comes from a new `.env` value, `SESSION_SECRET`. Document it in the README and add it with `vercel env add`.
- Add these routes:
  - `GET /auth/google` calls `authorize_redirect(request, GOOGLE_REDIRECT_URI)`.
  - `GET /auth/google/callback` calls `authorize_access_token` and stores `userinfo` (name, email, picture) in the session.
  - `GET /auth/logout` clears the session.
  - `GET /api/me` returns the current user, or 401.
- Before writing the client, confirm that `GOOGLE_REDIRECT_URI` exactly matches the URI registered in Google Cloud for both local and Vercel URLs.
- Top-right UI: show the user's name and avatar, with a dropdown that contains **Log out**.
- **Check:** unauthenticated calls to the portal APIs return 401. After login, `/api/me` returns the Google name and picture.

## 4. Set up the data model and storage

- Locally, use SQLite through the standard `sqlite3` module with parameterized queries only.
- In production, use the Supabase Postgres provisioned through the Vercel Marketplace, reading its connection URL from the injected env var.
- Hide both stores behind one repository module.
- Put the schema in `sql/schema.sql` with these tables:
  - `sessions`: `id`, `pin` (6 digits, unique among active sessions), `candidate_name`, `interviewer_email`, `language`, `created_at`, `updated_at`, `status`.
  - `files`: `id`, `session_id`, `path`, `content`, `updated_at`.
  - `runs`: `id`, `session_id`, `command`, `stdout`, `stderr`, `exit_code`, `created_at`.
- Sessions are saved per candidate under the candidate name the interviewer entered.

## 5. Build the API

Use Pydantic models for every request and response body, and validate input with `Field`.

**Interviewer endpoints (require sign-in):**

- `POST /api/sessions` takes `{candidate_name, language}` and returns `{id, pin, candidate_name, ...}` with status 201. It generates a random 6-digit PIN, retrying on collision.
- `GET /api/sessions` lists the interviewer's sessions, optionally filtered by candidate name.
- `GET /api/sessions/{id}` returns the session detail with its files and run history. It lets the interviewer reload an existing session before or after the interview.

**Shared endpoints (interviewer, or candidate holding a valid PIN):**

- `POST /api/join` takes `{pin}` and returns the session plus a session-scoped token. An invalid PIN returns 404.
- File operations:
  - `GET /api/sessions/{id}/files`
  - `PUT /api/sessions/{id}/files` with `{path, content}`
  - `DELETE /api/sessions/{id}/files?path=`
  - Renaming a file.
- `POST /api/sessions/{id}/run` takes `{entry_path}` and returns `{stdout, stderr, exit_code, duration_ms}`.
- A sync channel so both participants see each other's edits, file changes, and run output in near real time. Vercel functions cannot hold WebSocket connections, so use one of these:
  - Supabase Realtime.
  - A short-interval polling endpoint, `GET /api/sessions/{id}/changes?since=`.

**Check:** FastAPI's `/docs` lists every endpoint with request and response schemas.

## 6. Build the code runner and language support

- Support the 10 most popular languages on GitHub, per the GitHub Octoverse: Python, JavaScript, TypeScript, Java, C#, C++, C, Go, PHP, and Shell. Each language gets a starter file template.
- Run code in a per-session temp directory with a subprocess timeout and output size cap. Capture stdout, stderr, and the exit code.
- Use uv for Python:
  - Detect imports and auto-install third-party packages.
  - Either write PEP 723 inline metadata with `uv add --script`, or use `uv run --with <pkg>`.
  - Then run the file with `uv run`.
- Use nvm for JavaScript and TypeScript:
  - Select the Node runtime with `nvm use`.
  - Detect `import`/`require` statements and run `npm install` for missing packages.
  - Run TypeScript through a TS runner.
- For the other languages, make a best-effort attempt to resolve requirements, such as `go mod tidy` for Go. If a toolchain is not installed, return a clear error message.
- Document in the README which runtimes are available in the Vercel deployment.
- **Check:** a Python file containing `import requests` runs successfully without the user installing anything manually.

## 7. Build the IDE screen

- Lay out these panes on one screen:
  - A file explorer for creating, renaming, and deleting files and folders.
  - A tabbed file editor with syntax highlighting.
  - A terminal/console pane.
  - A preview/output pane. HTML, CSS, and JS render in a sandboxed iframe; other languages show program output.
  - A **Run** button with a **Live refresh** toggle that re-runs automatically after edits.
- Add a VS Code–style top menu (File, Edit, View, Run, Help) that exposes the same actions.
- Both participants have full control over every pane. Edits by one participant appear for the other within about 1–2 seconds.

## 8. Build the session flows and candidate portal

- **Landing page:**
  - Interviewers can sign in, then start a new session or load an existing one.
  - Candidates see a 6-digit PIN input with a **Join** button.
- **Candidate portal (interviewers only):**
  - Generating a session prompts for the candidate name and shows the PIN.
  - The portal lists sessions grouped by candidate name.
  - Interviewers can open any session before or after the interview to view its files and run history.
- Apply the Bento Box design tokens from the UI design section. Include light and dark modes and a custom favicon.

## 9. Write tests and set up CI

- Write pytest tests covering:
  - PIN generation (always 6 digits, unique).
  - Session create, list, and get.
  - Join with valid and invalid PINs.
  - File CRUD.
  - Running Python and JavaScript snippets.
  - 401 responses on the portal routes.
- Add a GitHub Actions workflow, `.github/workflows/ci.yml`. It installs uv, runs `uv sync`, then runs `uv run pytest` on every push and pull request.

## 10. Set up source control and deployment

- Create the repo with `gh repo create watchmecode.live --public --source . --push`, using the existing levensailor `gh` login.
- Commit and push to `main` after each change.
- Link the project with `vercel link`. Use region `iad1` and the FastAPI preset.
- Add Supabase through the Vercel Marketplace.
- Add every `.env` key with `vercel env add`.
- Deploy a preview for each branch.
- Use `VERCEL_TOKEN`, `VERCEL_ORG_ID`, and `VERCEL_PROJECT_ID` only for non-interactive deploys.

## 11. Write the documentation

- **README.md:** title, description, author, local setup, deploy steps, the live URL, and login instructions for both interviewers (Google) and candidates (PIN).
- **CONTRIBUTING.md:** how to create a feature branch, and how to open a pull request that explains why the change belongs in the repository.
- **CHANGELOG.md:** timestamped entries for features only.
- Include Mermaid diagrams for the architecture, the interviewer flow, the candidate join flow, and the run/sync sequence.
- Publish the docs on Read the Docs, using `.readthedocs.yaml` with MkDocs.

## Acceptance checklist

- [ ] An interviewer signs in with Google and sees their name and avatar plus a Log out dropdown.
- [ ] The interviewer creates a session for a named candidate and receives a 6-digit PIN.
- [ ] A candidate joins with the PIN in a separate browser. An invalid PIN is rejected.
- [ ] Both users edit files, switch tabs, and run code, and each sees the other's changes.
- [ ] Code runs in all 10 languages, or returns a clear missing-toolchain error. Python and JavaScript auto-install their imports.
- [ ] Live refresh re-runs the code after edits.
- [ ] The portal lists sessions by candidate, and they can be reopened after the interview.
- [ ] Logs reach both stdout and the rotating `logs/app.log` with EST timestamps.
- [ ] CI passes, the Vercel deployment is live, and the Read the Docs site is published.

## Reference context

_Added by Optimize: API documentation and example code for this project's configuration._

### Python project with uv

Docs: [uv docs](https://docs.astral.sh/uv/)

```bash
uv init app && cd app
uv add fastapi uvicorn pydantic python-dotenv
uv run uvicorn main:app --reload
```

### FastAPI endpoint with Pydantic validation

Docs: [FastAPI tutorial](https://fastapi.tiangolo.com/tutorial/) · [Request body](https://fastapi.tiangolo.com/tutorial/body/)

```python
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

app = FastAPI()

class ItemIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    price: float = Field(gt=0)

class ItemOut(ItemIn):
    id: int

ITEMS: dict[int, ItemOut] = {}

@app.post("/items", response_model=ItemOut, status_code=201)
def create_item(item: ItemIn) -> ItemOut:
    created = ItemOut(id=len(ITEMS) + 1, **item.model_dump())
    ITEMS[created.id] = created
    return created

@app.get("/items/{item_id}", response_model=ItemOut)
def read_item(item_id: int) -> ItemOut:
    if item_id not in ITEMS:
        raise HTTPException(404, "Item not found")
    return ITEMS[item_id]
```

### SQLite with the standard library

Docs: [sqlite3 module](https://docs.python.org/3/library/sqlite3.html)

```python
import sqlite3

with sqlite3.connect("app.db") as db:
    db.execute("CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY, body TEXT NOT NULL)")
    db.execute("INSERT INTO notes (body) VALUES (?)", (body,))
    rows = db.execute("SELECT id, body FROM notes ORDER BY id DESC").fetchall()
```

### FastAPI on Vercel

Docs: [Vercel Python runtime](https://vercel.com/docs/functions/runtimes/python) · [vercel env](https://vercel.com/docs/cli/env)

```json
// vercel.json — app object exported from api/index.py
{
  "rewrites": [{ "source": "/(.*)", "destination": "/api/index" }]
}
```

### Google sign-in with Authlib

Docs: [Google OpenID Connect](https://developers.google.com/identity/openid-connect/openid-connect) · [Authlib Starlette client](https://docs.authlib.org/en/latest/client/starlette.html)

```python
from authlib.integrations.starlette_client import OAuth

oauth = OAuth()
oauth.register(
    "google",
    client_id=os.environ["GOOGLE_CLIENT_ID"],
    client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": os.environ.get("GOOGLE_SCOPES", "openid email profile")},
)

@app.get("/auth/google")
async def login(request: Request):
    return await oauth.google.authorize_redirect(request, os.environ["GOOGLE_REDIRECT_URI"])

@app.get("/auth/google/callback")
async def callback(request: Request):
    token = await oauth.google.authorize_access_token(request)
    user = token["userinfo"]
```

### Rotating file handler with EST timestamps

Docs: [RotatingFileHandler](https://docs.python.org/3/library/logging.handlers.html#rotatingfilehandler)

```python
import logging, time
from datetime import datetime
from logging.handlers import RotatingFileHandler
from zoneinfo import ZoneInfo

class EstFormatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):
        return datetime.fromtimestamp(record.created, ZoneInfo("America/New_York")).isoformat(timespec="milliseconds")

handler = RotatingFileHandler("logs/app.log", maxBytes=1_048_576, backupCount=3)
handler.setFormatter(EstFormatter("%(asctime)s [%(levelname)s] %(filename)s:%(lineno)d %(funcName)s - %(message)s"))
logging.getLogger().addHandler(handler)
```

### Stdout handler

Docs: [StreamHandler](https://docs.python.org/3/library/logging.handlers.html#streamhandler)

```python
import logging, sys

console = logging.StreamHandler(sys.stdout)
console.setLevel(logging.INFO)
logging.getLogger().addHandler(console)
```
