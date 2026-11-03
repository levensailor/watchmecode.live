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
| POST | `/api/sessions` | `{candidate_name, language}` | 201 session with `pin` |
| GET | `/api/sessions` | `?candidate_name=` filter | Your sessions |
| GET | `/api/sessions/{id}` |  | Session with `files` and `runs` |
| POST | `/api/sessions/{id}/end` |  | Ends the session and retires the PIN |

## Shared (owning interviewer, or candidate bearer token)

| Method | Path | Body / query | Returns |
| --- | --- | --- | --- |
| POST | `/api/join` | `{pin}` | `{session, token}`; 404 for unknown PINs |
| GET | `/api/sessions/{id}/state` |  | Files, recent runs, change cursor, role |
| GET | `/api/sessions/{id}/files` |  | All files |
| PUT | `/api/sessions/{id}/files` | `{path, content}` | Upserted file |
| DELETE | `/api/sessions/{id}/files` | `?path=` file or folder | Deleted paths |
| POST | `/api/sessions/{id}/files/rename` | `{old_path, new_path}` | Old-to-new mapping (folders included) |
| POST | `/api/sessions/{id}/run` | `{entry_path}` | `{stdout, stderr, exit_code, duration_ms, …}` |
| GET | `/api/sessions/{id}/changes` | `?since=` cursor (`-1` returns only the cursor) | `{cursor, events}` |

Candidates send `Authorization: Bearer <token>`. Every client may send `X-Client-Id`, which is echoed in events so a browser can skip its own changes.

## Meta

| Method | Path | Returns |
| --- | --- | --- |
| GET | `/api/health` | Status and storage backend |
| GET | `/api/info` | App name, tagline, Google sign-in availability |
| GET | `/api/languages` | Supported languages, entry files, and extensions |
