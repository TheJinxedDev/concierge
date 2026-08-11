# Concierge package troubleshooting

**P6 warning:** Mutating commands must use explicit temporary targets. The package CLI's `preflight` is read-only; `install`/`upgrade`/`uninstall` own only package files; Hermes MCP/cron, capture state, and the durable library remain separate gates.

## Public direct install URL

The current public versioned raw skill URL is:

`https://raw.githubusercontent.com/TheJinxedDev/concierge/v0.1.16-dev.2/SKILL.md`

The public rough-beta release archive and checksum sidecar are linked from the
`v0.1.16-dev.2` release. Do not substitute an older tag, a cached branch URL, or
a different owner/repository/tag. The ordinary quickstart keeps release-grade
checks inside the implementation instead of asking each user to replay them.

The older `v0.1.16-dev` prerelease is explicitly superseded and must not be
used for fresh setup.

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

## Windows uninstall from an installed runtime

Change the terminal working directory to the original source checkout before
running `scripts/concierge_package.py uninstall`; do not run it from the
installed `runtime_path/artifact` directory. On Windows, `uv` can keep a handle
open inside the runtime it launched, so Concierge fails closed with a clear
instruction instead of risking a partial delete. The command still needs the
original explicit profile paths and install-receipt artifact hash.
