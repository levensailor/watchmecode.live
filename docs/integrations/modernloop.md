# ModernLoop

watchmecode works with ModernLoop in two ways:

1. **Today (no partnership needed):** watchmecode polls ModernLoop's Platform API every 10 minutes and creates a session for each newly scheduled interview.
2. **Native coding tool (needs a ModernLoop partnership):** ModernLoop calls watchmecode's `POST /v1/pads` while scheduling, the same way it calls CoderPad, so the join link lands in the candidate's schedule and invites.

If you also use Ashby, ModernLoop writes its schedules into Ashby, and the [Ashby integration](ashby.md) picks them up through webhooks. The poller skips candidates whose ATS is Ashby (configurable) to avoid duplicate sessions.

## Set up the poller

1. In ModernLoop, create a Platform API token with read access to interview events, candidates, and users.
2. In watchmecode, go to **Organization settings → ModernLoop**, paste the token, and press **Connect**.
3. Optional settings:

   | Setting | Effect |
   | --- | --- |
   | Job stages | Only create sessions for these job stage IDs (blank means all) |
   | Default language | Starter files for created sessions |
   | Skip candidates synced from Ashby | On by default, so Ashby webhooks own those interviews |

4. Press **Test** to check the token, then **Sync now** to run a sync immediately.

### What the poller does

- Each run reads `GET /v1/interview-events?updated_at_gte=<last sync>`. The first run looks back 1 day.
- For each `SCHEDULED` event that is still upcoming (and isn't a debrief), it creates a live session for the candidate. Interviewers are added as members of the watchmecode org, matched by their ModernLoop user email.
- `CANCELED` events end their sessions.
- Runs come from a Vercel cron (`/api/cron/modernloop`, every 10 minutes), authenticated with the `CRON_SECRET` bearer token.
- Errors appear as **Last error** in Organization settings.

Interviewers find the session in the candidate portal. The candidate join link is stored on the session and appears in the session's API record (`GET /v1/pads/{id}`).

## Partnership pitch

**To:** ModernLoop partnerships / integrations team
**Subject:** Add watchmecode.live to ModernLoop's coding tools

> Hi ModernLoop team,
>
> watchmecode.live is a live code-share IDE for technical interviews: 10 languages, shared editing and running, recordings with 1x/2x/4x playback, and timed take-homes. Our customers schedule with ModernLoop and want watchmecode links generated during scheduling, the way CoderPad, HackerRank, CodeSignal, and Codility work today.
>
> Our create-pad API is live and follows the same shape as those integrations:
>
> - `POST https://watchmecode.live/v1/pads` with `Authorization: Bearer <customer API key>`
> - Body: `title` (your link-title tokens fit here), `candidate_name` (optional: name, `Application 12345`, or omitted), `candidate_email` (optional), `language`, and `mode` (`live` or `take_home`)
> - Response: `id`, `join_url` (candidate link, no account or PIN needed), `interviewer_url`, `pin`
> - `GET /v1/pads/{id}` and `POST /v1/pads/{id}/end` return results: duration, runs, last output, playback URL, and a final-code zip URL
>
> Customers create the API key in watchmecode's Organization settings. We'd love to get on your coding tools list. What does your partner process look like?
>
> Thanks,
> levensailor, watchmecode.live

### How `/v1/pads` maps to ModernLoop's collaboration-link settings

| ModernLoop setting | watchmecode field |
| --- | --- |
| Link title tokens (`{{JOB_NAME}}`, `{{JOB_STAGE_NAME}}`, `{{ATS_APPLICATION_ID}}`, …) | `title` |
| Candidate name: real name, application ID, or omit | `candidate_name` (defaults to "Candidate") |
| Candidate email: send or omit | `candidate_email` (optional) |
| Coding link added to the schedule | `join_url` |

### Example

```bash
curl -s https://watchmecode.live/v1/pads \
  -H "Authorization: Bearer $WATCHMECODE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"title":"Backend Engineer - Onsite Coding (12345)","candidate_name":"Application 12345","language":"python"}'
```

```json
{
  "id": "3f2c…",
  "pin": "482913",
  "candidate_name": "Application 12345",
  "title": "Backend Engineer - Onsite Coding (12345)",
  "language": "python",
  "mode": "live",
  "status": "active",
  "join_url": "https://watchmecode.live/j/eyJzaWQi…",
  "interviewer_url": "https://watchmecode.live/#/s/3f2c…",
  "created_at": "2026-11-06T14:00:00.000+00:00",
  "results": null
}
```
