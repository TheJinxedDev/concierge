---
name: concierge
description: Set up and operate Concierge, a local-first semantic media archive with proposal-first capture and factual MCP queries.
version: 0.1.16-dev.1
---

# Concierge public-beta onboarding

Concierge is a **rough public beta**: local-first media history and taste evidence
with a visible proposal queue instead of silent edits. It is meant to be used,
prodded, and reported on—not mistaken for finished software.

## Non-negotiable boundaries

- Canonical media, observations, ratings, progress, and relationships are durable
  user data. Use the Concierge API/MCP surface; never edit SQLite directly.
- `submit_pending_proposal` creates only a `needs_review` item. It is not a
  canonical write or confirmation.
- No active-session observer exists. Scheduled capture may inspect only ended,
  prior sessions through Hermes' native `session_search` tool.
- Concierge never generates numeric taste scores. Preserve clear evidence or
  abstain; do not fill gaps with plausible-looking data.
- Automatic promotion is opt-in, uses the beta's documented `0.85` rubric, and
  must report canonical before/after receipts. Abstentions stay pending.
- Do not search for a Hermes source checkout, a Hermes virtualenv, private
  `cron`/`hermes_state` modules, or `croniter`. Concierge is a Hermes
  application: use native Hermes tools (`session_search`, `cronjob`) and public
  `hermes` CLI commands only.

## Install from this public-beta repository

The repository checkout is the beta runtime. The direct skill alone is not a
second copy of Hermes and does not contain a hidden Hermes backend.

### Start with a clean Python environment

Before any `uv run` command, clear inherited `PYTHONPATH` and `VIRTUAL_ENV`.
They can point at Hermes' own Python runtime and corrupt Concierge's separate
environment. In a POSIX-compatible Hermes terminal, run:

```text
unset PYTHONPATH VIRTUAL_ENV
```

In PowerShell, run:

```text
Remove-Item Env:PYTHONPATH,Env:VIRTUAL_ENV -ErrorAction SilentlyContinue
```

Do this once per terminal session before continuing below.

1. Work in the user-selected Hermes profile. If the target profile is unclear,
   ask; never assume or mutate another/default profile.
2. Clone or download the repository into a fresh temporary directory. Record the
   exact commit used for the test receipt.
3. From that checkout, run the read-only preflight:

   ```text
   uv run --locked python scripts/concierge_package.py preflight --check-commands
   ```

   Stop if it reports a checksum, inventory, symlink, traversal, secret, or
   compatibility problem.
4. Choose explicit, profile-scoped absolute paths:
   - `<HERMES_HOME>` — the selected profile's Hermes home (`hermes config path`
     can help resolve it);
   - `<LOCALAPPDATA>` — that profile's local application-data parent;
   - `<CONCIERGE_ENV>` — an external environment directory, not `.venv` inside
     the checkout or installed artifact.
5. Install the reviewed checkout into the selected profile only:

   ```text
   UV_PROJECT_ENVIRONMENT=<CONCIERGE_ENV> uv run --locked python scripts/concierge_package.py install --artifact-root . --hermes-home <HERMES_HOME> --local-appdata <LOCALAPPDATA>
   ```

   Require an `installed` or exact-owned `noop` JSON receipt. Keep its
   `runtime_path` and artifact hash for uninstall or feedback. For every
   `uv --directory`/`--project` command below, set `<CONCIERGE_PACKAGE_ROOT>`
   to the receipt's `runtime_project_path`, which is the installed
   `runtime_path/artifact` directory—not the versioned ownership directory.
6. Initialize an empty, profile-scoped Concierge library:

   ```text
   UV_PROJECT_ENVIRONMENT=<CONCIERGE_ENV> uv run --locked --directory <CONCIERGE_PACKAGE_ROOT> --project <CONCIERGE_PACKAGE_ROOT> python scripts/concierge_setup.py initialize --runtime-root <CONCIERGE_PACKAGE_ROOT> --data-dir <CONCIERGE_DATA> --environment-dir <CONCIERGE_ENV>
   ```

   This creates only the selected library. It does **not** inspect history,
   create a cron job, enable capture, or generate scores.

## Add and verify the semantic MCP server

Use the exact command/args returned in the setup receipt. Its expected shape is:

```text
HERMES_HOME=<HERMES_HOME> hermes mcp add taste_database --command uv --env UV_PROJECT_ENVIRONMENT=<CONCIERGE_ENV> PYTHONDONTWRITEBYTECODE=1 PYTHONPATH= VIRTUAL_ENV= --args run --locked --directory <CONCIERGE_PACKAGE_ROOT>/backend --project <CONCIERGE_PACKAGE_ROOT> python -m app.mcp_entry --data-dir <CONCIERGE_DATA>
HERMES_HOME=<HERMES_HOME> hermes mcp test taste_database
```

`hermes mcp add` performs discovery and asks for a confirmation before saving
the entry. Confirm only after its command, profile-scoped paths, and nine-tool
list match the setup receipt. This is intentionally interactive: if the agent's
terminal cannot answer the confirmation prompt, stop and ask the user rather
than piping an automatic approval or reporting a false setup failure.

If a same-name MCP entry already exists, inspect it first. An exact entry can be
reused after a successful test; different command, arguments, environment, or
profile is a conflict—do not overwrite it.

The MCP surface provides semantic, factual reads and one proposal-only write.
It does not expose review, deletion, raw SQL, cron-policy changes, or score
writes.

## Explain the automation choices before asking

Start fully manual. Explain these independent optional jobs in plain language,
then ask for a separate **yes/no** answer to each:

1. **One-time backlog capture** — looks through a bounded set of *existing,
   completed* conversations and turns only clear evidence into pending
   proposals. If yes, separately ask whether to `process existing` or `start
   fresh`. It never watches an active chat and never promotes.
2. **Ongoing ended-session capture** — periodically examines newly completed
   sessions and creates reviewable proposals. It is proposal-only: no canonical
   promotion and no invented score.
3. **Automatic promotion** — periodically applies the conservative `0.85` beta
   rubric to eligible pending proposals and records canonical before/after
   receipts. It can be imperfect—that is part of this beta—but uncertain items
   remain pending.

Do **not** offer or accept promotion by itself. It requires at least one enabled
capture source (backlog or ongoing capture); otherwise there is nothing safe for
it to promote. An unanswered question is not consent. Pause and ask for the
missing answer rather than guessing a mode.

Persist the answers and receive native Hermes job plans:

```text
UV_PROJECT_ENVIRONMENT=<CONCIERGE_ENV> uv run --locked --directory <CONCIERGE_PACKAGE_ROOT> --project <CONCIERGE_PACKAGE_ROOT> python scripts/concierge_setup.py save-automation-preferences --runtime-root <CONCIERGE_PACKAGE_ROOT> --data-dir <CONCIERGE_DATA> --decision-id <DECISION_ID> --backlog-cron <yes|no> --recent-capture-cron <yes|no> --promotion-cron <yes|no> --backlog-policy <process_existing|start_fresh> --favorite-media-interview <yes|no> --confirmation "I explicitly choose Concierge automation"
```

That command stores Concierge preferences and **returns plans only**. It must
not create or modify Hermes jobs itself.

Reusing a decision ID after an interrupted command is safe only when every
choice is unchanged: the helper returns the original decision as an exact
no-op. A reused ID with changed choices is a conflict; choose a fresh ID only
after explaining and reconfirming the changed choices.

Create each explicitly approved plan through the active Hermes application's
native `cronjob` tool, attaching the `concierge` skill and returned workdir. If
the tool is unavailable, use the public `HERMES_HOME=<HERMES_HOME> hermes cron
create` CLI equivalent. Never let a fallback CLI command silently target a
different/default profile.
After creation, read the job back with native Hermes tools and check its name,
schedule, prompt fingerprint, skill, and local delivery. Never import a private
Hermes scheduler module from Concierge code.

For the backlog plan, report completion after a verified terminal pass; do not
have the job modify its own scheduler record. A user-directed follow-up may
remove its exact native job after checking the receipt.

## Capture and query behavior

- For scheduled capture, use `session_search` on ended prior sessions only.
  When evidence is weak, ambiguous, or belongs to the active chat, abstain.
- Search canonical media first. Existing works get observation proposals;
  genuinely distinct identified works may get a complete `media_item` proposal.
  Do not create either canonically.
- Preserve explicit consumption facts without inventing opinions, dates,
  relationships, or numeric ratings.
- For media-shaped questions, query Concierge before relying on memory: use
  `search_media`, `get_media`, `get_taste_report`, `get_rating_history`, or
  `get_dimension_profile` as appropriate. Pending proposals are not canonical
  evidence and must be labelled pending.

## Beta limits and useful feedback

- This beta was smoke-tested on Windows. Linux is plausible but **not tested or
  supported yet**; report results rather than treating them as a compatibility
  promise.
- There is no active-session capture, no hidden provider setup, no automatic
  numeric scoring, and no claim of real-model conversational behavior.
- Report install friction, unclear consent questions, false/absent capture
  candidates, proposal-review problems, promotion surprises, data-safety
  concerns, and platform results with the commit hash and non-secret receipt.

## Uninstall

Use the artifact hash from the install receipt and target the same explicit
profile paths. This removes only the package-owned runtime and Concierge skill
files; it does not delete the user library, MCP entry, or Hermes jobs silently.
On Windows, do **not** run uninstall from `<CONCIERGE_PACKAGE_ROOT>`: `uv` can
keep a handle inside the runtime that it is about to remove. Change the
terminal's working directory back to the original `<SOURCE_CHECKOUT>` used for
installation (outside the installed runtime) before running this command.

```text
UV_PROJECT_ENVIRONMENT=<CONCIERGE_ENV> uv run --locked --directory <SOURCE_CHECKOUT> --project <SOURCE_CHECKOUT> python scripts/concierge_package.py uninstall --version 0.1.16-dev.1 --expected-artifact-hash <INSTALL_RECEIPT_HASH> --hermes-home <HERMES_HOME> --local-appdata <LOCALAPPDATA>
```
