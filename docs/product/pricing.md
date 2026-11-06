# Pricing proposal

## Market benchmarks (public pricing, 2026)

| Vendor | Entry tier | Growth tier | Notes |
| --- | --- | --- | --- |
| CoderPad | Starter $80/mo annual (5 interviews or tests a month) | Team $400/mo annual (30/mo) | Unlimited users. ATS, SSO, and API are Enterprise only. Overage $30. |
| HackerRank | Starter $79/mo annual (5 attempts a month) | Pro $419/mo annual (30/mo) | ATS starts at Pro. Overage $15–$20. |
| CodeSignal | Build $79/mo annual (5 credits a month) | Grow $479/mo annual (35/mo) | Credit pool. Basic ATS starts at Grow. |
| CodeInterview | $69/mo annual (8/mo) | $299/mo annual (40/mo) | Has a free tier (2 a month). |
| HackerEarth | Growth $99/mo (10 credits) | Scale $399/mo (25) | ATS starts at Scale. |
| Coderbyte | $99/mo (20 invites) | Pro $1,999/yr unlimited | Enterprise from $14,995/yr. |

**Patterns:**
- Pricing is metered by volume with unlimited seats.
- Overage is $15–$30 per use.
- ATS integration gates the middle tier.
- SSO/SCIM and audit logs gate Enterprise, with a floor of about $12K–$15K/yr.
- Trials are usually 14 days with no card.

## Proposal

Every plan has unlimited interviewers. Usage is metered in **interview credits**:

- 1 credit when a candidate joins a live session or starts a take-home
- 2 credits when the session uses a sandbox lab (a full VM)
- Interviewers practicing on their own doesn't use credits

### Free trial (14 days, no card)
- All Team features
- 15 credits
- A real subdomain from day one
- Upgrading keeps every session

### Starter: $49/mo annual or $59 monthly
- 10 credits a month; overage $12 per credit
- All 10 languages plus the web playground
- Recordings and playback, invite links, candidate portal
- Take-homes (3 a month)
- Email support

### Team: $249/mo annual or $299 monthly (most popular)
- 50 credits a month with one-month rollover; overage $10 per credit
- Everything in Starter, plus:
  - ATS and scheduling integrations: Ashby and ModernLoop, then Greenhouse and Lever as they ship
  - Public API and outbound webhooks, Slack
  - Unlimited take-homes, browser labs (SQL, frontend, whiteboard), sandbox project labs
  - AI evaluation summaries
  - Custom branding

### Enterprise: custom, from about $1,000/mo ($12K/yr)
- Everything in Team, plus:
  - A **dedicated isolated instance**: its own deployment, database, and region
  - SSO/SAML and SCIM through WorkOS, audit logs
  - Infrastructure and cloud labs, custom domain
  - DPA, 99.9% SLA, volume credits, priority support

## Why this wins

- **About 3x more interviews per dollar:** Team is $249 for 50 a month, against CoderPad Team at $400 for 30 and HackerRank Pro at $419 for 30.
- **ATS integrations on Team, not Enterprise:** that's the most common reason teams upgrade elsewhere.
- **Unlimited interviewers on every plan:** no seat math.
- **Isolation is a feature:** every workspace has its own database schema, and Enterprise gets its own instance.

## Implementation

- **Plan entitlements:** `app/plans.py`.
- **Stripe prices:** monthly and annual for Starter and Team, created by `scripts/stripe_setup.py`.
- **Overage:** reported as Stripe meter events (`interview_credits`) for usage beyond the allowance.
