# Changelog

## 0.1.16-dev.1 — native-Hermes setup correction (pre-release)

- Supersedes the `0.1.16-dev` prerelease, which retained an obsolete setup path.
- Uses native Hermes MCP/cron surfaces only; it does not import or search for
  Hermes internals, a Hermes source checkout, or a second Hermes environment.
- Clears inherited Python runtime paths for MCP children and documents the
  required clean-terminal step before `uv run` commands.
- Makes unchanged automation-decision retries exact no-ops despite a fresh
  timestamp; changed choices under the same decision ID still fail closed.
- Keeps all rough-beta boundaries: ended-session proposal capture, three
  independent explicit choices, the `0.85` promotion rubric, no active-session
  observer, no generated numeric score, and canonical before/after receipts.

## 0.1.16-dev — rough semantic beta (pre-release)

- Retains ended-session observation capture, three independent explicit cron choices, and the separate package-owned automatic-promotion cron using the existing `0.85` beta rubric.
- Keeps semantic Concierge query/read behavior and proposal/observation boundaries with canonical before/after receipts; capture does not generate numeric taste scores and does not observe active sessions.
- Reconciles the direct-skill package identity and documentation as a rough beta rather than presenting historical P6.5 smoke evidence as the beta verdict.
- Claims only Windows local verification; Linux should work in theory but is not currently tested. It does not claim native-Linux, Ubuntu WSL2, provider-backed, active-session, or ordinary private-use acceptance.
- Publishes the versioned raw skill URL and release archive for public rough-beta testing; verify the archive checksum before installation and treat the release as a work in progress, not a production-ready package.

### Historical P6.5 evidence

The earlier `0.1.0` P6.5 entry was package-plumbing smoke evidence, not this beta's automation or semantic-read acceptance. It remains historical context and is not used as current proof.

This entry does not claim that Concierge is production-ready. It is a public rough beta for independent testing: Windows is the tested path, Linux is expected to work in theory but is not currently tested, and reports that break or clarify the package are welcome.
