# Integrations roadmap

**Already shipped:**
- [Ashby](../integrations/ashby.md): live interviews through webhooks, and take-homes through the Assessments Partner API.
- [ModernLoop](../integrations/modernloop.md): Platform API poller.
- The public pads API.

## Top 10 to build next

| # | Integration | Effort | Data in | Data out | Mechanism | Docs |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | Greenhouse Assessment API | S–M | Test requests (candidate, job) | Status, score, playback link | Partner API, approval through partners@greenhouse.io | [docs](https://developers.greenhouse.io/assessment.html) |
| 2 | Lever | M | Stage-change webhooks | Notes, feedback, PDF | OAuth app, sandbox then review | [docs](https://hire.lever.co/developer/usecases) |
| 3 | Kombo unified Assessment API | M | Orders from Workday, SuccessFactors, SmartRecruiters, Avature, and others | Results | One integration covers many ATSs (Merge has no assessment endpoints) | [docs](https://docs.kombo.dev/assessment/introduction) |
| 4 | Slack app | S | Slash command to create a pad | Session-complete posts with playback | Slack app, unlisted first; the Marketplace needs 10+ workspaces | — |
| 5 | AI evaluation summaries | S–M | Code, runs, events | Rubric summary and AI-use signals in ATS notes | OpenAI or Anthropic API | — |
| 6 | Calendar auto-provisioning | M | Interview events | Join link added to invites | Google Calendar API, Microsoft Graph | — |
| 7 | Google Meet add-on | M | Meeting context | IDE in the side panel or main stage | Meet add-ons SDK | [docs](https://developers.google.com/workspace/meet/add-ons/guides/quickstart) |
| 8 | Zoom App (Collaborate mode) | M–L | Meeting context | IDE in Zoom | Zoom Apps, review required | [docs](https://developers.zoom.us/docs/zoom-apps/guides/collaborate-mode/) |
| 9 | WorkOS SSO and directory sync | S–M | Users and groups from Okta, Entra, Google | Login | WorkOS API (Enterprise tier) | [docs](https://workos.com/docs/directory-sync) |
| 10 | Microsoft Teams meeting app | M | Meeting context | IDE in a Teams meeting | Teams meeting stage apps | [docs](https://learn.microsoft.com/en-us/microsoftteams/platform/apps-in-teams-meetings/build-apps-for-teams-meeting-stage) |

## Later

- **ATS marketplaces:**
  - [SmartRecruiters](https://developers.smartrecruiters.com/docs/partners-assessment-api-2021)
  - [Workable](https://workable.readme.io/page/assessment-providers)
  - [Teamtailor](https://partner.teamtailor.com/partners/)
  - [iCIMS](https://developer-community.icims.com/getting-started/integrating-icims)
  - [Gem](https://help.gem.com/databases/gem-help-center/gems-api) (API key, no assessment framework)
- **Scheduling:** Calendly and Cal.com booking webhooks. GoodTime's public API can't register a video provider, so use calendar auto-provisioning instead.
- **Developer signal:** a GitHub App that creates take-home repos from templates. GitHub Classroom was retired on August 28, 2026.
- **Procurement:** an [AWS Marketplace SaaS listing](https://docs.aws.amazon.com/marketplace/latest/userguide/saas-integrate-contract.html) for Enterprise.
- **Automation:** outbound webhooks (`session.ended`, `assessment.submitted`), which unlock Zapier, Make, and n8n.
- **Interview intelligence:** Metaview (MCP or Zapier only), BrightHire (REST plus read-only MCP), and Fireflies (audio by URL) can't ingest coding events. Link our playback from their notes instead.
