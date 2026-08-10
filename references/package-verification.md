# Concierge package verification

**Artifact status:** P7.7 local clean-room evidence packet is complete and reproducible; the P8 release gate remains distinct and blocked by the unresolved public URL and real-provider behavioral acceptance.

## P6.1 checks

From the repository root:

```bash
uv run pytest -q backend/tests/test_p61_package_layout.py
uv run pytest -q

git diff --check
```

The package-layout test checks the root skill name/version, manifest identity, required support files, fixture markers, export/migration compatibility claims, and the explicit no-installable-claim state.

## Later evidence levels

| Level | What it proves | What it does not prove |
|---|---|---|
| P6.1 local artifact | Required files exist and agree on package identity | Installation, MCP mutation, cron creation, capture, clean-room behavior |
| P6.2 setup path | Read-only preflight and bounded idempotent setup behavior | Fresh-agent natural-language behavior or a published release |
| P6.3 recovery | Upgrade, uninstall, conflict, interruption, and package-file recovery boundaries | Scheduler execution against real sessions |
| P6.4 release docs | Documentation, manifest, package CLI, report schema, and known-limitations completeness | Runtime success merely because docs exist |
| P6.5 local smoke | Exact artifact in temporary profile/data paths, MCP discovery, report, uninstall boundary, default-profile hash stability | Public release or live capture authorization |
| P7.1–P7.5 | Disposable clean-room installation, MCP/cron/readback, and mock fresh-agent plumbing | Real-provider natural-language behavior |
| P7.6 | Eight synthetic negative/unavailable-path cases with cursor/claim/lock/scoring dispositions | Provider or live-session behavior |
| P7.7 | Reproducible local artifact/hash, environment/commands, MCP/cron/database, mock transcript, counts, and limitations packet | Public URL, real-provider acceptance, independent review, release decision |
| P8 | Final local verification and release-gate reconciliation | Anything outside the recorded artifact/version/ref |

The P6.5 install report schema is `backend/app/package_report.py`; it records artifact identity/hash, paths, checks, version commands, actions, non-actions, MCP/cron/database mutation flags, caveats, and the fresh-session requirement. Never turn a successful `hermes skills install`, MCP connection, or cron terminal status into a claim about canonical safety without independent readback.
