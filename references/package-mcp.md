# Concierge MCP package boundary

The private development package owns a profile-scoped stdio MCP launch shape. It
is still explicit and reversible: package installation does not alter Hermes
configuration, while the onboarding skill performs the user-directed add/test
step and reads the result back.

The compatibility MCP key is `taste_database`. The semantic-beta registry has
exactly nine tools:

- six factual reads: `search_media`, `get_media`, `get_taste_report`,
  `get_dimension_profile`, `get_rating_history`, `list_evidence_dimensions`;
- two pending-proposal reads: `list_pending_proposals`, `get_proposal`;
- one proposal-only write: `submit_pending_proposal`.

The server owns the application boundary. It must not expose raw SQLite,
automatic scoring, accept/reject/promote, import/restore, archive/delete, or
automation-policy mutation.

## Profile-scoped launch

The setup helper resolves an explicit runtime and data directory. The command
uses `app.mcp_entry`, not `app.mcp_server`, because Hermes may scrub inherited
environment variables from an MCP child:

```text
uv run --directory <runtime> --project <runtime> python -m app.mcp_entry --data-dir <profile-scoped-data>
```

The corresponding Hermes registration is:

```text
hermes mcp add taste_database --command uv --env UV_PROJECT_ENVIRONMENT=<profile-scoped-env> PYTHONDONTWRITEBYTECODE=1 --args run --locked --directory <runtime>/backend --project <runtime> python -m app.mcp_entry --data-dir <profile-scoped-data>
hermes mcp test taste_database
```

`<profile-scoped-env>` must be outside both the immutable artifact root and the
versioned install directory. The default is a hidden sibling environment under
the package parent (for example, `.../packages/.0.1.16-dev.venv`). The
`UV_PROJECT_ENVIRONMENT` override prevents the first `uv run` or Hermes MCP
test from creating `.venv` inside the installed package tree, so package
uninstall can quarantine the versioned runtime without holding the active
environment directory open. `--args` is the final Hermes option. A same-name
exact command/args/environment record is a no-op after successful testing; a
same-name drift is a conflict and must not be automatically adopted or
overwritten.

## Verification

Discovery alone is insufficient. Verify the nine-tool registry, perform one
isolated canonical search, submit one `needs_review` proposal, read it back
through `list_pending_proposals`/`get_proposal`, and assert canonical IDs are
unchanged. Do not point the command at the user's default library merely to
prove the protocol.
