# Concierge

A local-first personal media library for recording experiences, changing opinions, ratings, progress, relationships, and the evidence behind recommendations.

## Product boundary

- **The SQLite database is authoritative.** Hermes memory is not a shadow copy of detailed media history.
- **Opinions retain provenance.** Explicit user statements, assistant inferences, imports, and manual edits remain distinguishable.
- **Assistant actions are proposal-first.** The assistant will query and draft reviewable changes through a narrow API, never write raw SQL.
- **Portability is a feature.** Versioned exports and restore verification are foundational work.

## Hermes package artifact

The repository root also carries the rough `0.1.16-dev` semantic-beta `concierge` Hermes skill package. It is deliberately distinct from the historical P6.5 local smoke artifact: P6.5 proved package-file plumbing, while this beta gate verifies the bounded automation and semantic-read behavior that the beta is meant to let other people test.

- [`SKILL.md`](SKILL.md) — direct skill entry point;
- [`manifest.yaml`](manifest.yaml) — package identity, version, support-file inventory, and side-effect declarations;
- [`CHANGELOG.md`](CHANGELOG.md) — artifact history;
- [`references/`](references/) — preflight, verification, contracts, MCP/cron boundaries, recovery, fixtures, compatibility, scoring, and limitations;
- [`scripts/concierge_package.py`](scripts/concierge_package.py) — explicit-target preflight/install/upgrade/uninstall/recovery commands;
- `backend/app/package_preflight.py`, `package_lifecycle.py`, `package_mcp.py`, `package_report.py`, and the beta runners — tested local package boundaries.

The beta retains ended-session observation capture, three independently explicit cron choices (backlog capture, recent ended-session capture, and automatic promotion), the existing `0.85` promotion rubric, and semantic Concierge query/read behavior. Capture remains proposal-first; canonical promotion is a separate package-owned path with before/after receipts and abstentions. It does not observe active sessions, generate numeric taste scores, or expose score writes. The manifest records `artifact_status: rough-semantic-beta`, `setup_status: beta_smoke_verified` after the exact synthetic gate, and `installable_claim: false` until a real versioned remote exists. Package commands require explicit disposable target paths and do not mutate the default profile.

The verification claim is intentionally narrow: Windows backend/frontend/build/package checks plus Ubuntu WSL2 smoke on the two designated PCs. This is not a native-Linux, provider-backed, active-session-observer, or ordinary private-use acceptance claim. The checkout has no configured Git remote, so there is no honest versioned raw `SKILL.md` URL to publish yet. Once a real remote and ref exist, the supported direct form is:

```text
hermes skills install <versioned-raw-SKILL.md-url>
```

Do not substitute a whole Hermes profile distribution for this skill path. Profile distributions are a separate product with different ownership and side-effect boundaries.

## Run the local app

For the normal local experience, the launcher builds the browser bundle when needed, starts the application and API together on loopback, and opens `http://127.0.0.1:4173/`:

```bash
uv run python scripts/launch.py
```

It installs frontend packages on the first run when `frontend/node_modules` is absent. Use `--no-browser` to leave tab-opening to the caller and `--port 8124` to choose another loopback port. Stop it with `Ctrl+C`; it never exposes the library beyond `127.0.0.1`.

Prerequisites are a supported Python with [`uv`](https://docs.astral.sh/uv/) and a current Node.js/npm installation. The launch command is intentionally agent-executable from a checkout: no manually managed database path, API terminal, or frontend terminal is required.

## Development state

Read [`PROJECT_STATUS.md`](PROJECT_STATUS.md) for the live restart point and [`DEFERRED_WORK.md`](DEFERRED_WORK.md) for deliberately postponed features.

## Current persistence foundation

`app.bootstrap.open_library(path)` creates the supplied data directory, migrates its database, and returns a ready `LibraryService`; `open_default_library()` honors only an absolute `%LOCALAPPDATA%/taste-database` compatibility override, otherwise using the same legacy-compatible directory name on Windows or POSIX. Existing installs keep that technical directory and SQLite filename so a branding change cannot strand user data. `app.api.create_app()` serves only a loopback application API: `/health`, `/export`, `/import`, managed `/backup` and `/backup/restore`, media read/write/archive routes, creator read/write routes, proposal review routes, and narrow recommendation history/outcome routes. `POST /media` is create-only: its SQLite insert is atomic, returns `201` for a new stable ID, and returns `409` without mutation if that ID already belongs to an active or archived record. Existing-record edits remain matching-ID `PUT /media/{item_id}` operations. No route exposes raw SQLite. Once recommendation history cites a media target or observation, destructive deletion or an evidence-removing edit returns `409` rather than orphaning the ledger; archive/restore remains available because it preserves identity.

`GET /media` supports case-insensitive title/alias search plus typed category/status filters; empty or whitespace-only title input means no title constraint. Archived media is excluded from ordinary collections by default and can be requested with `include_archived=true`. `GET /creators/{creator_id}/media` is the first relationship query: it returns stable-ID-ordered works directly credited to that creator, accepts an optional typed `role`, preserves the same archive opt-in, returns `404` for an unknown creator, and returns `200 []` for a known creator with no matching works. `GET /profile/rating-history` is a deterministic read projection, not a generated taste score: it returns each rated work's chronological history plus accepted, assistant-readable supporting, contradictory, and mixed/neutral context evidence; archived works require `include_archived=true`. `GET /profile/dimensions/{dimension}` provides the same cited evidence buckets for one case-insensitive observation dimension across works, with no opaque aggregation. `GET /profile/report` composes complete visible rating and progress histories, typed creator attribution, stored directed relationships whose source and target are both visible, and every visible cited dimension from one SQLite snapshot; progress, credits, and relationship types remain uninterpreted recorded context, and the report does not generate prose, affinity, claims, reverse links, or a score. `GET /duplicates/candidates` is the read-only polite-library-escort: it surfaces same-category title/alias identity collisions as explicitly uncertain, reviewable candidates and never merges or mutates records.

Portable exports use schema v1.8 when they contain typed rating/progress capture proposals, v1.7 when they contain a pending new-record media proposal without typed capture events, and otherwise retain the earliest schema version that can represent their collections. They preserve chronological rating history, reversible archive state, reusable creator identities and typed credits, a separate authoritative proposal queue, bounded media-first category capabilities, typed capture proposals, and a factual recommendation ledger. Recommendation occurrences link one canonical target to recommendation date/source/rationale and optional exact accepted observation evidence; separately identified append-only events preserve initial response, tried, opinion, and explicit success assessment without generating recommendations or analytics. Normal import merges recommendations create-only by stable ID and rejects conflicting rewrites; managed restore remains exact. Legacy media retain required consumption status; simple opinion categories such as paintings, comedians, and art museums share searchable taste evidence without fabricated lifecycle data. `LibraryService` validates import documents before atomic persistence and exports all top-level collections from one SQLite snapshot. `POST /backup` writes a managed, versioned JSON backup beneath the application data directory; `POST /backup/restore` replaces and verifies the complete snapshot before committing. See `docs/adding-a-category.md` for the agent-followable extension procedure.

Recommendation application writes are intentionally narrower than generic record editing. `POST /recommendations` creates one outcome-free immutable occurrence or replays the exact same stable ID; conflicting content returns `409`. `GET /recommendations` lists occurrences chronologically. `POST /recommendations/{recommendation_id}/outcomes` appends one typed event or replays it exactly; conflicting event IDs return `409`, unknown recommendations return `404`, and invalid chronology returns `422`. There are no recommendation update or delete routes, and no endpoint generates, ranks, or judges recommendations.

The first migration deliberately uses Python's standard `sqlite3` module and a compact, application-owned migration ledger. Query-oriented ORM models and Alembic remain planned additions once an access pattern requires a normalized schema. Query and future profile work are application-service-owned: profile claims must remain traceable to accepted, assistant-readable evidence rather than opaque scores or unreviewed proposals.

Hermes can now connect through the repo-owned stdio MCP server in `app.mcp_server`. The beta registry exposes nine tools: six factual reads, two pending-proposal reads, and one proposal-only mutation. The server searches and retrieves canonical records, exposes deterministic cited reports, and reads/submits reviewable proposals through `LibraryService` without exposing SQLite, review/promotion mutation, generated prose, or opaque scoring. See [`docs/hermes-mcp.md`](docs/hermes-mcp.md) for the current MCP boundary and [`docs/exploratory-cron-blueprint.md`](docs/exploratory-cron-blueprint.md) for the live-tested no-write automation handoff.

## Planned stack

- Python domain/API: FastAPI, Pydantic
- Database/search: SQLite with WAL + FTS5; SQLAlchemy/Alembic when normalized query models are introduced
- UI: React, TypeScript, Vite
- Tests: pytest, then frontend component/e2e tests where justified

## Local browser UI development

The production React/TypeScript/Vite editor lives in `frontend/`. It reads and writes only through the loopback FastAPI boundary; Vite proxies `/api` to `http://127.0.0.1:8000` during development. The library rail can open a new-entry draft whose stable ID is suggested from category/title until manually edited; creation uses the create-only POST boundary, while subsequent edits use PUT. Both modes support multiple alternate titles, append-only rating and progress history, and append-only evidence-backed manual observations with scope, polarity, provenance, privacy, and source context. Creator-credit editing loads reusable identities on demand, creates new identities through an atomic create-only POST that preserves existing names and aliases on stable-ID conflict, stops suggestions after manual ID editing, and adds or removes typed roles without flattening the media record. Typed relationships connect the selected record to another loaded media item, reject self/duplicate links, and preserve siblings and unrelated fields. A separate inference-review panel loads assistant observation proposals on demand, exposes their target, confidence, source excerpt, provenance, and privacy, and records explicit accept/reject outcomes without silently modifying media evidence. Accepted observation proposals remain visibly awaiting promotion until a second explicit action atomically appends their observation to canonical evidence, records the promoted observation ID, and makes retries idempotent. Rejected proposals, promoted observations, and accepted metadata proposals remain available in a collapsed, selected-record history ordered newest first; observation text and metadata values render as inert DOM text. Exact outgoing previews preserve untouched fields; every unfinished optional-detail draft participates in discard protection.

Run the API and frontend in separate terminals:

```bash
PYTHONPATH=backend uv run uvicorn app.api:create_app --factory --host 127.0.0.1 --port 8000
cd frontend && npm install && npm run dev -- --port 4173
```

Then open `http://127.0.0.1:4173/`. Frontend checks are `npm test` and `npm run build`. Existing stable IDs remain intentionally read-only because the matching-ID PUT contract has no atomic rename operation; a new entry's editable ID becomes immutable after a successful POST.

## Data

Local database, backups, and exports live outside this repository in the active profile's data root. The Windows compatibility default is:

```text
%LOCALAPPDATA%/taste-database/
```

That directory is intentionally not version controlled.
