# Concierge package compatibility matrix

**Artifact status:** rough `0.1.16-dev.3` semantic beta prerelease; exact Windows package gate verified; Linux untested.

This package-contained matrix is the public beta compatibility record.

## Portable export

| Contract | Capability |
|---|---|
| v1.0–v1.2 | Base media, rating history, archive state |
| v1.3 | Reusable creators and typed credits |
| v1.4 | Reviewable proposal queue |
| v1.5 | Bounded non-consumption categories |
| v1.6 | Factual recommendation ledger |
| v1.7 | Targetless new-record `media_item` proposals |
| v1.8 | Typed reviewable rating/progress capture proposals |

Unknown versions, fields, categories, and revisions fail closed. Empty introduced collections remain explicit.

## SQLite and MCP

Migrations 1–6 cover the current application schema; migration 5 adds typed capture proposals and migration 6 adds typed event identity. The beta MCP registry has nine tools: six factual reads, two pending-proposal reads, and one proposal-only mutation. It does not expose raw SQL, review/promotion mutation, automatic scoring, or quarantined automation-policy tools.

## Boundary

This matrix is descriptive package material, not standalone evidence. The exact Windows package, setup, cron, MCP, clean-room, and bounded fresh-agent checks are recorded separately in the publication receipts; Linux remains untested.
