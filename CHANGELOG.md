# Changelog

Feature changes, newest first. Times are America/New_York.

## 2026-11-03

- **17:20** Add Privacy Policy and Terms of Service pages with footer links, a 120x120 PNG logo for Google OAuth branding, and remove the landing-page count tiles.
- **14:16** Add README, contributing guide, changelog, and a MkDocs site with Mermaid diagrams so interviewers and contributors can get started quickly.
- **14:10** Skip pip installs for packages already cached so repeat Python runs on Vercel stay fast and quiet.
- **14:09** Fall back to pip --target for Python auto-install so third-party imports work on Vercel, where the uv binary is not bundled.
- **14:04** Isolate Postgres tables in a dedicated schema so watchmecode can share an existing Supabase database safely.
- **14:00** Add pytest coverage and GitHub Actions CI so PIN, session, file, runner, and auth behavior stays verified on every push.
- **13:58** Add the Bento landing page, candidate portal, and shared IDE so interviewers and candidates can code together in the browser.
- **13:47** Add session storage, PIN join, file and change-feed APIs so interviewer and candidate share one workspace.
- **13:43** Add Google OpenID Connect sign-in with PKCE so interviewers authenticate without managing passwords.
- **13:42** Scaffold the FastAPI app with EST rotating logs so every request is traceable from the first deploy.
