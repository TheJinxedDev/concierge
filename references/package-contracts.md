# Concierge package contracts

The package preserves the semantic-beta boundaries already verified in the repository.

## Data authority

- SQLite through `LibraryService` is authoritative for canonical media/taste data.
- Hermes memory and Obsidian are not alternate media databases.
- Raw SQLite access is not a supported agent boundary.

## Capture, promotion, and proposals

- Automatic capture is proposal-first and separately enabled through independent
  backlog and recent completed-session jobs.
- A candidate starts at `needs_review`; pending data is not canonical data.
- Existing works use a canonical target ID. New works use a targetless, complete `media_item` proposal.
- Stable proposal IDs are caller-owned; exact retries are no-ops and same-ID payload drift is a conflict.
- Source context, provenance, confidence, dates, ambiguity, and contradiction notes remain visible.
- Capture may preserve a clear consumption fact without inventing a rating or opinion.
- The beta capture bridge is deliberately narrow: exact canonical title/alias plus
  an explicit consumption/reaction cue. It does not infer numeric ratings,
  broad metadata, or a new targetless work from session text.
- A separate package-owned promotion job applies the `0.85` beta threshold and
  atomically promotes only eligible pending proposals; abstentions remain
  reviewable and are reported.

## Typed events

`rating_event` and `progress_event` are explicit typed proposals with exact canonical targets, event dates, stable event/idempotency identities, and append-only promotion semantics. They are not generic metadata values.

The semantic-beta MCP registry does not expose a typed-event proposal-write tool. These contracts are available through the application service and portable import/export boundary only; the sole MCP mutation remains the legacy `submit_pending_proposal` path until a separately reviewed typed mutation is added.

## Deterministic workflow

Source freshness, cursor, action identity, claims, locks, batch limits, retry classes, and run reports remain application-owned. Unknown commit outcomes hold the cursor and never count as success.

Completed-session discovery has a monotonic per-pass boundary. `discovery_as_of`
and the exact `discovered_sources` ledger may widen for a later `pending_only`
run, but never regress; discovery progression does not advance the processing
cursor. A source is processable only after it is durably appended to that
ledger. The `off + process_existing` manual lane deliberately keeps its initial
boundary and retires its exact job after verified catch-up instead of widening
into ongoing capture.

## Package ownership

The native-Hermes cron plans are exact: `concierge-backlog-capture`,
`concierge-session-capture`, and `concierge-auto-promotion`, each with its own
`concierge/automation/<kind>` owner marker, `concierge` skill, Sunday 04:00
host-local/no-catch-up schedule, explicit local
delivery, workdir, and fingerprinted prompt. Familiar names alone never
establish ownership. Concierge persists only the explicit preferences and plan;
Hermes creates, reads back, and removes jobs through its public scheduler. The
backlog job is finite and reports readiness for user-directed removal after a
verified terminal pass; the recent job is ongoing and proposal-only; promotion
is a separate canonical mutation path outside MCP.

Canonical sources for the full contracts are `backend/app/capture_contract.py`,
`backend/app/capture_envelope.py`, `backend/app/capture_state.py`,
`backend/app/automation_cron_identity.py`, `backend/app/automatic_capture.py`,
`backend/app/automation_promotion.py`, and ADRs 0007–0012.
