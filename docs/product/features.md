# Feature catalog

What watchmecode.live does today, written as marketing copy for review. Each feature has a headline, a short description, and the visual used on the product site (`static/marketing/`).

## Interviewing

### Join in one click
Candidates join with a 6-digit PIN or a signed invite link. They don't need an account or a download, and the link opens the shared IDE directly.
*Visual:* `join.webp`, the join tile with a PIN entered.

### A shared IDE in real time
Interviewer and candidate share the explorer, tabs, editor, console, and preview with equal control. Edits sync in about a second, and both sides see who changed what.
*Visual:* `ide-light.webp` / `ide-dark.webp`, plus `sync.gif` (two browsers editing the same file).

### 10 languages plus a web playground
Python, JavaScript, TypeScript, Java, C#, C++, C, Go, PHP, and Shell, plus an HTML/CSS/JS playground with a sandboxed live preview. `console.log` output from the preview shows up in the console.
*Visual:* `preview.webp`, a web session with the rendered preview.

### Run with automatic installs
Write `import requests` or `require('lodash')` and press Run. Packages install on demand, so nobody loses five minutes to environment setup.
*Visual:* `run.webp`, the console showing "Installing requests…" and then the program output.

### Live refresh
Turn on Live refresh and the code re-runs a moment after you stop typing. It's useful for test-driven exercises and UI work.
*Visual:* the toolbar toggle in `ide-light.webp`.

## Review

### Recording and playback
Press ● Record and both people see a REC badge. Afterward, replay every keystroke, file change, and run at 1x, 2x, or 4x, and scrub to any moment.
*Visual:* `playback.webp` and `playback.gif`.

### Candidate portal
Sessions are grouped by candidate. You can end a session, open its recordings, or reopen it after the interview to review the final code and every run.
*Visual:* `portal.webp`.

### Results bundle
Every session produces a summary (duration, runs, last output), a zip of the final code, and a playback link. These are ready for your ATS or a hiring-committee packet.
*Visual:* `results.webp`, the API results JSON.

## Assessments and labs

### Timed take-homes
Save starter files as reusable assessments. Candidates get a solo IDE with a countdown and a Submit button. Work is submitted when time runs out, and results go back to the ATS.
*Visual:* `take-home.webp`, the countdown toolbar.

### SQL lab
Seed tables from CSV files and query them with SQL. Results come back as formatted tables.
*Visual:* `sql-lab.webp`.

### Frontend lab
React with live preview and no build step, in the same shared IDE.
*Visual:* `react-lab.webp`.

### Whiteboard
A shared Excalidraw board for system design. It's saved with the session and replayable.
*Visual:* `whiteboard.webp`.

## Team and integrations

### Your own workspace
Every customer gets a subdomain (for example `ae.watchmecode.live`) with its own members, roles, API keys, and data. Enterprise customers get a fully dedicated instance.
*Visual:* `workspace.webp`, the workspace settings.

### Ashby
Scheduled interviews become sessions automatically. The candidate's join link is available as an email-template token, and results, the final code, and playback are written back to the candidate profile. Take-homes plug into Ashby's Assessments framework.
*Visual:* `integrations.webp`, the Ashby settings card.

### ModernLoop
Interviews scheduled in ModernLoop get a session automatically, and cancelled ones are cleaned up.
*Visual:* `integrations.webp`, the ModernLoop settings card.

### Public API
`POST /v1/pads` returns a candidate join link, so any scheduler or ATS can create interviews. OpenAPI docs come included.
*Visual:* `api.webp`, a curl request and its response.

## Trust

### Sign-in and security
- Google sign-in with PKCE for interviewers.
- Integration credentials encrypted at rest.
- HMAC-verified webhooks.
- Row-level security and a separate database schema per workspace.
- Clear privacy policy and terms.

*Visual:* the security strip on the home page.

### Built for every screen
Bento design, light and dark themes, and it works on phones.
*Visual:* `mobile.webp`.
