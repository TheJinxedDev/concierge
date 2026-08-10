---
name: concierge
description: Operate Concierge's local-first media library through its review-first API and MCP surfaces.
version: 0.1.16-dev
---

# Concierge agent onboarding

## Safety contract

- Canonical media, creator, observation, rating, progress, relationship, and recommendation records are durable user data. Do not use raw SQLite or bypass `LibraryService`.
- Conversation capture is **proposal-first**. `submit_pending_proposal` creates only a `needs_review` inbox item; its receipt is not confirmation and it does not change canonical media.
- Never accept, reject, import, restore, archive, or overwrite a record merely because a conversation supplied plausible information. Promotion is also explicit user review by default; only an independently enabled `fully_auto` promotion job may apply the small documented beta rubric and threshold.
- Preserve a specific, non-secret `source_context`, use stable caller-owned IDs, and set bounded confidence from 0 through 1. Reuse an ID only for a deliberate retry; collisions are rejected, not overwritten.
- When identity, category, status, rating, user intent, or source attribution is ambiguous, ask or keep the candidate incomplete rather than inventing a canonical record.

## First-run onboarding

Run this section only when the user asks to set up Concierge. Work inside the
currently selected Hermes profile's own data root, and never touch another profile's MCP, cron,
sessions, memories, or database. The old `%LOCALAPPDATA%\\taste-database` path is a
technical compatibility fallback only; it is not the product name or a license to
use machine-wide data.

The automation choices are independent and explicit:

- `fully_manual` means both ongoing recent capture and automatic promotion are
  off. The database, GUI, and factual MCP surface remain available.
- `semi_auto` means recent completed-session capture is on and automatic
  promotion is off. Capture creates only `needs_review` proposals.
- `fully_auto` means recent completed-session capture and the separate automatic
  promotion cron are both on. Promotion still uses the beta threshold and may
  leave candidates pending; it never watches the active conversation.
- `promotion_only` is allowed when a user wants a promotion pass over an
  existing pending inbox without enabling ongoing capture.

The three scheduled jobs are separate: a finite backlog pass, an ongoing recent
completed-session capture pass, and a later automatic promotion pass. A `false`
answer is persisted just as deliberately as a `true` answer. No active-session
observer is created by any lane.

### 1. Resolve the installed runtime and profile data root

Read the package `installation.json` from the installed package envelope. Use
its `runtime_path` as `<CONCIERGE_INSTALL>`, read its `artifact_directory`
(currently `artifact`), and set `<CONCIERGE_RUNTIME>` to that child directory.
Verify that `<CONCIERGE_RUNTIME>` contains `pyproject.toml`, `backend/`, and
`scripts/concierge_setup.py` before running anything. Resolve the active
profile's Hermes home from `HERMES_HOME` or `hermes config path`; set
`<CONCIERGE_DATA>` to `<HERMES_HOME>\\concierge-data`. Both paths must be
absolute and profile-scoped. If either path is ambiguous, stop and ask rather
than falling back.

### 2. Initialize the local database

Set `<CONCIERGE_ENV>` to the sibling profile-scoped environment path
`<CONCIERGE_RUNTIME>\\..\\.venv`. It must be outside the immutable artifact;
do not let `uv` create `.venv` under `<CONCIERGE_RUNTIME>`.

Run the package setup helper once:

```text
UV_PROJECT_ENVIRONMENT=<CONCIERGE_ENV> uv run --locked --directory <CONCIERGE_RUNTIME> --project <CONCIERGE_RUNTIME> python scripts/concierge_setup.py initialize --runtime-root <CONCIERGE_RUNTIME> --data-dir <CONCIERGE_DATA>
```

A `database_initialized` or exact `database_ready` result is acceptable. Read
back the reported database path. This creates/migrates only the selected local
library; it does not import user data, enable capture, or create a cron job.

### 3. Register and verify the profile-scoped MCP server

Use the exact `mcp` command/args returned by the setup helper. The expected
shape is:

```text
hermes mcp add taste_database --command uv --env UV_PROJECT_ENVIRONMENT=<CONCIERGE_ENV> PYTHONDONTWRITEBYTECODE=1 --args run --locked --directory <CONCIERGE_RUNTIME>\\backend --project <CONCIERGE_RUNTIME> python -m app.mcp_entry --data-dir <CONCIERGE_DATA>
hermes mcp test taste_database
```

`--env` must carry the exact external environment entry returned by the setup
helper, and `--args` must be the final Hermes option. Do not replace `app.mcp_entry` with
`app.mcp_server`: the entry point pins the data directory before the server is
imported. If `taste_database` already exists, inspect its exact command/args;
an exact owned entry is a no-op after a successful test, while a same-name drift
is a conflict and must not be overwritten. Verify the nine-tool registry and
one disposable search plus proposal/write/readback call before proceeding.

At this point onboarding is still `fully_manual`. Do not create a scheduler
just because MCP works.

### 4. Explain manual entry, then choose three independent automation consents

Before asking about automation, explain the manual path plainly: the user can
open the local Concierge GUI, add or edit a media record, review captured
proposals, accept or reject a proposal, and explicitly promote an accepted
proposal. The GUI is the visible review surface; the MCP read tools are the
agent's factual query surface. A user can always paste a link and say what they
want saved, even with every cron disabled.

Use **How should Concierge be set up?** only as an orientation question; answer
it with the three independent decisions below, never with one inferred mode.

Ask each scheduled job as a separate yes/no decision. Do not compress these into
one mode question, infer a missing answer, or treat one consent as consent for
another job:

The backlog question must distinguish **process existing completed conversations**
from **start fresh from the first run**; these are separate from the yes/no
answer about whether the backlog cron runs.

1. **Backlog cron:** should Concierge run one finite pass to process existing
   completed conversations that already exist? If yes, ask separately whether to
   `process existing` or `start fresh`; this job retires after a verified
   terminal pass.
2. **Recent capture cron:** should Concierge periodically crawl only newly
   completed sessions after its durable watermark and create reviewable
   proposals? It never reads an active conversation and never promotes.
3. **Automatic promotion cron:** should Concierge periodically apply the beta
   confidence rubric to eligible pending proposals? Explain that this is the
   fully automatic beta boundary: threshold `0.85`, high-confidence supported
   candidates may be promoted, and occasional incorrect promotions are accepted
   for now. Abstentions remain pending; metadata and inferred rating scores do
   not pass the beta gate.

If backlog consent is yes, offer the optional short interview about the user's
core favorite movies, television, anime, games, or other media. The interview
is optional and must happen before the first backlog pass; it is never a hidden
prerequisite for using Concierge. If the user accepts, conduct it before the
first backlog scheduler tick: ask for roughly three to five exact titles across
the user's core media interests, the category/medium for each, and one short
reason it matters. Search existing canonical records first. Treat answers as
user-directed onboarding input: save only when the user explicitly asks to save
an entry, use the normal reviewable proposal/manual-entry path, and never
silently promote an interview answer. If the user skips or pauses the interview,
record that choice and continue without blocking setup.

Persist all three boolean answers, the backlog policy, and the interview choice
in one profile-scoped automation-preferences record. Derive the displayed lane
from those booleans; do not store a single lossy mode as the source of truth.

### Clarification timeout and no-guess rule

A clarification timeout is not an answer. If a clarification window closes
without an explicit user choice, do not guess, silently choose a lane, or turn
the timeout into `start_fresh`, `process_existing`, `fully_manual`, or any
other policy value. Do not spend another model/tool turn trying to resolve the
missing choice automatically.

Stop onboarding at the unanswered decision. Do not write or complete the
capture enablement decision, create or modify a capture job, initialize capture
state, or run a backlog pass. Leave harmless setup already completed for the
selected profile—such as the local database or MCP registration—unchanged.
Send one short user-facing request naming the exact missing choice. For an
unanswered backlog question, say: **“Setup is paused. I still need your
explicit backlog choice: reply `process existing` or `start fresh`. No capture job was enabled.”** When the user replies, resume from the unanswered decision
without re-asking an independently answered question.

Use the setup helper with all three explicit cron answers:

```text
UV_PROJECT_ENVIRONMENT=<CONCIERGE_ENV> uv run --locked --directory <CONCIERGE_RUNTIME> --project <CONCIERGE_RUNTIME> python scripts/concierge_setup.py save-automation-preferences --runtime-root <CONCIERGE_RUNTIME> --data-dir <CONCIERGE_DATA> --hermes-home <HERMES_HOME> --decision-id <DECISION_ID> --backlog-cron <yes|no> --recent-capture-cron <yes|no> --promotion-cron <yes|no> --backlog-policy <process_existing|start_fresh> --favorite-media-interview <yes|no> --confirmation "I explicitly choose Concierge automation"
```

The helper rejects missing or ambiguous answers and persists explicit `no`
values. `start_fresh` is valid when backlog cron is disabled because it records
the boundary policy for a later explicit enablement; it does not create a job.
The helper creates only the exact owned jobs corresponding to the three enabled
answers: `concierge-backlog-capture`, `concierge-session-capture`, and
`concierge-auto-promotion`.

The created prompts carry the absolute runtime, Hermes home, data directory, and
package-owned runner command. The backlog runner is independent of the recent
capture choice; the promotion runner is independent of both capture choices.
The package command's exit status and JSON readback are the run result, not the
scheduler's last-status field. A verified finite backlog run removes only its
exact owned `concierge-backlog-capture` record.

The helper reconciles the profile-scoped Hermes cron store in the same command.
Read back every created job's exact role, owner metadata, schedule, delivery,
and prompt fingerprint, and require a ready scheduler result. Never adopt a
same-name job with different ownership or fingerprint.

The `off + process_existing` job is temporary and exact-owned. After a verified
`complete` or clean `no_visible_evidence` run with no remaining backlog,
retryable/blocked claims, canonical mutation, or failed proposal/report/state
readback, the worker must remove that exact job and verify that it is absent.
Any partial, blocked, failed, unknown-commit, lock, source, or uncertain
readback result keeps the job for a safe retry. Never remove a same-name or
fingerprint-conflicted job.

### 5. Validate with synthetic completed sessions before any live use

Before allowing the backlog job to read real Hermes history, run the packaged
synthetic fixture harness. It must seed only the profile-scoped disposable
library, run through the real Hermes cron store/scheduler boundary, create
review-only proposals, persist the capture report/state/watermark, and prove
canonical IDs are unchanged. Synthetic cases are not permission to inspect the
active session or the user's real session database.

Run the harness with the same external environment:

```text
UV_PROJECT_ENVIRONMENT=<CONCIERGE_ENV> uv run --locked --directory <CONCIERGE_RUNTIME> --project <CONCIERGE_RUNTIME> python scripts/run_synthetic_completed_sessions.py --hermes-home <HERMES_HOME> --runtime-root <CONCIERGE_RUNTIME> --data-dir <CONCIERGE_DATA>
```

The synthetic run must report:

- completed source records only, after the durable per-pass discovery boundary;
- exact proposal readback with `review_state=needs_review`;
- durable action/cursor/watermark state;
- unchanged canonical media before/after;
- no active-session observation, score invention, default-profile access, or
  automatic promotion during the capture-only synthetic pass.

If the synthetic or real completed-session adapter is unavailable, stop at
`PARTIAL` rather than substituting active-session observation or a guessed
source. Automatic promotion is tested separately through its rubric and
promotion job; it is never smuggled into the capture worker.


The trigger boundary is explicit. In the active conversation, submit one complete proposal using `submit_pending_proposal` only when the user explicitly asks to make/save an entry. An ordinary media remark—even a clear one—must not silently create a proposal just because the agent noticed useful evidence. A standing mode selection authorizes only its separately scheduled completed-session job; it does not authorize an active-conversation observer.

The scheduled path is batch-only: in `semi_auto`, a package-owned worker may
read completed prior sessions after its durable session/message watermark and
create bounded `needs_review` proposals. Its initial discovery boundary is a
bootstrap boundary, not a permanent rejection fence: each later run takes a
fresh `as_of`, appends newly discovered exact source cursors atomically, and
processes only the resulting durable ledger. Discovery may advance while a
blocked processing watermark remains held; no source outside the ledger may
be processed. `fully_manual` has no **ongoing** gathering job, but it may
explicitly opt into one bounded existing-backlog review pass. Manual entry and
explicit capture requests remain available in all three product settings.
`fully_auto` is available only when the user separately enabled recent capture
and promotion; it does not authorize active-session observation.

1. Search canonical records first. If one exact existing record is identified, propose an `observation` against its stable ID with `assistant_inferred` provenance and source context.
2. If the conversation establishes a distinct new work sufficiently to name and categorize it, submit a `media_item` proposal containing the proposed record. Do **not** create the work directly.
3. Treat direct user statements as evidence, not as permission to infer every missing field. Do not manufacture ratings, progress, credits, relationships, or detailed observations.
4. **Consumption is independently valuable from evaluation.** When the user clearly establishes that they have consumed, are consuming, caught up with, finished, dropped, or otherwise have lifecycle history for a work, preserve the supported status/progress fact even if rating, observations, dates, credits, relationships, and other fields are absent. The v1.8 typed `rating_event`/`progress_event` queue is an application import/export/service contract, not an MCP mutation in this beta; do not claim that `submit_pending_proposal` can write typed lifecycle events. Until a separately reviewed typed MCP mutation exists, keep such lifecycle candidates out of the MCP write path rather than silently coercing them.
5. Keep provisional emotional reactions and tentative scores separate from the consumption fact. A user can have a canonical or reviewable consumption record with no rating at all; tentative reactions belong in source context or a reviewable observation, not an invented final rating.
6. Tell the user the proposal is pending review unless the separately enabled
   promotion cron later promotes it through the beta rubric. The Concierge UI
   can inspect it, accept/reject it, and manually promote it when automatic
   promotion is disabled.

## Automatic querying contract

Automatic querying is part of the Concierge beta. A fresh agent must route
media-shaped questions to Concierge's canonical read surface without requiring
the user to say "use Concierge": what the user watched, what episode or
installment they are on, what they rated, what they seem to value from recorded
evidence, and requests for recommendations all require a bounded Concierge read
before memory or general web search. Use `search_media`/`get_media` to identify
the work, then `get_taste_report`, `get_rating_history`,
`get_dimension_profile`, or progress-bearing `get_media` data as applicable.

Recommendation prose is generated from returned canonical evidence and clearly
separates fact from judgment. Do not write a recommendation ledger entry unless
the user explicitly asks to record that recommendation. Pending proposals are
not canonical evidence and must be labeled as pending if the user asks about
captured-but-not-promoted material.

## Read surface

Use bounded factual tools before relying on memory:

- `search_media` finds canonical title/alias candidates.
- `get_media` retrieves one exact record; archives are opt-in.
- `get_taste_report`, `get_dimension_profile`, `get_rating_history`, and `list_evidence_dimensions` return deterministic cited projections, not taste conclusions.
- `list_pending_proposals` and `get_proposal` return bounded, exact legacy/typed proposal views with canonical-versus-proposed separation; pending is the default review-state and archives are opt-in. MCP proposal reads redact source context and hide private/recommendation-excluded proposed observation content even though the service retains the write-time evidence for review.

The beta MCP boundary has nine registered tools: the six factual reads, the two proposal reads, and the one `submit_pending_proposal` mutation. Review, acceptance, promotion, import, restore, archive, delete, raw SQL, score writes, and automation-policy mutation remain excluded from MCP discovery. Automatic promotion is a package-owned scheduler path, not an MCP write tool.

## Local verification

From the repository root:

```bash
uv run pytest -q
cd frontend && npm test && npm run build
UV_PROJECT_ENVIRONMENT=<CONCIERGE_ENV> uv run --locked fastmcp list --command "uv run --locked --directory <CONCIERGE_RUNTIME>/backend --project <CONCIERGE_RUNTIME> python -m app.mcp_entry --data-dir <CONCIERGE_DATA>" --input-schema --json
```

Do not treat a successful MCP tool registration as proof that a write is canonical. Verify the returned proposal state and inspect it through the API/UI review queue before any user-directed acceptance or promotion.

## Versioned package boundary

This root skill is the rough `concierge` semantic-beta direct Hermes skill, version `0.1.16-dev`. It includes the package lifecycle, semantic MCP reads, ended-session observation capture, three independently explicit package-owned cron choices, and the separate `0.85` automatic-promotion path. Capture is proposal-first and promotion emits canonical before/after receipts; no active-session observer or generated numeric taste score is included. Historical P6.5 smoke evidence is not the beta verdict. Installation never silently targets the default profile; the onboarding section is the explicit, user-directed path for setup and consent.

The package manifest is [`manifest.yaml`](manifest.yaml). This private repository now has a versioned beta ref. For an authenticated collaborator, the supported direct-install URL is:

```text
hermes skills install https://raw.githubusercontent.com/TheJinxedDev/concierge/v0.1.16-dev/SKILL.md
```

The repository is private, so this URL requires GitHub access on the machine running Hermes. Do not present it as an anonymous public-install URL.

For a read-only local preflight from the repository root:

```bash
uv run python scripts/concierge_package.py preflight --check-commands --report .hermes/concierge-beta/install-report.json
```

The preflight reports the artifact hash, current Hermes/Python/uv evidence when requested, exact data/runtime/profile paths, and publication status. Without `--report` it creates no directories and performs no profile, MCP, cron, capture, or database mutation. With `--report`, it writes only the explicitly requested report path and marks that narrow filesystem side effect in the receipt; the selected environment remains untouched. P6.2–P6.5 describe the historical package plumbing and smoke layers; the current `0.1.16-dev` beta gate is the bounded automation/semantic-read smoke described in the package contracts and known limitations.

The package-file lifecycle is explicit and target-scoped:

```bash
uv run python scripts/concierge_package.py install --artifact-root . --hermes-home <temporary-hermes-home> --local-appdata <temporary-local-appdata>
uv run python scripts/concierge_package.py uninstall --version 0.1.16-dev --expected-artifact-hash <read-from-install-receipt> --hermes-home <temporary-hermes-home> --local-appdata <temporary-local-appdata>
```

Mutating package-file commands require explicit target paths and refuse to adopt drifted files. They own only the package runtime and `skills/concierge` tree. The onboarding helper then performs separately visible, profile-scoped database/consent setup, while Hermes MCP and cron remain independently read back and conflict-checked. `scripts/concierge_package.py` is a repository/runtime command, not a claim that a direct skill install silently configures another profile.

Read the bounded support documents before implementing later setup slices:

- [`references/package-preflight.md`](references/package-preflight.md)
- [`references/package-verification.md`](references/package-verification.md)
- [`references/package-contracts.md`](references/package-contracts.md)
- [`references/package-troubleshooting.md`](references/package-troubleshooting.md)
- [`references/package-mcp.md`](references/package-mcp.md)
- [`references/package-cron.md`](references/package-cron.md)
- [`references/package-upgrade-recovery.md`](references/package-upgrade-recovery.md)
- [`references/fixture-catalog.md`](references/fixture-catalog.md)
- [`references/compatibility-matrix.md`](references/compatibility-matrix.md)
- [`references/scoring-disabled-policy.md`](references/scoring-disabled-policy.md)
- [`references/known-limitations.md`](references/known-limitations.md)
