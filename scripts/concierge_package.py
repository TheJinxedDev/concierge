#!/usr/bin/env python3
"""Package-facing Concierge preflight and local lifecycle commands.

Read-only preflight may inspect the active environment. Mutating commands require
explicit Hermes-home and local-appdata paths so a package smoke test cannot
silently target the user's default profile.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.package_lifecycle import (  # noqa: E402
    LifecycleAction,
    install_artifact,
    recover_interrupted_install,
    uninstall_artifact,
    upgrade_artifact,
)
from app.package_preflight import (  # noqa: E402
    CheckStatus,
    run_preflight,
)


def _path(value: str | None) -> Path | None:
    return Path(value).expanduser() if value else None


def _environment(args: argparse.Namespace) -> dict[str, str]:
    values = dict(os.environ)
    for option, environment_key in (
        ("hermes_home", "HERMES_HOME"),
        ("local_appdata", "LOCALAPPDATA"),
        ("data_dir", "CONCIERGE_DATA_DIR"),
    ):
        value = getattr(args, option, None)
        if value is not None:
            values[environment_key] = value
    return values


def _installation_payload(result) -> dict[str, object]:
    payload: dict[str, object] = {
        "action": result.action.value,
        "reason": result.reason,
        "mutated": result.mutated,
    }
    if result.installation is not None:
        installation = result.installation
        payload.update(
            {
                "package_name": installation.package_name,
                "version": installation.version,
                "artifact_hash": installation.artifact_hash,
                "artifact_files": list(installation.artifact_files),
                "skill_files": list(installation.skill_files),
                "runtime_path": str(installation.runtime_path),
                "skill_path": str(installation.skill_path),
            }
        )
    if result.cleaned_paths:
        payload["cleaned_paths"] = [str(path) for path in result.cleaned_paths]
    return payload


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temporary_path = Path(handle.name)
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(temporary_path, path)


def _require_mutation_paths(args: argparse.Namespace) -> tuple[Path, Path]:
    hermes_home = _path(args.hermes_home)
    local_appdata = _path(args.local_appdata)
    if hermes_home is None or local_appdata is None:
        raise ValueError(
            "mutating package commands require explicit --hermes-home and --local-appdata"
        )
    return hermes_home, local_appdata


def _add_artifact_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--artifact-root", default=str(ROOT))


def _add_target_arguments(parser: argparse.ArgumentParser, *, include_data: bool = False) -> None:
    parser.add_argument("--hermes-home")
    parser.add_argument("--local-appdata")
    if include_data:
        parser.add_argument("--data-dir")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="concierge_package")
    subparsers = parser.add_subparsers(dest="command", required=True)

    preflight = subparsers.add_parser("preflight", help="run read-only package checks")
    _add_artifact_argument(preflight)
    _add_target_arguments(preflight, include_data=True)
    preflight.add_argument("--check-commands", action="store_true")
    preflight.add_argument("--report")

    install = subparsers.add_parser("install", help="install into explicit target paths")
    _add_artifact_argument(install)
    _add_target_arguments(install)

    upgrade = subparsers.add_parser("upgrade", help="upgrade an exact prior installation")
    _add_artifact_argument(upgrade)
    _add_target_arguments(upgrade)
    upgrade.add_argument("--previous-version", required=True)
    upgrade.add_argument("--previous-artifact-hash", required=True)

    uninstall = subparsers.add_parser("uninstall", help="remove an exact package installation")
    _add_target_arguments(uninstall)
    uninstall.add_argument("--version", required=True)
    uninstall.add_argument("--expected-artifact-hash", required=True)

    recover = subparsers.add_parser("recover", help="clean owned interrupted staging")
    _add_target_arguments(recover)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "preflight":
            report = run_preflight(
                Path(args.artifact_root),
                environ=_environment(args),
                check_commands=args.check_commands,
            )
            payload = report.as_dict()
            if args.report:
                report_path = Path(args.report).expanduser()
                payload["side_effects"] = {
                    **payload.get("side_effects", {}),
                    "filesystem_mutated": True,
                    "report_written": True,
                    "report_path": str(report_path),
                }
                _write_json(report_path, payload)
            print(json.dumps(payload, indent=2, sort_keys=True))
            return 0 if report.status is CheckStatus.PASS else 2

        hermes_home, local_appdata = _require_mutation_paths(args)
        if args.command == "install":
            result = install_artifact(Path(args.artifact_root), hermes_home, local_appdata)
        elif args.command == "upgrade":
            result = upgrade_artifact(
                Path(args.artifact_root),
                hermes_home,
                local_appdata,
                previous_version=args.previous_version,
                previous_artifact_hash=args.previous_artifact_hash,
            )
        elif args.command == "uninstall":
            result = uninstall_artifact(
                hermes_home,
                local_appdata,
                version=args.version,
                expected_artifact_hash=args.expected_artifact_hash,
            )
        elif args.command == "recover":
            result = recover_interrupted_install(hermes_home, local_appdata)
        else:  # pragma: no cover - argparse constrains the command set
            raise ValueError(f"unknown command: {args.command}")
        print(json.dumps(_installation_payload(result), indent=2, sort_keys=True))
        return 0 if result.action not in {LifecycleAction.CONFLICT, LifecycleAction.FAILED, LifecycleAction.MISSING} else 2
    except (OSError, ValueError) as exc:
        print(
            json.dumps(
                {"action": "failed", "reason": str(exc), "mutated": False},
                indent=2,
                sort_keys=True,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
