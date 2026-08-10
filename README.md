# Concierge — rough semantic beta `0.1.16-dev`

Concierge is a local-first media memory system for Hermes agents. It keeps canonical media records, provenance-preserving observations, progress and rating history, and semantic read projections in a user-owned SQLite library.

> **Private prerelease.** This is a rough beta for authenticated collaborators to test, not a public release or a claim of production readiness.

## Try it with a fresh-ish Hermes agent

This repository is private. The versioned skill entry point is:

```text
https://raw.githubusercontent.com/TheJinxedDev/concierge/v0.1.16-dev/SKILL.md
```

Give that link to an authenticated Hermes agent and say:

> Set up Concierge from this link in the current profile. Before making any profile, database, MCP, or cron changes, explain the plan and ask each of the three automation choices separately.

The direct CLI form is:

```bash
hermes skills install https://raw.githubusercontent.com/TheJinxedDev/concierge/v0.1.16-dev/SKILL.md
```

The URL requires GitHub access because the repository is private. The onboarding instructions in [`SKILL.md`](SKILL.md) are the authority for profile-scoped setup and consent. Setup must not silently mutate another profile, import real data, enable capture, or create a scheduler.

## What this beta includes

- Ended-session observation capture only; there is no active-session observer.
- Three independent explicit cron choices: finite backlog capture, recent ended-session capture, and automatic promotion.
- Separate automatic-promotion execution using the existing `0.85` beta rubric.
- Proposal-first capture with reviewable pending observations and canonical before/after receipts.
- Semantic Concierge title, dimension, history, and cited-evidence reads.
- No generated numeric taste scores and no opaque score writes.
- Explicit abstention for low-confidence, metadata, and inferred-rating candidates.

A `false` consent is recorded as deliberately as `true`. Capture never becomes a canonical write merely because a session contained a plausible statement. Automatic promotion is the separately enabled, deliberately small rough-beta exception; it may abstain and leave candidates pending.

## Install and package boundary

The root skill is a direct Hermes skill package. [`manifest.yaml`](manifest.yaml) records the exact package files, version, artifact status, side-effect declarations, and authenticated versioned raw URL.

The package lifecycle is explicit and target-scoped:

```bash
uv run python scripts/concierge_package.py preflight --check-commands
uv run python scripts/concierge_package.py install --artifact-root . --hermes-home <temporary-hermes-home> --local-appdata <temporary-local-appdata>
uv run python scripts/concierge_package.py uninstall --version 0.1.16-dev --expected-artifact-hash <read-from-install-receipt> --hermes-home <temporary-hermes-home> --local-appdata <temporary-local-appdata>
```

The first command is read-only. Installation and uninstall own only their explicitly supplied package/runtime targets. Database initialization, MCP registration, and each cron consent remain separate, visible setup steps. Do not use a default profile or real library for a first-run test.

## Verification scope

The beta was verified with Windows backend/frontend/build/package checks and exact disposable smoke, plus Ubuntu WSL2 smoke on the designated second PC. The Linux claim is **Ubuntu WSL2 smoke only**—not native-Linux live testing, provider-backed fresh-agent behavior, active-session observation, or ordinary private-use acceptance.

Historical P6.5 smoke evidence is not this beta verdict. The current beta retains the requested automation and semantic-read behavior without broadening the work into P8.5 hardening.

Read the bounded contracts before setup:

- [`references/package-contracts.md`](references/package-contracts.md)
- [`references/package-preflight.md`](references/package-preflight.md)
- [`references/package-mcp.md`](references/package-mcp.md)
- [`references/package-cron.md`](references/package-cron.md)
- [`references/scoring-disabled-policy.md`](references/scoring-disabled-policy.md)
- [`references/known-limitations.md`](references/known-limitations.md)

## Package identity

- Name: `concierge`
- Version/ref: `0.1.16-dev` / `v0.1.16-dev`
- Artifact status: `rough-semantic-beta`
- Repository visibility: private
- License: Apache-2.0 (see [`LICENSE`](LICENSE))

Local databases, backups, exports, credentials, virtual environments, and Hermes profile state are deliberately outside this repository.
