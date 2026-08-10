# Concierge cron package boundary

The private development package defines three exact, package-owned automation
jobs. Creating them remains opt-in and profile-scoped; installation alone never
creates them. The backlog, recent completed-session capture, and automatic
promotion lanes are separate identities and separate onboarding choices.

| Field | Exact value |
|---|---|
| Job name | `concierge-backlog-capture`, `concierge-session-capture`, `concierge-auto-promotion` |
| Owner marker | `concierge/automation/backlog`, `concierge/automation/recent_capture`, `concierge/automation/promotion` |
| Skill tuple | `("concierge",)` |
| Schedule | Sunday 04:00, host-local, no catch-up |
| Delivery | `local` for the CLI beta test |
| Capture mode | proposal-only package runners; no active-session observer |
| Backlog policy | `process_existing` or `start_fresh` |

Ownership is established by the complete fingerprinted prompt, stable name,
schedule, delivery, and skill identity—not by a familiar name alone. Same-name
legacy records and fingerprint drift are conflicts. Uninstall/update removes or
changes only an exact owned record.

## Creation gate

Onboarding asks and persists three independent yes/no choices: finite backlog,
recent completed-session capture, and automatic promotion. It also persists the
backlog policy (`process_existing` or `start_fresh`) and the optional favorite-
media interview choice. The exact confirmation is:

```text
I explicitly choose Concierge automation
```

The setup helper reconciles the profile-scoped Hermes cron store and reads back
every created record's owner metadata, fingerprint, schedule, delivery, skill,
and enabled state. An exact repeat is a no-op; a same-name collision or drifted
fingerprint stops without overwrite. The prompts carry the absolute runtime,
Hermes home, data directory, and the exact package-owned runner command.

A scheduler status such as `last_status=ok` is never sufficient evidence. Each
runner must read back its own proposal/report/state and canonical before/after
snapshot. The finite backlog runner independently accepts backlog consent,
processes ended sessions only, and removes only its exact owned record after a
verified `complete` or `no_visible_evidence` terminal pass with no remaining
backlog, retryable/blocked claims, errors, canonical mutation, or uncertain
readback. Partial, blocked, failed, unknown-commit, lock, source, and uncertain
readback results retain the job for retry. The ongoing recent runner advances a
durable watermark and never promotes. The promotion runner is separate and
applies the documented `0.85` beta threshold, retaining abstentions.
