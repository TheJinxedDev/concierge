# Concierge package preflight

**Artifact status:** Optional read-only diagnostic for the public rough beta. It
does not authorize profile mutation and is already included in Quick setup.

A package operation must begin with read-only inspection and stop before any mutation when a prerequisite, ownership check, or compatibility claim is uncertain. The repository implementation is `backend/app/package_preflight.py`, exposed through `scripts/concierge_package.py`.

## Diagnostic order

1. Capture a redacted read-only baseline: repository/ref, package version, active Hermes profile, safe MCP names, safe cron IDs/metadata, and existing Concierge data-path state.
2. Verify Hermes/platform support with the live CLI and official documentation.
3. Verify Python and `uv` without installing anything.
4. Resolve a package-owned checkout/runtime path. Keep runtime files separate from the user database.
5. Verify only the already-available Python/uv/Hermes prerequisites; do not silently install providers, models, Node/npm, or unrelated packages.
6. Confirm or choose the local data path. Preserve an explicit absolute `CONCIERGE_DATA_DIR` and never replace an existing database.
7. Initialize or migrate only through the application-owned path, with backup and failure recovery.
8. Configure the existing `taste_database` MCP key idempotently; same-name conflicts stop rather than overwrite.
9. Offer capture enablement as a separate explicit decision. Installation and MCP setup do not imply consent.
10. Create the exact package-owned cron record only after recorded enablement.
11. Run MCP discovery, a bounded health check, and readback against disposable or explicitly chosen local data.
12. Write an install report containing paths, versions, artifact identity, actions, and non-actions.
13. Start a fresh Hermes session before claiming skill/MCP visibility.

## Read-only command

From the repository root:

```bash
uv run python scripts/concierge_package.py preflight --check-commands
```

The command resolves the explicit `CONCIERGE_DATA_DIR` exactly, computes a deterministic artifact hash over manifest-declared files, probes `hermes`, `python`, and `uv` only when `--check-commands` is supplied, and emits JSON. It does not create the selected Hermes home, runtime root, data directory, database, MCP entry, cron job, enablement ledger, or capture state. If `--report` is supplied, the CLI writes only that explicitly requested report file and marks the narrow report-write side effect in the receipt.

## Hard stops

- No resolved package remote/ref or artifact hash.
- Existing MCP or cron record with an incompatible fingerprint.
- Missing or ambiguous data path.
- Migration would replace, delete, or silently coerce data.
- Required dependency is not present in the lockfile.
- Capture consent is absent, incomplete, or not exact.
- A check would read private session content or mutate the default profile without separate authorization.

The P6.3 package-file commands require explicit target paths:

```bash
uv run python scripts/concierge_package.py install --artifact-root . --hermes-home <temporary-hermes-home> --local-appdata <temporary-local-appdata>
uv run python scripts/concierge_package.py recover --hermes-home <temporary-hermes-home> --local-appdata <temporary-local-appdata>
```

Those commands own only the versioned package runtime and `skills/concierge`;
MCP, cron, consent, and SQLite remain separate gates. Normal users should use
the repository README's Quick setup rather than replaying these diagnostic steps.
