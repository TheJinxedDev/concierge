# Concierge fixture catalog

The public beta ships only small, synthetic package fixtures:

- `backend/tests/fixtures/concierge_e2e/seed_export.json` — disposable canonical
  seed data for import/export and MCP checks.
- `backend/tests/fixtures/concierge_e2e/source_catalog.json` — synthetic source
  descriptions used to reason about proposal boundaries.

No real Hermes history, profiles, backups, session databases, or provider
outputs belong in the package. Native Hermes behavior is verified through a
selected disposable profile and its public tools, not by importing Hermes
implementation modules into this package.
