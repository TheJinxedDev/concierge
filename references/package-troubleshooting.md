# Concierge package troubleshooting

**P6 warning:** Mutating commands must use explicit temporary targets. The package CLI's `preflight` is read-only; `install`/`upgrade`/`uninstall` own only package files; Hermes MCP/cron, capture state, and the durable library remain separate gates.

## No direct install URL

This checkout currently has no configured Git remote. A raw `SKILL.md` URL cannot be resolved honestly until a repository and versioned ref exist. Do not invent a GitHub owner, repository, tag, or URL. Use the manifest's `raw_skill_url: unresolved` state.

## Skill path versus profile distribution

The intended product is a direct `concierge` skill added to an existing profile. A Hermes profile distribution is a different product that owns a new profile's SOUL, skills, MCP, and cron configuration. Do not use it as a shortcut for this package.

## Installed skill but missing support material

Check that the installed artifact came from a versioned `SKILL.md` whose referenced `references/` files were present at the same ref. Do not copy support files from a different version or silently fall back to the working checkout.

## MCP confusion

A successful MCP connection proves transport and discovery only. Preserve the `taste_database` key, verify the exact nine beta tools, and stop on a same-name/different-command conflict. Do not replace an existing entry to make setup green.

## Cron confusion

A listed, enabled, or terminally successful cron record is not proof of capture. Compare exact ownership/fingerprints, run report, cursor/action state, pending-proposal readback, and canonical before/after state. Never adopt the unrelated legacy Concierge-named records found in the default profile.

## Test confusion

A green local suite proves only the tested checkout. It does not prove package installation, clean-room isolation, fresh-agent tool use, or safe uninstall. Those are separate P6.5/P7 gates.
