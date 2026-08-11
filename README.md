# Concierge

**A local-first media and taste archive for Hermes.**

Concierge gives your Hermes agent somewhere durable to keep the media history that would otherwise disappear into old conversations: what you watched, played, read, liked, disliked, dropped, reconsidered, and why.

Instead of reducing your taste to a handful of mystery scores or opaque memories, Concierge keeps the underlying evidence in a local, inspectable library. Your agent can query that history later for recommendations, reflections, progress checks, and conversations that benefit from actually remembering what you thought.

> **Public beta:** Concierge is usable, actively tested, and still evolving. If you try it, bug reports and weird edge cases are very welcome.

## What it does

Concierge stores:

- media and creators;
- ratings and progress;
- observations and reactions;
- relationships between entries;
- recommendation evidence;
- provenance, source context, confidence, and stable IDs.

Your Hermes agent can then query that library through the Concierge app and MCP interface.

That means questions like:

- “What romance anime have I actually liked?”
- “Did I ever finish this?”
- “Why did I dislike that game?”
- “What have I said about this creator before?”
- “Recommend something based on the parts of those shows I liked, not just their genre.”

…can be answered from an inspectable history instead of vibes and conversational residue.

## Local-first and reviewable

The Concierge library lives locally in SQLite.

Conversation-derived observations do not have to become permanent records immediately. Concierge separates observations and proposals from canonical data, so you can review what the agent extracted before accepting it.

For people who want less manual upkeep, optional automation can process completed Hermes sessions and promote sufficiently clear candidates automatically.

Ambiguous or low-confidence candidates stay pending for review.

The goal is simple: **make useful context easy to keep without making it mysterious.**

## Automation, your way

Automation is optional and configured during setup. Installing Concierge does not silently enable background capture.

There are three independent decisions. Onboarding asks them one at a time so a
single-choice chat UI cannot accidentally turn them into mutually exclusive
options:

1. **Backlog capture**<br>
   Process existing completed conversations, or start fresh.

2. **Ended-session capture**<br>
   Use Hermes' native completed-session search to review a bounded set of prior sessions and create proposals only from clear evidence.

3. **Automatic promotion**<br>
   Apply the current beta `0.85` promotion rubric to eligible pending proposals.

Automatic promotion is offered only after at least one capture source is
enabled; otherwise there would be nothing for it to promote. You can use
Concierge completely manually, let it collect proposals for review, or enable
automatic promotion as well.

Even with everything enabled, Concierge processes completed sessions rather than watching the active conversation, and uncertain candidates remain pending.

## Typical workflow

1. Install Concierge into a Hermes profile.
2. Let the agent initialize its profile-scoped library and MCP connection.
3. Choose which, if any, automation features you want.
4. Talk about media normally, or edit entries directly through the local UI.
5. Review pending observations and proposals whenever you want.
6. Query Concierge when your agent needs actual historical evidence or context.

Concierge records canonical before/after snapshots around promotion, so automated changes remain inspectable.

## Installation

Give your Hermes agent this repository link and ask it to set Concierge up in
the currently selected profile. The agent should read [`SKILL.md`](SKILL.md),
clone or download this checkout as the beta runtime, and follow the explicit
profile/MCP/cron consent flow.

### Quick setup

After checking out the immutable beta tag, the agent resolves three absolute,
profile-scoped paths and runs one command from the repository root:

```text
python scripts/concierge_quickstart.py --hermes-home <HERMES_HOME> --local-appdata <LOCALAPPDATA> --environment-dir <CONCIERGE_ENV>
```

That one helper performs read-only preflight, creates an isolated locked Python
environment, installs the exact owned runtime, and initializes the selected
profile's empty local library. It deliberately leaves MCP confirmation and all
three automation choices to native Hermes so those remain visible and explicit.
Its console receipt stays short; exact inventories and cron plans are saved at
the reported profile-scoped `receipt_path`.

For an advanced/manual skill-only inspection, use the immutable public-beta tag:

```text
hermes skills install https://raw.githubusercontent.com/TheJinxedDev/concierge/v0.1.16-dev.4/SKILL.md
```

The normal path is still to give the agent this repository link. The skill-only
command is not a substitute for the tagged runtime checkout.

After quickstart succeeds, Concierge returns a receipt. MCP registration uses a
PTY-capable terminal because Hermes asks the user to approve the nine exposed
tools. The agent must then verify the exact record with `hermes mcp list` and
require `hermes mcp test taste_database` to report nine tools; the add command's
exit code alone is not proof. Start a new Hermes session before expecting those
tools to appear in an already-running agent.

The same receipt includes `ui.launch_command`, `ui.readiness_url`, and `ui.url`.
The agent starts that command in the background, waits for readiness, and points
you to the local browser UI. The production UI is prebuilt into the package and
does not require Node or npm on the installing machine. It serves the interface
and API together on loopback, normally at `http://127.0.0.1:4173/`.

Automation is then asked as three sequential yes/no questions: backlog capture,
ongoing ended-session capture, and—only if either capture source is enabled—
automatic promotion. The second pass reuses the receipt rather than asking for
all profile paths again. Backlog policy is asked only when backlog capture is
enabled.

```text
python scripts/concierge_quickstart.py --receipt <RECEIPT_PATH> --backlog-cron <yes|no> --recent-capture-cron <yes|no> --promotion-cron <yes|no> [--backlog-policy <process_existing|start_fresh>]
```

The exact installation can be checked without writing to it:

```text
python scripts/concierge_quickstart.py --verify-receipt <RECEIPT_PATH>
```

For example:

> Set up Concierge from this repository in the currently selected profile. Read the skill first, walk me through the automation options, and verify the setup when you're done.

Concierge is designed to integrate with an existing Hermes profile rather than install or replace an entire profile environment.

To uninstall package-owned files later, run this from the original checkout,
not from inside the installed runtime:

```text
python scripts/concierge_package.py uninstall --version 0.1.16-dev.4 --expected-artifact-hash <HASH> --hermes-home <HERMES_HOME> --local-appdata <LOCALAPPDATA>
```

The user library, MCP entry, and Hermes jobs are separate and are never silently
deleted by this command.

## Current beta status

The current `0.1.16-dev.4` beta uses native Hermes surfaces, one profile-scoped
quickstart, no upper Hermes version pin, and a prebuilt local browser UI. It adds
receipt-driven verification and setup reuse, sequential automation consent,
strict native MCP readback, and an exact UI launch handoff. Earlier prereleases
remain historical evidence but are superseded for installation. The privacy,
proposal, scoring, and explicit automation boundaries remain unchanged.

Current testing covers:

- exact-package UI startup, built asset delivery, API routing, and manual
  proposal acceptance/promotion through the browser;
- package installation and uninstall;
- explicit cron-plan generation and rejection of promotion with no capture source;
- native Hermes cron creation/readback/removal in a disposable profile;
- automatic promotion using the `0.85` beta rubric;
- protection against generated numeric taste scores and active-session observation;
- package cleanup and profile isolation.

### Platform support

**Windows is currently the tested path.**

Linux may work, but it has not been tested for this candidate. Reports from
Linux users would be especially useful; it is not a compatibility promise yet.

Active-session observation is not currently part of Concierge. The existing automation works from completed sessions.

## A few intentional boundaries

Concierge deliberately avoids several behaviors that would make a personal taste archive harder to trust:

- proposals are not treated as canonical changes by default;
- it does not invent numeric ratings from casual conversation;
- provenance and source context are preserved;
- ambiguous automated candidates can abstain and remain pending;
- Hermes memory and Obsidian are not used as duplicate media databases;
- active conversations are not passively monitored.

These are design choices rather than temporary limitations, although the exact workflows around them may continue to evolve during the beta.

## Help test Concierge

This is the fun part.

Use it normally. Throw strange libraries at it. Ask awkward semantic questions. Upgrade it. Restart things. Uninstall it. Give it conversations that should produce obvious observations and conversations that absolutely should not.

Useful issue reports include things like:

- confusing installation or setup;
- missed observations;
- proposals that should have remained pending;
- incorrect source or media attribution;
- surprising automatic-promotion decisions;
- semantic queries that return the wrong evidence;
- restart, upgrade, uninstall, or profile-isolation problems;
- unexpected canonical changes.

When opening an issue, it helps to include:

- your OS;
- Hermes version;
- Concierge version or git ref;
- what you were doing when the problem appeared;
- a redacted receipt or relevant output, if available.

Please avoid posting private conversations, database contents, credentials, or personal media history in public issues.

“This broke in a fascinating way” is an extremely useful beta report.

## Project status

Concierge is still a work in progress.

The current beta is focused on getting the storage model, capture boundaries, querying behavior, automation, and Hermes integration exercised by people outside the development environment before those interfaces settle down.


## License

See [`LICENSE`](LICENSE).