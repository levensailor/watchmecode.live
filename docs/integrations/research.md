# Integration research notes

Findings that shaped the Ashby and ModernLoop integrations. Each item lists its source and what still needs a live sandbox check.

## Ashby

| Question | Finding | Source |
| --- | --- | --- |
| Can a join link appear in candidate invites without a partnership? | Yes, through a **Candidate** custom field of type *Short answer* with **Enable in Email Templates** turned on. Ashby exposes it as a substitution token in email templates and calendar invites. Needs the Legacy Plus, Plus, or Enterprise plan. Application custom fields can't be tokens, so watchmecode writes to a Candidate field. | [Substitution tokens](https://docs.ashbyhq.com/substitution-tokens), [custom field tokens](https://www.ashbyhq.com/product-updates/email-template-substitution-tokens) |
| What does `interviewSchedule.update` `extraData` do? | It's private key-value storage on the schedule or event (keys up to 100 characters, values up to 512, under 1 KB total, no PII) for partner reference IDs. Candidates never see it, so it can't carry a join link. | `interviewScheduleCreate` webhook schema |
| `interviewScheduleCreate` / `interviewScheduleUpdate` payload | `data.interviewSchedule` has `id`, `status` (`Scheduled`, `Cancelled`, …), `applicationId`, `interviewStageId`, and `interviewEvents[]`. Each event has `id`, `startTime`, `endTime`, `meetingLink`, `feedbackLink`, and `interviewers[]` (each with `id`, `email`, `firstName`, `lastName`). There's no candidate ID, so it comes from `application.info`. | [Webhook reference](https://developers.ashbyhq.com/reference/interviewschedulecreatewebhook) |
| Webhook authentication | `Ashby-Signature: sha256=<hex HMAC-SHA256 of the raw body>` using the per-webhook secret. | [Authenticating webhooks](https://developers.ashbyhq.com/docs/authenticating-webhooks) |
| `assessment.update` fields | `assessment_id` and `timestamp` (ms) are required. `assessment_status`, `assessment_result`, `assessment_profile_url`, `cancelled_reason`, and `metadata[]` are typed values `{identifier, label, type, value}`, where `type` is one of `boolean_success`, `numeric_score`, `numeric_duration_minutes`, `string`, `url`, and a few others. Sending `assessment_result` marks the assessment complete and requires `assessment_profile_url`. | [assessment.update](https://developers.ashbyhq.com/reference/assessmentupdate) |
| Partner endpoints Ashby calls | `assessment.list` returns `{success, results:[{assessment_type_id, name, description}]}`. `assessment.start` sends `{assessment_type_id, candidate{ashby_id, first_name, last_name, email, ashby_profile_url}, application{ashby_id, status}, job{ashby_id, name, req_id, ashby_job_url, hiringTeam[]}}` and expects `{success, results:{assessment_id, update_request?}}`. `assessment.cancel` sends `{assessment_id}`. `customFields.fetch` is optional. | Partner reference pages |
| Results back without a partnership | `candidate.createNote` (HTML), `candidate.uploadFile` (multipart: `candidateId` and `file`), and `assessment.addCompletedToCandidate` (`partnerId`, typed `result` and `metadata`). `X-On-Behalf-Of` attributes a note to an Ashby user found with `user.search`. | API reference |

## ModernLoop

| Question | Finding | Source |
| --- | --- | --- |
| Do ModernLoop schedules reach Ashby? | Yes. ModernLoop's Ashby API key has read and write access to **Interviews**, and schedules it creates are written to Ashby. Ashby's `interviewSchedule*` webhooks should therefore fire for them, so watchmecode treats Ashby webhooks as the primary path. | [ModernLoop Ashby integration](https://help.modernloop.io/articles/8776421019-ashby-integration) |
| Platform API events | `GET /v1/interview-events` (Bearer token) supports `updated_at_gte`, `statuses`, and a `cursor`. Each event has `id`, `schedule_id`, `name`, `start_at`, `end_at`, `status` (`SCHEDULED`, `COMPLETED`, `CANCELED`), `application{id, candidate{id, name}, job, stage}`, `interviewers[]`, and `video_meeting_url`. Candidates include `primary_email` and `ats{type, id}`. There are no webhooks. | [openapi.json](https://api.modernloop.io/openapi.json) |
| Coding links in ModernLoop invites | Only for the coding tools ModernLoop builds (CoderPad, HackerRank, CodeSignal, Codility). Adding watchmecode takes a partnership; see [modernloop.md](modernloop.md). | [Collaboration links](https://help.modernloop.io/articles/3516144274-collaboration-links) |

## Still to confirm in a live sandbox

- Ashby: the exact retry behavior when our webhook returns non-2xx (we return 2xx for anything handled or ignored).
- Ashby: whether `assessment.addCompletedToCandidate` accepts an arbitrary `partnerId` from a non-partner key. It's configurable, and failures are logged without blocking.
- ModernLoop to Ashby: confirm webhook delivery end to end with a test candidate.
