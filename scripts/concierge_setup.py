#!/usr/bin/env python3
"""Initialize and describe one explicit profile-scoped Concierge setup.

This command owns only the application setup ledger and database bootstrap. It
never guesses a profile, opens a default data directory, or silently enables a
scheduler. Hermes MCP/cron configuration remains an explicit follow-up from the
onboarding skill after the user chooses the bounded semi_auto lane.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(ROOT / "backend"))

from app.bootstrap import DATABASE_FILENAME, open_library  # noqa: E402
from app.automation_cron_identity import (  # noqa: E402
    HermesAutomationCronStore,
    build_automation_job_specs,
    reconcile_automation_jobs,
)
from app.automation_preferences import (  # noqa: E402
    AutomationPreferences,
    AutomationPreferencesStore,
)
from app.capture_boundary import CaptureMode  # noqa: E402
from app.capture_enablement import (  # noqa: E402
    EXPLICIT_ENABLE_CONFIRMATION,
    CaptureEnablementStore,
    EnablementRequest,
)
from app.cron_identity import (  # noqa: E402
    CaptureSchedule,
    DEFAULT_CAPTURE_PROMPT_BODY,
    build_package_owned_job_spec,
)
from app.package_mcp import build_mcp_server_spec  # noqa: E402
from app.setup_contract import BacklogPolicy, DeliveryTarget  # noqa: E402


def _manifest_version() -> str:
    manifest = ROOT / "manifest.yaml"
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if line.startswith("version:"):
            version = line.partition(":")[2].strip()
            if version:
                return version
    raise ValueError(f"package manifest has no version: {manifest}")


DEFAULT_SCHEDULE = "0 4 * * 0"
DEFAULT_PACKAGE_MARKER = f"concierge@{_manifest_version()}"
EXPLICIT_AUTOMATION_CONFIRMATION = "I explicitly choose Concierge automation"


def _absolute(value: str, label: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError(f"{label} must be an absolute path")
    return path.resolve(strict=False)


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _spec_payload(
    runtime_root: Path,
    data_directory: Path,
    package_marker: str,
    *,
    capture_mode: CaptureMode = CaptureMode.PENDING_ONLY,
    backlog_policy: BacklogPolicy = BacklogPolicy.START_FRESH,
) -> dict[str, object]:
    mcp = build_mcp_server_spec(runtime_root, data_directory=data_directory)
    schedule = CaptureSchedule(expression=DEFAULT_SCHEDULE)
    cron = build_package_owned_job_spec(
        schedule=schedule,
        delivery_target=DeliveryTarget.LOCAL,
        capture_mode=capture_mode,
        backlog_policy=backlog_policy,
        package_marker=package_marker,
    )
    return {
        "mcp": {
            "name": mcp.name,
            "command": mcp.command,
            "args": list(mcp.args),
            "environment": list(mcp.environment),
            "fingerprint": mcp.fingerprint,
            "data_directory": str(data_directory),
        },
        "cron": {
            "name": cron.name,
            "schedule": cron.schedule.expression,
            "deliver": cron.delivery_target.value,
            "skills": list(cron.skills),
            "prompt": cron.prompt,
            "owner_marker": cron.owner_marker,
            "package_marker": cron.package_marker,
            "capture_mode": cron.capture_mode.value,
            "backlog_policy": cron.backlog_policy.value,
            "fingerprint": cron.fingerprint,
        },
    }


def initialize(args: argparse.Namespace) -> dict[str, object]:
    runtime_root = _absolute(args.runtime_root, "--runtime-root")
    data_directory = _absolute(args.data_dir, "--data-dir")
    database_path = data_directory / DATABASE_FILENAME
    existed_before = database_path.is_file()
    library = open_library(data_directory)
    snapshot = {
        "canonical_media": len(library.list_media_items(include_archived=True)),
        "pending_proposals": len(library.list_proposals()),
    }
    return {
        "action": "database_ready" if existed_before else "database_initialized",
        "mutated": not existed_before,
        "runtime_root": str(runtime_root),
        "data_directory": str(data_directory),
        "database_path": str(database_path),
        "database_exists": database_path.is_file(),
        "snapshot": snapshot,
        "setup_policy": {
            "fully_manual_default": True,
            "semi_auto_mode": CaptureMode.PENDING_ONLY.value,
            "fully_auto_enabled": False,
            "backlog_policy_choice_required": True,
            "backlog_policy_default": None,
            "capture_consent_required": True,
            "backlog_cron_created": False,
        },
        "next": _spec_payload(runtime_root, data_directory, args.package_marker),
    }


def save_automation_preferences(args: argparse.Namespace) -> dict[str, object]:
    runtime_root = _absolute(args.runtime_root, "--runtime-root")
    data_directory = _absolute(args.data_dir, "--data-dir")
    hermes_home = _absolute(args.hermes_home, "--hermes-home")
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
    store = AutomationPreferencesStore(data_directory / "automation-preferences.json")
    stored = store.save(preferences)
    specs = build_automation_job_specs(
        stored,
        schedule=args.schedule,
        runtime_root=runtime_root,
        data_directory=data_directory,
        hermes_home=hermes_home,
    )
    scheduler_results = reconcile_automation_jobs(
        HermesAutomationCronStore(hermes_home),
        stored,
        schedule=args.schedule,
        runtime_root=runtime_root,
        data_directory=data_directory,
        hermes_home=hermes_home,
    )
    return {
        "action": "automation_preferences_saved",
        "mutated": True,
        "runtime_root": str(runtime_root),
        "data_directory": str(data_directory),
        "preferences_path": str(data_directory / "automation-preferences.json"),
        "hermes_home": str(hermes_home),
        "preferences": stored.as_payload(),
        "scheduler": {
            "ready": all(result.action.value in {"noop", "created", "removed"} for result in scheduler_results),
            "results": [
                {
                    "action": result.action.value,
                    "reason": result.reason,
                    "job": result.job,
                    "mutated": result.mutated,
                }
                for result in scheduler_results
            ],
        },
        "jobs": [
            {
                "kind": spec.kind.value,
                "name": spec.name,
                "owner_marker": spec.owner_marker,
                "schedule": spec.schedule,
                "prompt": spec.prompt,
                "fingerprint": spec.fingerprint,
            }
            for spec in specs
        ],
    }


def enable_capture(args: argparse.Namespace) -> dict[str, object]:
    data_directory = _absolute(args.data_dir, "--data-dir")
    schedule = CaptureSchedule(expression=args.schedule)
    mode = CaptureMode(args.mode)
    backlog_policy = BacklogPolicy(args.backlog_policy)
    if mode is CaptureMode.FULL_AUTO:
        raise ValueError(
            "the legacy enable-capture command cannot select fully_auto; use "
            "save-automation-preferences with separate backlog, recent-capture, "
            "and promotion confirmations"
        )
    store = CaptureEnablementStore(data_directory / "capture-enablement.json")
    request = EnablementRequest(
        decision_id=args.decision_id,
        mode=mode,
        delivery_target=DeliveryTarget.LOCAL,
        origin=None,
        schedule=schedule,
        decided_at=datetime.now(timezone.utc),
        confirmation=args.confirmation,
        backlog_policy=backlog_policy,
    )
    result = store.enable(request)
    current = result.state.current_decision
    assert current is not None
    return {
        "action": "enabled" if result.recorded else "noop",
        "mutated": result.recorded,
        "mode": current.mode.value,
        "backlog_policy": current.backlog_policy.value,
        "capture_enabled": result.state.is_enabled,
        "backlog_enabled": current.backlog_policy is BacklogPolicy.PROCESS_EXISTING,
        "data_directory": str(data_directory),
        "ledger_path": str(store.path),
        "schedule": schedule.expression,
        "confirmation_exact": args.confirmation == EXPLICIT_ENABLE_CONFIRMATION,
        "state": _state_payload(result.state),
    }


def enable_semi_auto(args: argparse.Namespace) -> dict[str, object]:
    args.mode = CaptureMode.PENDING_ONLY.value
    return enable_capture(args)


def _state_payload(state) -> dict[str, object]:
    return {
        "schema_version": state.schema_version,
        "current_decision_id": state.current_decision_id,
        "decisions": [
            {
                "decision_id": decision.decision_id,
                "action": decision.action.value,
                "mode": decision.mode.value,
                "backlog_policy": decision.backlog_policy.value,
                "delivery_target": decision.delivery_target.value,
                "schedule": decision.schedule.expression,
                "confirmation": decision.confirmation,
            }
            for decision in state.decisions
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package-marker", default=DEFAULT_PACKAGE_MARKER)
    subparsers = parser.add_subparsers(dest="command", required=True)

    initialize_parser = subparsers.add_parser("initialize")
    initialize_parser.add_argument("--runtime-root", required=True)
    initialize_parser.add_argument("--data-dir", required=True)
    initialize_parser.set_defaults(handler=initialize)

    capture_parser = subparsers.add_parser("enable-capture")
    capture_parser.add_argument("--data-dir", required=True)
    capture_parser.add_argument("--decision-id", required=True)
    capture_parser.add_argument(
        "--mode",
        required=True,
        choices=[mode.value for mode in CaptureMode],
    )
    capture_parser.add_argument(
        "--backlog-policy",
        required=True,
        choices=[policy.value for policy in BacklogPolicy],
    )
    capture_parser.add_argument("--schedule", default=DEFAULT_SCHEDULE)
    capture_parser.add_argument("--confirmation", required=True)
    capture_parser.set_defaults(handler=enable_capture)

    enable_parser = subparsers.add_parser("enable-semi-auto")
    enable_parser.add_argument("--data-dir", required=True)
    enable_parser.add_argument("--decision-id", required=True)
    enable_parser.add_argument(
        "--backlog-policy",
        default=BacklogPolicy.START_FRESH.value,
        choices=[policy.value for policy in BacklogPolicy],
    )
    enable_parser.add_argument("--schedule", default=DEFAULT_SCHEDULE)
    enable_parser.add_argument("--confirmation", required=True)
    enable_parser.set_defaults(handler=enable_semi_auto)

    automation_parser = subparsers.add_parser("save-automation-preferences")
    automation_parser.add_argument("--runtime-root", required=True)
    automation_parser.add_argument("--data-dir", required=True)
    automation_parser.add_argument("--hermes-home", required=True)
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
    scheduler = payload.get("scheduler")
    if isinstance(scheduler, dict) and scheduler.get("ready") is False:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
