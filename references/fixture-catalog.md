# Concierge synthetic fixture catalog

The package's runtime evidence must use disposable fixtures, never real Hermes session history or the default library.

## Canonical fixture sources

- Seed: `backend/tests/fixtures/concierge_e2e/seed_export.json`
- Source catalog: `backend/tests/fixtures/concierge_e2e/source_catalog.json`
- Fixture tests: `backend/tests/test_concierge_fixtures.py`, `backend/tests/test_synthetic_tracer.py`, and the later capture-state/matrix suites.

Every source case is explicitly marked `source_class: synthetic_fixture` and uses a `synthetic://concierge-e2e/...` reference. The fixture root is disposable and must be cleaned up after a run.

## Current cases

| Case family | Expected proof |
|---|---|
| Known canonical title | Exact stable canonical target |
| Unique alias | Canonical target plus preserved source wording |
| New unambiguous title | Targetless pending `media_item` candidate |
| Ambiguous identity | No fabricated target; explicit ambiguity |
| Repeated/duplicate source | Stable action identity and no duplicate proposal |
| Conflict/correction | Contradiction/history preserved; no overwrite |
| Malformed/incomplete source | Contract failure or safe decline |
| Unreadable/changed source | Retry/blocked disposition and held cursor |
| Interrupted/stale/unknown result | Recoverable claim/lock; no unverified success |
| Disabled synthetic scoring | Semantic-only behavior; no score write |

The existing fixture evidence proves the synthetic boundaries described in the project status. P6.5 additionally runs the exact local artifact, temporary skill/MCP profile, synthetic database, MCP discovery/readback, and uninstall cleanup. It still does not prove live session enumeration, fresh-agent behavior, or public release.
