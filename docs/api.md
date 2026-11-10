# API

The live OpenAPI explorer is at [watchmecode.live/docs](https://watchmecode.live/docs). Every body is a Pydantic model.

## Auth

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/auth/google` | Starts Google sign-in (PKCE S256) |
| GET | `/auth/google/callback` | Stores name, email, and picture in the session cookie |
| GET | `/auth/logout` | Clears the session |
| GET | `/api/me` | Current interviewer, or 401 |

## Interviewer (session cookie required)

| Method | Path | Body / query | Returns |
| --- | --- | --- | --- |
| POST | `/api/sessions` | `{candidate_name, language, org_id?, problem?: {company, window, slug}}` | 201 session with `pin`; with `problem`, the starter files are the AI-written problem (502 if it can't be written, 429 past the hourly limit) |
| GET | `/api/problems/companies` |  | `{source_url, companies: [{name, windows: [{id, label}]}], languages}` |
| GET | `/api/problems` | `?company=&window=` | `{company, window, problems: [{slug, title, difficulty, frequency, acceptance, topics}]}` |
| POST | `/api/problems/generate` | `{company, window, slug, language}` | Interviewer preview `{name, summary, files, notes, source, cached}`; cached per workspace |
| GET | `/api/sessions` | `?candidate_name=`, `?org_id=` | Your sessions and your organizations' sessions |
| GET | `/api/sessions/{id}` |  | Session with `files` and `runs` |
| POST | `/api/sessions/{id}/end` |  | Ends the session, retires the PIN, stops any recording, and pushes results to connected integrations |
| GET | `/api/sessions/{id}/files.zip` |  | Final code as a zip |
| GET | `/api/sessions/{id}/recordings` |  | Recordings, newest first |
| POST | `/api/sessions/{id}/recordings` |  | 201 starts a recording; 409 if one is already running |
| POST | `/api/sessions/{id}/recordings/{rid}/stop` |  | Stops the recording |
| GET | `/api/sessions/{id}/recordings/{rid}` |  | Playback data: starting files plus the edits, file changes, and runs in the recording |

## Shared (owning interviewer, or candidate bearer token)

| Method | Path | Body / query | Returns |
| --- | --- | --- | --- |
| POST | `/api/join` | `{pin}` | `{session, token}`; 404 for unknown PINs |
| POST | `/api/join/link` | `{token}` from `/j/{token}` | `{session, token}`; 404 invalid or expired, 410 ended |
| POST | `/api/sessions/{id}/submit` |  | Submits a take-home (409 for live sessions) |
| GET | `/api/sessions/{id}/state` |  | Files, recent runs, change cursor, role |
| GET | `/api/sessions/{id}/files` |  | All files |
| PUT | `/api/sessions/{id}/files` | `{path, content}` | Upserted file |
| DELETE | `/api/sessions/{id}/files` | `?path=` file or folder | Deleted paths |
| POST | `/api/sessions/{id}/files/rename` | `{old_path, new_path}` | Old-to-new mapping (folders included) |
| POST | `/api/sessions/{id}/run` | `{entry_path}` | `{stdout, stderr, exit_code, duration_ms, …}` |
| GET | `/api/sessions/{id}/changes` | `?since=` cursor (`-1` returns only the cursor) | `{cursor, events}` |

Candidates send `Authorization: Bearer <token>`. Every client may send `X-Client-Id`, which is echoed in events so a browser can skip its own changes. Take-home candidates get 403 `Time is up` once the time limit passes; their work is submitted automatically.

## Public API (`/v1`, org API key)

Send `Authorization: Bearer wmc_…`, or HTTP Basic with the key as the username. Pads are scoped to the key's organization.

| Method | Path | Body / query | Returns |
| --- | --- | --- | --- |
| POST | `/v1/pads` | `{candidate_name?, candidate_email?, title?, language, mode: live\|take_home, time_limit_minutes?, interviewer_email?, files?}` | 201 `{id, pin, join_url, interviewer_url, …}` |
| GET | `/v1/pads` | `?status=active\|ended&limit=` | Recent pads |
| GET | `/v1/pads/{id}` |  | Pad, with `results` once ended |
| POST | `/v1/pads/{id}/end` |  | Ends the pad and returns results |
| GET | `/v1/pads/{id}/results` |  | Duration, runs, last output, playback URL, files URL |
| GET | `/v1/pads/{id}/files.zip` |  | Final code |

## Organizations (session cookie; admin-only writes noted)

| Method | Path | Notes |
| --- | --- | --- |
| GET, POST | `/api/orgs` | List your orgs, or create one (you become admin) |
| GET | `/api/orgs/{id}` | Members, API keys (admins only), integration status |
| POST, DELETE | `/api/orgs/{id}/members[/{email}]` | Admin. The last admin can't be removed. |
| GET, POST, DELETE | `/api/orgs/{id}/api-keys[/{key_id}]` | Admin. The key is returned once on create. |
| PUT, DELETE | `/api/orgs/{id}/integrations/ashby` | Admin. `{api_key?, settings, rotate_webhook_secret?}` |
| GET | `/api/orgs/{id}/integrations/ashby/webhook-secret` | Admin. Webhook URL and secret for Ashby. |
| POST | `/api/orgs/{id}/integrations/{ashby\|modernloop}/test` | Admin. Checks the stored credentials. |
| PUT, DELETE | `/api/orgs/{id}/integrations/modernloop` | Admin. `{api_token?, settings}` |
| POST | `/api/orgs/{id}/integrations/modernloop/sync` | Admin. Runs a sync now. |
| GET, POST, PUT, DELETE | `/api/orgs/{id}/assessments[/{template_id}]` | Take-home templates (members) |

## Integration endpoints (called by third parties)

| Method | Path | Caller and auth |
| --- | --- | --- |
| POST | `/integrations/ashby/webhook/{org_slug}` | Ashby webhooks; `Ashby-Signature: sha256=<HMAC>` |
| POST | `/partners/ashby/assessment.list` · `assessment.start` · `assessment.cancel` · `customFields.fetch` | Ashby Assessments; Basic Auth with a watchmecode API key |
| GET | `/api/cron/modernloop` | Vercel cron; `Authorization: Bearer $CRON_SECRET` |
| GET | `/j/{token}` | Candidate invite link; redirects into the app |

## Meta

| Method | Path | Returns |
| --- | --- | --- |
| GET | `/api/health` | Status and storage backend |
| GET | `/api/info` | App name, tagline, Google sign-in availability |
| GET | `/api/languages` | Supported languages, entry files, and extensions |
