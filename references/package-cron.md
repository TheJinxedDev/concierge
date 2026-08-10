# Concierge automation boundary

Concierge has three independent, opt-in automation plans. Installation creates
none of them. Concierge stores the explicit preferences and returns a plan; the
selected Hermes profile creates and owns jobs through the public `cronjob` tool
or `hermes cron` CLI.

| Plan | What it does | What it never does |
|---|---|---|
| `concierge-backlog-capture` | One bounded pass over selected, completed prior sessions | Reads an active session, promotes, or writes a score |
| `concierge-session-capture` | Ongoing bounded reviews of newly ended sessions | Reads a raw Hermes database or writes canonical media |
| `concierge-auto-promotion` | Applies the documented 0.85 beta rubric to eligible pending proposals | Runs without a backlog/recent capture source or invents scores |

Every generated plan has a stable name, owner marker, schedule, local delivery,
`concierge` skill, workdir, and prompt fingerprint. A familiar name alone is
not ownership: native Hermes readback must confirm the full plan before use.

## Consent gate

Onboarding explains and records separate yes/no choices for backlog capture,
ongoing completed-session capture, and automatic promotion. A missing answer is
not consent. `process_existing` also requires backlog capture.

Automatic promotion is rejected unless either backlog or ongoing capture is
enabled. There is no `promotion_only` setup lane: on a fresh profile it would
be an empty, misleading job.

## Native Hermes boundary

Concierge does not import a private Hermes scheduler, inspect a Hermes source
checkout, open a Hermes state database, or install `croniter`. Scheduled capture
uses the native `session_search` tool; job creation, readback, and removal use
native Hermes scheduling tools. If the native tool cannot prove an ended-session
boundary or a job's exact identity, it must abstain and report the blocker.

Capture creates only reviewable proposals with source context and uncertainty.
Promotion is a separate job and must report canonical before/after receipts;
scheduler `last_status` is not a receipt. A finished backlog job reports that it
is ready for user-directed removal rather than attempting to mutate its own
scheduler record.
