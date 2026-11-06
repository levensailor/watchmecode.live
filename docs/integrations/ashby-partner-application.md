# Ashby partner application (draft)

Ready-to-send material for joining Ashby's Assessments partner program and requesting a native "attach live pad link to invites" option. Every endpoint below is live in production.

## Email to Ashby partnerships

> **Subject:** Assessments partner request: watchmecode.live (live coding interviews and take-homes)
>
> Hi Ashby partnerships team,
>
> watchmecode.live is a live code-share IDE for technical interviews: interviewer and candidate edit and run code together in 10 languages, with recordings and playback. We'd like to be listed as an Assessments partner, and we'd like to ask about the live-interview pad link option you offer CoderPad and CodeSignal.
>
> We've implemented the Assessments Partner API at `https://watchmecode.live/partners/ashby`:
>
> - `POST /assessment.list`: the customer's take-home templates
> - `POST /assessment.start`: creates a timed take-home, emails the candidate, and returns `assessment_id` plus an `update_request`
> - `POST /assessment.cancel`: ends the take-home and returns `cancelled_reason`
> - `POST /customFields.fetch`: returns an empty list for now
>
> Customers authenticate with a watchmecode API key that they create in watchmecode and paste into Ashby (Basic Auth, key as the username). We call `assessment.update` with `Started`, then send `assessment_result`, `assessment_profile_url`, and metadata (time spent, runs, final-code download, playback URL) on submit. The final code is also attached with `candidate.uploadFile`.
>
> For live interviews we use customer-configured `interviewScheduleCreate` and `interviewScheduleUpdate` webhooks, verified with the `Ashby-Signature` HMAC, to create one session per interview event. The join link goes into a candidate custom field (used as an email-template token) and a note, and results come back as a note and a file when the interview ends. A native "attach watchmecode link to invites" checkbox would remove the custom-field step for customers.
>
> Docs: https://github.com/levensailor/watchmecode.live/blob/main/docs/integrations/ashby.md
> Security and data handling: https://watchmecode.live/privacy
>
> Could you share the next steps (sandbox, security review, listing assets)?
>
> Thanks,
> levensailor, watchmecode.live

## Listing details

| Field | Value |
| --- | --- |
| Partner name | watchmecode.live |
| Category | Assessments (coding) and live interview tool |
| Base URL | `https://watchmecode.live/partners/ashby` |
| Authentication | HTTP Basic with a watchmecode API key as the username (per customer organization) |
| Ashby API permissions needed from customers | Candidates (write) for `assessment.update`, `candidate.uploadFile`, and `candidate.createNote`. Interviews (read) for live-interview webhooks. |
| Logo | `static/favicon-120.png` (120×120) |
| Support | https://github.com/levensailor/watchmecode.live/issues |

## Data handling summary

- **Received from Ashby:** candidate name, email, and Ashby IDs; job name; hiring-team emails (used to grant session access).
- **Stored:** in Supabase Postgres (US East), in a dedicated `watchmecode` schema with row-level security enabled. Customer Ashby API keys are encrypted with Fernet (`INTEGRATION_ENCRYPTION_KEY`).
- **Sent back to Ashby:** assessment status and result, a link to the session, the final code as a zip, and a playback link.
- **Deletion:** on request, within 30 days (see the privacy policy).
