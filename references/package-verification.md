# Concierge package verification

**Artifact status:** rough `0.1.16-dev.4` semantic-beta prerelease candidate. Windows package and prebuilt-UI evidence must be recorded; broader provider/Linux acceptance remains outside this packet.

## Ordinary verification

Quick setup already performs package preflight. After setup, run the receipt's
read-only verifier, then use native Hermes readback:

```text
python scripts/concierge_quickstart.py --verify-receipt <RECEIPT_PATH>
hermes mcp list
hermes mcp test taste_database
```

Require `action=concierge_installation_verified`, the expected artifact hash,
and exactly nine MCP tools. Start a new Hermes session before expecting those
tools in an already-running agent.

This proves the owned runtime, skill path, readable database, and MCP connection.
It does not prove that every model will interpret every conversation correctly,
that Linux works, or that optional cron jobs were enabled. Verify any approved
cron plans by reading those native Hermes jobs back separately.

Developer and release-gate checks belong in the repository test suite; ordinary
users do not need to replay the historical P6/P7/P8 evidence ceremony.
