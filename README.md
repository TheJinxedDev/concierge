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

There are three independent choices:

1. **Backlog capture**<br>
   Process existing completed conversations, or start fresh.

2. **Ended-session capture**<br>
   Use Hermes' native completed-session search to review a bounded set of prior sessions and create proposals only from clear evidence.

3. **Automatic promotion**<br>
   Apply the current beta `0.85` promotion rubric to eligible pending proposals.

You can use Concierge completely manually, let it collect proposals for review, or enable automatic promotion as well.

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

If you want to install the onboarding skill directly first, use the current
public-beta branch:

```text
hermes skills install https://raw.githubusercontent.com/TheJinxedDev/concierge/main/SKILL.md
```

The skill then obtains the repository checkout; it does not try to build or
search for a second Hermes installation. This beta changes quickly, so record
the commit hash in any report rather than assuming an old release tag describes
the current code.

For example:

> Set up Concierge from this repository in the currently selected profile. Read the skill first, walk me through the automation options, and verify the setup when you're done.

Concierge is designed to integrate with an existing Hermes profile rather than install or replace an entire profile environment.

## Current beta status

The current `0.1.16-dev` source-branch candidate has a local disposable
Windows smoke of package install/removal, MCP discovery, native Hermes cron
creation/readback/removal, explicit automation choices, and empty-inbox
promotion. A full fresh-agent walkthrough from this public link is the next
test—not a claim already being made here.

Current testing covers:

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