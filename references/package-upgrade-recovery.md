# Concierge upgrade, uninstall, and recovery boundary

**Status:** P6.3 implemented-local package-file lifecycle; MCP/cron and clean-room smoke evidence remain separate gates.

## Package-owned upgrade

- Match the installed package name, version, exact skill path, support-file set, and owned MCP/cron fingerprints. The local file lifecycle is implemented in `backend/app/package_lifecycle.py` and exposed through `scripts/concierge_package.py`.
- Preserve the user database and pending/canonical history.
- Do not overwrite a same-name MCP or cron record with a different fingerprint.
- Use a backup/restore path before migrations and verify the result after each migration.
- An interrupted setup must leave a readable report and a safe rerun path. `concierge_package.py recover` removes only `.concierge-install-*` staging directories.

## Uninstall boundary

Uninstall may remove only package-owned skill files and exact package-owned MCP/cron entries established by the installed manifest. It must not delete the database, backups, unrelated skills, unrelated MCP entries, legacy jobs, Hindsight state, Obsidian notes, or user-authored files.

## Recovery classes

| Situation | Required behavior |
|---|---|
| Exact rerun | No-op or continue from durable checkpoint |
| Same-ID/different-payload | Conflict; preserve prior record |
| Migration failure | Roll back/restore without replacement; report exact stage |
| MCP conflict | Stop; show redacted identity/fingerprint difference |
| Cron conflict | Stop; never adopt or overwrite |
| Unknown write result | Verify before retry; do not duplicate |
| Missing capture consent | Remain `off`; create no job |

The P6.3 tests prove exact install/no-op, modified-file conflict, versioned upgrade with old-runtime preservation, drifted-record uninstall refusal, explicit target-path CLI behavior, and owned staging recovery. They do not prove MCP configuration, cron execution, fresh-agent behavior, or a public raw URL.
