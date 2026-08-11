---
name: concierge
description: Set up and operate Concierge, a local-first semantic media archive with proposal-first capture and factual MCP queries.
version: 0.1.16-dev.2
---

# Concierge public-beta onboarding

Concierge is a rough public beta for keeping media history, reactions, and taste
evidence locally. It has a visible proposal queue instead of silently rewriting
your library. Use it, prod it, and report what feels awkward or breaks.

## Privacy and data boundaries

- Work only in the Hermes profile the user selected. Never inspect or mutate
  another profile, the default profile by assumption, or unrelated local data.
- Canonical media, observations, ratings, progress, and relationships are durable
  user data. Use Concierge's API/MCP surface; never edit SQLite directly.
- `submit_pending_proposal` creates only a `needs_review` inbox item. It is not a
  canonical write or confirmation.
- No active-session observer exists. Scheduled capture may inspect only ended,
  prior sessions through Hermes' native `session_search` tool.
- Concierge never generates numeric taste scores. Preserve explicit evidence or
  abstain rather than filling gaps.
- Automatic promotion is opt-in, uses the documented `0.85` beta rubric, records
  canonical before/after receipts, and leaves uncertain candidates pending.
- Use native Hermes tools (`session_search`, `cronjob`) and public `hermes` CLI
  commands. Do not search for or install a second Hermes backend, `croniter`, or
  private `cron`/`hermes_state` modules.

## Quick setup

The tagged repository checkout is the runtime. Start from the immutable
`v0.1.16-dev.2` tag, not an older release or a cached branch URL.

1. Confirm which Hermes profile should own Concierge. Resolve that profile's
   absolute `<HERMES_HOME>` with the public Hermes CLI, plus an absolute
   `<LOCALAPPDATA>` and an external `<CONCIERGE_ENV>` directory. If the target is
   ambiguous, ask—do not guess.
2. Clone or download `https://github.com/TheJinxedDev/concierge` at tag
   `v0.1.16-dev.2` into a temporary checkout. From its root run the single
   **Quick setup** command shown in that checkout's README with the three paths.

   The bootstrap performs package preflight, creates a separate locked Python
   environment, installs the exact owned runtime, and initializes only
   `<HERMES_HOME>/concierge-data`. It strips inherited Hermes Python paths and
   does not copy credentials, inspect sessions, configure providers, create cron
   jobs, or generate scores.
3. Require `action=concierge_ready_for_hermes_registration`. Keep the returned
   artifact hash and paths for feedback or uninstall.
4. Register `initialization.mcp` using native `hermes mcp add`, then run
   `hermes mcp test taste_database`. Confirm the returned profile paths and nine
   tools before accepting Hermes' interactive registration prompt. A same-name
   entry with different command, arguments, environment, or profile is a
   conflict—never overwrite it silently.

The MCP surface provides factual semantic reads plus one proposal-only write. It
does not expose review, deletion, raw SQL, cron-policy changes, or score writes.

## Explain automation before asking

Start fully manual. Explain and ask for a separate yes/no answer to each job:

1. **One-time backlog capture** — examines a bounded set of existing, completed
   conversations and creates pending proposals from clear evidence. If enabled,
   also ask whether to `process_existing` or `start_fresh`. It never watches an
   active chat or promotes.
2. **Ongoing ended-session capture** — periodically examines newly completed
   sessions after a durable watermark and creates reviewable proposals. It does
   not promote or invent scores.
3. **Automatic promotion** — periodically applies the `0.85` beta rubric to
   eligible pending proposals and records canonical before/after receipts.
   Uncertain candidates remain pending.

Do not offer or accept promotion alone: at least one capture source (backlog or
ongoing capture) must be enabled. An unanswered question is not consent.

After all three answers are explicit, rerun that same README quickstart command
with:

```text
--backlog-cron <yes|no> --recent-capture-cron <yes|no> --promotion-cron <yes|no> --backlog-policy <process_existing|start_fresh>
```

This safely reuses the owned installation. The short console receipt names the
full `receipt_path`; read `automation.native_hermes_jobs.plans` there. It does
not create jobs itself. Create only the approved plans through Hermes' native
`cronjob` tool (or the public profile-scoped `hermes cron` CLI), attach the
`concierge` skill and returned workdir, then read each job back. Never let a CLI
fallback target another/default profile.

## Everyday behavior

- For scheduled capture, use `session_search` on ended prior sessions only.
  Abstain when evidence is weak, ambiguous, or belongs to the active chat.
- Search canonical media first. Existing works get observation proposals;
  clearly distinct identified works may get complete `media_item` proposals.
  Neither is created canonically by capture.
- Preserve explicit consumption facts without inventing opinions, dates,
  relationships, or numeric ratings.
- For media-shaped questions, query Concierge before memory: use `search_media`,
  `get_media`, `get_taste_report`, `get_rating_history`, or
  `get_dimension_profile`. Label pending proposals as pending.

## Beta limits and feedback

This beta was smoke-tested on Windows. Linux is plausible but untested and not
yet supported. There is no active-session capture, hidden provider setup,
automatic numeric scoring, or claim of proven real-model behavior across every
Hermes release. Concierge uses Hermes' public CLI/tool contracts and has no
upper Hermes version pin; report incompatibilities with the Hermes version,
Concierge tag, and non-secret setup receipt.

## Uninstall

Run the repository README's uninstall command from the original checkout, not
from inside the installed runtime (which Windows may keep open). Use the exact
version, artifact hash, and profile paths from the setup receipt.

This removes only package-owned runtime and skill files. It does not silently
delete the user's library, MCP entry, or Hermes jobs; remove those separately
only with explicit user direction.
