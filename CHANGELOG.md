# Changelog

Feature changes, newest first. Times are America/New_York.

## 2026-11-06

- **11:00** Launch the product site on the apex (home, features, labs, integrations, pricing with a monthly/annual toggle, customers, security), a signup flow with a live subdomain check, a workspace picker for sign-in, and a script that creates the Stripe products, prices, meter, and webhook.
- **10:45** Add Stripe billing (Checkout, Customer Portal, signed webhooks, metered overage), plan entitlements, interview-credit metering, a workspace signup API with a subdomain check, and a Workspace & billing view for members, sign-in domains, and plans.
- **10:40** Sign in once at the apex with Google and hand off to each workspace subdomain with single-use Ed25519 tokens that dedicated instances can verify with only the public key.
- **10:35** Serve each customer workspace from its own subdomain and database schema, with a platform store for tenants, memberships, and usage, workspace-bound sign-in, and a migration runner.
- **10:20** Capture product screenshots and a playback GIF from a seeded demo workspace for the marketing site.
- **10:00** Document the Ashby and ModernLoop integrations, the public API, and organization settings, and update the privacy policy and terms for ATS data and integration credentials.
- **09:55** Poll ModernLoop for scheduled interviews via Vercel cron, accept anonymous candidates on the pads API, and add the ModernLoop partnership pitch.
- **09:50** Add the Ashby Assessments Partner API for timed take-homes: list templates, start (with candidate email), cancel, and report Started and Submitted results back to Ashby.
- **09:45** Create sessions from Ashby interview schedules through signed webhooks, write the join link to a candidate custom field and a note, and push results, the final code, and playback back to Ashby.
- **09:40** Add organization settings, an org picker for new sessions, candidate invite links, and a timed take-home mode with Submit so teams can manage integrations and assessments in the browser.
- **09:30** Add organizations, API keys, encrypted integration credentials, signed join links, and a public pads API as the foundation for Ashby and ModernLoop integrations.

## 2026-11-03

- **17:35** Add interview recording for interviewers and a playback view with 1x/2x/4x speed and seeking, so interviews can be reviewed after they end.
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
