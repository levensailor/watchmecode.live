# Architecture

## System

```mermaid
flowchart LR
  interviewer[Interviewer browser]
  candidate[Candidate browser]
  google[Google OpenID Connect]
  subgraph vercel [Vercel iad1]
    api["FastAPI app (api/index.py)"]
    runner["Code runner (subprocess, /tmp workspace)"]
  end
  db[("Supabase Postgres, schema watchmecode")]
  interviewer -->|session cookie| api
  candidate -->|bearer token from PIN| api
  interviewer -.->|OIDC + PKCE| google
  api --> db
  api --> runner
```

| Piece | File |
| --- | --- |
| Settings | `app/config.py` |
| Logging (stdout + rotating file, EST) | `app/logging_config.py` |
| Google sign-in, candidate tokens | `app/auth.py` |
| Storage (SQLite or Postgres) | `app/repository.py`, `sql/` |
| HTTP routes | `app/routes.py` |
| Runner and language support | `app/runner.py`, `app/languages.py` |
| UI | `static/index.html`, `static/app.js`, `static/app.css` |
| Workspace routing and per-workspace repositories | `app/tenancy.py`, `app/platform.py`, `sql/platform*.sql` |
| Sign-in hub handoff (Ed25519) | `app/handoff.py`, `app/auth.py` |
| Plans, metering, Stripe | `app/plans.py`, `app/billing.py`, `app/platform_api.py` |
| Product site | `app/site.py`, `templates/site/`, `static/site.css`, `static/site.js` |
| Labs and sandbox runner | `app/labs/sql_lab.py`, `app/sandbox_runner.py`, `static/whiteboard.html` |

## Workspaces

Every request passes through `TenantMiddleware`, which maps the `Host` header to a tenant and sets two context variables: the tenant, and its `Repository`. `app.state.repo` is a proxy that resolves to the current request's repository, so routes, integrations, and background tasks need no tenant plumbing. Shared workspaces store data in Postgres schema `t_<slug>` (a SQLite file per workspace locally). The pre-workspace data stays in `watchmecode` as the `app` workspace.

```mermaid
sequenceDiagram
  participant B as Browser on ae.watchmecode.live
  participant H as watchmecode.live (hub)
  participant G as Google
  B->>H: /auth/google?tenant=ae&next=/
  H->>G: OIDC + PKCE
  G->>H: callback (email, name)
  H->>H: member of ae, or email domain allowed?
  H->>B: 303 ae…/auth/handoff?token (Ed25519, 2 min, single use, audience-bound)
  B->>B: session cookie bound to tenant "ae"
```

Session cookies are host-only and also record the workspace they were issued for, so a cookie copied to another subdomain is refused. Memberships and plans live in the `platform` schema; plan checks (`require_feature`, `require_active`) return 402 with an upgrade message.

## Interviewer flow

```mermaid
sequenceDiagram
  actor I as Interviewer
  participant UI as Browser
  participant API as FastAPI
  participant G as Google
  participant DB as Database
  I->>UI: Interviewer sign in with Google
  UI->>API: GET /auth/google
  API-->>UI: 302 to Google (state, nonce, PKCE S256)
  UI->>G: consent
  G-->>UI: 302 /auth/google/callback?code
  UI->>API: GET /auth/google/callback
  API->>G: exchange code + verifier
  G-->>API: id_token, userinfo
  API-->>UI: session cookie, 303 to /
  I->>UI: Generate PIN for candidate
  UI->>API: POST /api/sessions
  API->>DB: insert session (unique active PIN), starter files
  API-->>UI: 201 id, pin
```

## Candidate join flow

```mermaid
sequenceDiagram
  actor C as Candidate
  participant UI as Browser
  participant API as FastAPI
  participant DB as Database
  C->>UI: enter 6-digit PIN
  UI->>API: POST /api/join
  API->>DB: find active session by PIN
  alt PIN matches an active session
    API-->>UI: session + signed token (scoped to session id)
    UI->>API: GET /api/sessions/{id}/state (Bearer token)
    API-->>UI: files, runs, cursor
  else unknown or ended PIN
    API-->>UI: 404
  end
```

## Run and sync

Vercel functions cannot hold WebSockets, so both browsers poll an append-only change feed. Each request carries an `X-Client-Id`, and a browser skips its own events.

```mermaid
sequenceDiagram
  participant A as Participant A
  participant API as FastAPI
  participant DB as Database
  participant R as Runner
  participant B as Participant B
  A->>API: PUT /files (debounced 350 ms)
  API->>DB: upsert file + event file_upsert
  loop every ~1 s
    B->>API: GET /changes?since=cursor
    API-->>B: events after cursor
  end
  B->>B: patch editor (minimal diff)
  A->>API: POST /run (manual or live refresh)
  API->>R: sync files to workspace, install imports, execute with timeout
  R-->>API: stdout, stderr, exit code
  API->>DB: insert run + event run
  B->>API: GET /changes
  API-->>B: run event
```

## Storage

Tables: `sessions`, `files`, `runs`, and `events`, the change feed. A partial unique index keeps PINs unique among active sessions. On Postgres, every connection runs `SET LOCAL search_path` to the `watchmecode` schema. That setting lasts only for the transaction, which is safe behind Supabase's transaction-mode pooler.
