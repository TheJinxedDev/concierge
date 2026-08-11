#!/usr/bin/env python3
"""Install and initialize Concierge in one profile-scoped command.

This bootstrap is intentionally standard-library-only. It delegates dependency
resolution to ``uv`` in an external environment, never inherits Hermes' Python
module paths, and never configures MCP or cron without the caller's later native
Hermes actions.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Sequence
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
PASSTHROUGH_ENVIRONMENT = (
    "APPDATA",
    "COMSPEC",
    "HOME",
    "LOCALAPPDATA",
    "PATH",
    "PATHEXT",
    "SYSTEMDRIVE",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "USERPROFILE",
    "WINDIR",
)


def _absolute(value: str, label: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError(f"{label} must be an absolute path")
    return path.resolve(strict=False)


def build_child_environment(environment_directory: Path) -> dict[str, str]:
    """Return a minimal process environment without credentials or Hermes imports."""

    values = {
        key: value
        for key in PASSTHROUGH_ENVIRONMENT
        if (value := os.getenv(key)) is not None
    }
    values.update(
        {
            "PYTHONPATH": "",
            "VIRTUAL_ENV": "",
            "PYTHONDONTWRITEBYTECODE": "1",
            "UV_PROJECT_ENVIRONMENT": str(environment_directory),
        }
    )
    return values


def result_mutated(*receipts: dict[str, object] | None) -> bool:
    """Return whether any completed setup stage reported a mutation."""

    return any(bool(receipt and receipt.get("mutated")) for receipt in receipts)


def validate_automation_choices(
    backlog: str | None,
    recent_capture: str | None,
    promotion: str | None,
) -> bool:
    """Validate an optional complete automation decision before setup mutates disk."""

    choices = (backlog, recent_capture, promotion)
    if not any(choice is not None for choice in choices):
        return False
    if not all(choice is not None for choice in choices):
        raise ValueError("all three automation choices must be explicit")
    if promotion == "yes" and backlog != "yes" and recent_capture != "yes":
        raise ValueError("automatic promotion requires an enabled capture source")
    return True


def condense_quickstart_receipt(
    payload: dict[str, object], *, receipt_path: Path
) -> dict[str, object]:
    """Keep console output short while preserving full details on disk."""

    installation = dict(payload["installation"])
    initialization = dict(payload["initialization"])
    automation = payload.get("automation")
    automation_summary = None
    if isinstance(automation, dict):
        jobs = automation.get("native_hermes_jobs")
        plans = jobs.get("plans", []) if isinstance(jobs, dict) else []
        automation_summary = {
            "action": automation.get("action"),
            "mutated": automation.get("mutated"),
            "preferences": automation.get("preferences"),
            "native_hermes_plan_count": len(plans),
            "native_hermes_plan_names": [
                plan.get("name") for plan in plans if isinstance(plan, dict)
            ],
        }
    return {
        "action": payload["action"],
        "mutated": payload["mutated"],
        "receipt_path": str(receipt_path),
        "installation": {
            key: installation.get(key)
            for key in (
                "action",
                "version",
                "artifact_hash",
                "runtime_project_path",
                "skill_path",
            )
        },
        "initialization": {
            key: initialization.get(key)
            for key in ("action", "data_directory", "database_path", "mcp")
        },
        "automation": automation_summary,
        "next_steps": payload.get("next_steps"),
    }


def _write_receipt(path: Path, payload: dict[str, object]) -> None:
    encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == encoded:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(encoded, encoding="utf-8")
    temporary.replace(path)


def _run_json(command: Sequence[str], *, environment: dict[str, str]) -> dict[str, object]:
    completed = subprocess.run(
        list(command),
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "no output"
        raise RuntimeError(f"command failed ({completed.returncode}): {detail}")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Concierge helper returned malformed JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Concierge helper returned a non-object receipt")
    return payload


def quickstart(args: argparse.Namespace) -> dict[str, object]:
    automation_requested = validate_automation_choices(
        args.backlog_cron,
        args.recent_capture_cron,
        args.promotion_cron,
    )
    hermes_home = _absolute(args.hermes_home, "--hermes-home")
    local_appdata = _absolute(args.local_appdata, "--local-appdata")
    environment_directory = _absolute(args.environment_dir, "--environment-dir")
    data_directory = _absolute(
        args.data_dir or str(hermes_home / "concierge-data"), "--data-dir"
    )
    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("uv is required; install or enable uv before retrying")
    environment = build_child_environment(environment_directory)

    base = [uv, "run", "--locked", "--directory", str(ROOT), "--project", str(ROOT)]
    preflight = _run_json(
        [*base, "python", "scripts/concierge_package.py", "preflight", "--check-commands"],
        environment=environment,
    )
    if preflight.get("status") != "pass":
        raise RuntimeError("package preflight did not pass")

    installation = _run_json(
        [
            *base,
            "python",
            "scripts/concierge_package.py",
            "install",
            "--artifact-root",
            str(ROOT),
            "--hermes-home",
            str(hermes_home),
            "--local-appdata",
            str(local_appdata),
        ],
        environment=environment,
    )
    if installation.get("action") not in {"installed", "noop"}:
        raise RuntimeError("package installation did not produce an owned installation")
    runtime_root = Path(str(installation["runtime_project_path"]))

    runtime_base = [
        uv,
        "run",
        "--locked",
        "--directory",
        str(runtime_root),
        "--project",
        str(runtime_root),
    ]
    initialization = _run_json(
        [
            *runtime_base,
            "python",
            "scripts/concierge_setup.py",
            "initialize",
            "--runtime-root",
            str(runtime_root),
            "--data-dir",
            str(data_directory),
            "--environment-dir",
            str(environment_directory),
        ],
        environment=environment,
    )
    if initialization.get("action") not in {"database_initialized", "database_ready"}:
        raise RuntimeError("database initialization did not complete")

    automation = None
    if automation_requested:
        automation = _run_json(
            [
                *runtime_base,
                "python",
                "scripts/concierge_setup.py",
                "save-automation-preferences",
                "--runtime-root",
                str(runtime_root),
                "--data-dir",
                str(data_directory),
                "--decision-id",
                args.decision_id or f"onboarding-{uuid4()}",
                "--backlog-cron",
                args.backlog_cron,
                "--recent-capture-cron",
                args.recent_capture_cron,
                "--promotion-cron",
                args.promotion_cron,
                "--backlog-policy",
                args.backlog_policy,
                "--favorite-media-interview",
                args.favorite_media_interview,
                "--confirmation",
                "I explicitly choose Concierge automation",
            ],
            environment=environment,
        )

    payload = {
        "action": "concierge_ready_for_hermes_registration",
        "mutated": result_mutated(installation, initialization, automation),
        "installation": installation,
        "initialization": initialization,
        "automation": automation,
        "next_steps": {
            "mcp": "Register and test the exact MCP spec in initialization.mcp with native Hermes.",
            "automation": "Explain and ask the three independent cron choices; full native plans are in receipt_path.",
        },
    }
    receipt_path = data_directory / "quickstart-receipt.json"
    _write_receipt(receipt_path, payload)
    return condense_quickstart_receipt(payload, receipt_path=receipt_path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hermes-home", required=True)
    parser.add_argument("--local-appdata", required=True)
    parser.add_argument("--environment-dir", required=True)
    parser.add_argument("--data-dir")
    parser.add_argument("--backlog-cron", choices=("yes", "no"))
    parser.add_argument("--recent-capture-cron", choices=("yes", "no"))
    parser.add_argument("--promotion-cron", choices=("yes", "no"))
    parser.add_argument(
        "--backlog-policy", choices=("process_existing", "start_fresh"), default="start_fresh"
    )
    parser.add_argument("--favorite-media-interview", choices=("yes", "no"), default="no")
    parser.add_argument("--decision-id")
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        payload = quickstart(build_parser().parse_args(argv))
    except (KeyError, OSError, RuntimeError, ValueError) as exc:
        print(json.dumps({"action": "failed", "mutated": False, "reason": str(exc)}))
        return 2
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
