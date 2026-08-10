"""Profile-scoped stdio entry point for the Concierge MCP server.

Hermes may scrub inherited environment variables when it starts a child MCP
process. The data directory is therefore an explicit argument, set before the
application module is imported, so the server cannot silently fall back to the
machine-wide compatibility directory.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Concierge MCP for one data directory")
    parser.add_argument(
        "--data-dir",
        required=True,
        help="absolute profile-scoped Concierge data directory",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    data_directory = Path(args.data_dir).expanduser()
    if not data_directory.is_absolute():
        raise SystemExit("--data-dir must be an absolute path")
    os.environ["CONCIERGE_DATA_DIR"] = str(data_directory)

    from .mcp_server import mcp

    mcp.run()


if __name__ == "__main__":
    main()
