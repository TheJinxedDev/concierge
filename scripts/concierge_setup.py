#!/usr/bin/env python3
"""Initialize Concierge and emit native Hermes automation plans.

This helper owns only Concierge's selected profile-scoped database and
preferences ledger. It never imports Hermes internals or modifies MCP/cron
configuration. A caller creates returned plans through Hermes' public
``cronjob`` tool or ``hermes cron`` CLI after the user has made each explicit
choice.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.automation_cron_identity import build_automation_job_specs  # noqa: E402
from app.automation_preferences import (  # noqa: E402
    AutomationPreferences,
    AutomationPreferencesStore,
)
from app.bootstrap import DATABASE_FILENAME, open_library  # noqa: E402
from app.package_mcp import build_mcp_server_spec  # noqa: E402
from app.setup_contract import BacklogPolicy  # noqa: E402


DEFAULT_SCHEDULE = "0 4 * * 0"
EXPLICIT_AUTOMATION_CONFIRMATION = "I explicitly choose Concierge automation"


def _absolute(value: str, label: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError(f"{label} must be an absolute path")
    return path.resolve(strict=False)


def _mcp_payload(
    runtime_root: Path,
    data_directory: Path,
    environment_directory: Path,
) -> dict[str, object]:
    mcp = build_mcp_server_spec(
        runtime_root,
        data_directory=data_directory,
        environment_directory=environment_directory,
    )
    return {
        "name": mcp.name,
        "command": mcp.command,
        "args": list(mcp.args),
        "environment": list(mcp.environment),
        "fingerprint": mcp.fingerprint,
        "data_directory": str(data_directory),
    }


def initialize(args: argparse.Namespace) -> dict[str, object]:
    runtime_root = _absolute(args.runtime_root, "--runtime-root")
    data_directory = _absolute(args.data_dir, "--data-dir")
    environment_directory = _absolute(args.environment_dir, "--environment-dir")
    database_path = data_directory / DATABASE_FILENAME
    existed_before = database_path.is_file()
    library = open_library(data_directory)
    return {
        "action": "database_ready" if existed_before else "database_initialized",
        "mutated": not existed_before,
        "runtime_root": str(runtime_root),
        "data_directory": str(data_directory),
        "database_path": str(database_path),
        "database_exists": database_path.is_file(),
        "snapshot": {
            "canonical_media": len(library.list_media_items(include_archived=True)),
            "pending_proposals": len(library.list_proposals()),
        },
        "setup_policy": {
            "fully_manual_default": True,
            "active_session_observer": False,
            "generated_numeric_scores": False,
            "native_hermes_cron_required": True,
            "cron_created": False,
        },
        "mcp": _mcp_payload(runtime_root, data_directory, environment_directory),
    }


def _job_payload(spec) -> dict[str, object]:
    return {
        "kind": spec.kind.value,
        "name": spec.name,
        "owner_marker": spec.owner_marker,
        "schedule": spec.schedule,
        "prompt": spec.prompt,
        "fingerprint": spec.fingerprint,
        "skills": list(spec.skills),
        "deliver": spec.delivery_target.value,
        "workdir": spec.workdir,
    }


def save_automation_preferences(args: argparse.Namespace) -> dict[str, object]:
    runtime_root = _absolute(args.runtime_root, "--runtime-root")
    data_directory = _absolute(args.data_dir, "--data-dir")
    if args.backlog_cron == "no" and args.backlog_policy == BacklogPolicy.PROCESS_EXISTING.value:
        raise ValueError("process_existing requires backlog cron")
    if args.confirmation != EXPLICIT_AUTOMATION_CONFIRMATION:
        raise ValueError("automation requires the exact explicit confirmation")

    preferences = AutomationPreferences(
        decision_id=args.decision_id,
        decided_at=datetime.now(timezone.utc).isoformat(),
        backlog_cron_enabled=args.backlog_cron == "yes",
        recent_capture_cron_enabled=args.recent_capture_cron == "yes",
        promotion_cron_enabled=args.promotion_cron == "yes",
        backlog_policy=BacklogPolicy(args.backlog_policy),
        favorite_media_interview=args.favorite_media_interview == "yes",
    )
    stored = AutomationPreferencesStore(data_directory / "automation-preferences.json").save(
        preferences
    )
    plans = build_automation_job_specs(
        stored,
        schedule=args.schedule,
        runtime_root=runtime_root,
        data_directory=data_directory,
    )
    return {
        "action": "automation_preferences_saved",
        "mutated": True,
        "runtime_root": str(runtime_root),
        "data_directory": str(data_directory),
        "preferences_path": str(data_directory / "automation-preferences.json"),
        "preferences": stored.as_payload(),
        "native_hermes_jobs": {
            "created": False,
            "creation_required": bool(plans),
            "tool": "cronjob",
            "cli_fallback": "hermes cron create",
            "instruction": (
                "Create only these returned plans with Hermes' native scheduler, then "
                "read each job back. Do not import Hermes internals or create jobs from "
                "this Python helper."
            ),
            "plans": [_job_payload(plan) for plan in plans],
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    initialize_parser = subparsers.add_parser("initialize")
    initialize_parser.add_argument("--runtime-root", required=True)
    initialize_parser.add_argument("--data-dir", required=True)
    initialize_parser.add_argument("--environment-dir", required=True)
    initialize_parser.set_defaults(handler=initialize)

    automation_parser = subparsers.add_parser("save-automation-preferences")
    automation_parser.add_argument("--runtime-root", required=True)
    automation_parser.add_argument("--data-dir", required=True)
    automation_parser.add_argument("--decision-id", required=True)
    automation_parser.add_argument("--backlog-cron", required=True, choices=["yes", "no"])
    automation_parser.add_argument(
        "--recent-capture-cron", required=True, choices=["yes", "no"]
    )
    automation_parser.add_argument("--promotion-cron", required=True, choices=["yes", "no"])
    automation_parser.add_argument(
        "--backlog-policy",
        required=True,
        choices=[policy.value for policy in BacklogPolicy],
    )
    automation_parser.add_argument(
        "--favorite-media-interview", required=True, choices=["yes", "no"]
    )
    automation_parser.add_argument("--schedule", default=DEFAULT_SCHEDULE)
    automation_parser.add_argument("--confirmation", required=True)
    automation_parser.set_defaults(handler=save_automation_preferences)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        payload = args.handler(args)
    except (OSError, ValueError) as error:
        print(json.dumps({"action": "failed", "mutated": False, "reason": str(error)}))
        return 2
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
