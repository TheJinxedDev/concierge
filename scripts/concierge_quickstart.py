#!/usr/bin/env python3
"""Install and initialize Concierge in one profile-scoped command.

This bootstrap is intentionally standard-library-only. It delegates dependency
resolution to ``uv`` in an external environment, never inherits Hermes' Python
module paths, and never configures MCP or cron without the caller's later native
Hermes actions.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
from typing import Sequence
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.package_preflight import load_artifact  # noqa: E402
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


@dataclass(frozen=True)
class SetupContext:
    hermes_home: Path
    local_appdata: Path
    environment_directory: Path
    data_directory: Path


def _absolute(value: str, label: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError(f"{label} must be an absolute path")
    return path.resolve(strict=False)


def build_child_environment(
    environment_directory: Path, data_directory: Path
) -> dict[str, str]:
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
            "CONCIERGE_DATA_DIR": str(data_directory),
        }
    )
    return values


def build_ui_handoff(
    runtime_root: Path,
    data_directory: Path,
    environment_directory: Path,
    *,
    port: int = 4173,
) -> dict[str, object]:
    """Return the exact native command and URLs for the installed browser UI."""

    url = f"http://127.0.0.1:{port}/"
    python = environment_directory / (
        "Scripts/python.exe" if os.name == "nt" else "bin/python"
    )
    return {
        "launch_command": [
            str(python),
            "-I",
            str(runtime_root / "scripts" / "launch.py"),
            "--data-dir",
            str(data_directory),
            "--port",
            str(port),
        ],
        "url": url,
        "readiness_url": f"{url}health",
    }


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


def derive_backlog_policy(backlog: str | None, policy: str | None) -> str:
    """Require a backlog policy only when one-time backlog capture is selected."""

    if backlog == "yes" and policy is None:
        raise ValueError("backlog policy is required when backlog capture is enabled")
    if backlog != "yes" and policy not in {None, "start_fresh"}:
        raise ValueError("backlog policy is only meaningful with backlog capture")
    return policy or "start_fresh"


def _read_receipt(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read a valid quickstart receipt: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError("quickstart receipt must contain a JSON object")
    return payload


def load_setup_context(path: Path) -> SetupContext:
    payload = _read_receipt(path)
    context = payload.get("setup_context")
    if not isinstance(context, dict):
        raise ValueError("quickstart receipt has no setup_context")
    try:
        return SetupContext(
            hermes_home=_absolute(str(context["hermes_home"]), "receipt hermes_home"),
            local_appdata=_absolute(str(context["local_appdata"]), "receipt local_appdata"),
            environment_directory=_absolute(
                str(context["environment_directory"]), "receipt environment_directory"
            ),
            data_directory=_absolute(str(context["data_directory"]), "receipt data_directory"),
        )
    except KeyError as exc:
        raise ValueError(f"quickstart receipt setup_context is missing {exc.args[0]}") from exc


def _installed_artifact_hash(runtime_root: Path) -> str:
    return load_artifact(runtime_root).artifact_hash


def verify_quickstart_receipt(
    path: Path, *, artifact_hash_reader=_installed_artifact_hash
) -> dict[str, object]:
    """Verify the exact owned install and database through read-only operations."""

    payload = _read_receipt(path)
    installation = payload.get("installation")
    initialization = payload.get("initialization")
    if not isinstance(installation, dict) or not isinstance(initialization, dict):
        raise ValueError("quickstart receipt lacks installation or initialization evidence")
    runtime_root = _absolute(
        str(installation.get("runtime_project_path", "")), "receipt runtime_project_path"
    )
    skill_path = _absolute(str(installation.get("skill_path", "")), "receipt skill_path")
    database_path = _absolute(
        str(initialization.get("database_path", "")), "receipt database_path"
    )
    if not runtime_root.is_dir() or not skill_path.is_dir() or not database_path.is_file():
        raise ValueError("quickstart receipt points to a missing runtime, skill, or database")
    web_root = runtime_root / "frontend" / "dist"
    assets = web_root / "assets"
    if not (web_root / "index.html").is_file() or not assets.is_dir() or not any(assets.iterdir()):
        raise ValueError("quickstart receipt points to an installation without a built UI")
    expected_hash = installation.get("artifact_hash")
    actual_hash = artifact_hash_reader(runtime_root)
    if not isinstance(expected_hash, str) or actual_hash != expected_hash:
        raise ValueError("installed artifact hash does not match the quickstart receipt")
    uri = f"file:{database_path.as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        canonical_media = connection.execute("SELECT COUNT(*) FROM media_items").fetchone()[0]
        pending_proposals = connection.execute("SELECT COUNT(*) FROM proposals").fetchone()[0]
    return {
        "action": "concierge_installation_verified",
        "mutated": False,
        "artifact_hash": actual_hash,
        "runtime_project_path": str(runtime_root),
        "skill_path": str(skill_path),
        "database_path": str(database_path),
        "ui_bundle": str(web_root),
        "snapshot": {
            "canonical_media": canonical_media,
            "pending_proposals": pending_proposals,
        },
        "native_hermes_checks": [
            "hermes mcp list",
            "hermes mcp test taste_database",
        ],
    }


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
        "ui": payload.get("ui"),
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
    if args.receipt:
        receipt_path = Path(args.receipt)
        verify_quickstart_receipt(receipt_path)
        context = load_setup_context(receipt_path)
        if any((args.hermes_home, args.local_appdata, args.environment_dir, args.data_dir)):
            raise ValueError("--receipt cannot be combined with explicit setup paths")
    else:
        if not all((args.hermes_home, args.local_appdata, args.environment_dir)):
            raise ValueError("setup requires --hermes-home, --local-appdata, and --environment-dir")
        hermes_home = _absolute(args.hermes_home, "--hermes-home")
        context = SetupContext(
            hermes_home=hermes_home,
            local_appdata=_absolute(args.local_appdata, "--local-appdata"),
            environment_directory=_absolute(args.environment_dir, "--environment-dir"),
            data_directory=_absolute(
                args.data_dir or str(hermes_home / "concierge-data"), "--data-dir"
            ),
        )
    hermes_home = context.hermes_home
    local_appdata = context.local_appdata
    environment_directory = context.environment_directory
    data_directory = context.data_directory
    backlog_policy = derive_backlog_policy(args.backlog_cron, args.backlog_policy)
    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("uv is required; install or enable uv before retrying")
    environment = build_child_environment(environment_directory, data_directory)

    base = [uv, "run", "--locked", "--directory", str(ROOT), "--project", str(ROOT)]
    preflight = _run_json(
        [
            *base,
            "python",
            "scripts/concierge_package.py",
            "preflight",
            "--check-commands",
            "--data-dir",
            str(data_directory),
        ],
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
                backlog_policy,
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
        "ui": build_ui_handoff(
            runtime_root,
            data_directory,
            environment_directory,
        ),
        "setup_context": {
            "hermes_home": str(hermes_home),
            "local_appdata": str(local_appdata),
            "environment_directory": str(environment_directory),
            "data_directory": str(data_directory),
        },
        "next_steps": {
            "mcp": "Register and test the exact MCP spec in initialization.mcp with native Hermes.",
            "automation": "Explain and ask the three independent cron choices; full native plans are in receipt_path.",
            "ui": "Start ui.launch_command, wait for ui.readiness_url, then point the user to ui.url.",
        },
    }
    receipt_path = data_directory / "quickstart-receipt.json"
    _write_receipt(receipt_path, payload)
    return condense_quickstart_receipt(payload, receipt_path=receipt_path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hermes-home")
    parser.add_argument("--local-appdata")
    parser.add_argument("--environment-dir")
    parser.add_argument("--data-dir")
    parser.add_argument("--receipt")
    parser.add_argument("--verify-receipt")
    parser.add_argument("--backlog-cron", choices=("yes", "no"))
    parser.add_argument("--recent-capture-cron", choices=("yes", "no"))
    parser.add_argument("--promotion-cron", choices=("yes", "no"))
    parser.add_argument(
        "--backlog-policy", choices=("process_existing", "start_fresh")
    )
    parser.add_argument("--favorite-media-interview", choices=("yes", "no"), default="no")
    parser.add_argument("--decision-id")
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        if args.verify_receipt:
            if any(
                value is not None
                for value in (
                    args.receipt,
                    args.hermes_home,
                    args.local_appdata,
                    args.environment_dir,
                    args.data_dir,
                    args.backlog_cron,
                    args.recent_capture_cron,
                    args.promotion_cron,
                )
            ):
                raise ValueError("--verify-receipt cannot be combined with setup options")
            payload = verify_quickstart_receipt(Path(args.verify_receipt))
        else:
            payload = quickstart(args)
    except (KeyError, OSError, RuntimeError, ValueError) as exc:
        print(json.dumps({"action": "failed", "mutated": False, "reason": str(exc)}))
        return 2
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
