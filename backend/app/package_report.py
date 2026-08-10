"""Versioned, redaction-safe package setup and release evidence reports."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from .package_preflight import PreflightReport


REPORT_SCHEMA_VERSION = "1"


class PackageReportStatus(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    BLOCKED = "blocked"
    FAILED = "failed"


@dataclass(frozen=True)
class PackageReport:
    request_id: str
    package_name: str
    package_version: str
    artifact_hash: str
    artifact_status: str
    terminal_status: PackageReportStatus
    paths: dict[str, str]
    checks: tuple[dict[str, Any], ...]
    commands: dict[str, dict[str, Any]]
    actions: tuple[str, ...] = ()
    non_actions: tuple[str, ...] = ()
    mcp: dict[str, Any] = field(default_factory=lambda: {"mutated": False})
    cron: dict[str, Any] = field(default_factory=lambda: {"mutated": False})
    database: dict[str, Any] = field(default_factory=lambda: {"mutated": False})
    verification: dict[str, Any] = field(default_factory=dict)
    fresh_session_required: bool = True
    caveats: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": REPORT_SCHEMA_VERSION,
            "request_id": self.request_id,
            "package_name": self.package_name,
            "package_version": self.package_version,
            "artifact_hash": self.artifact_hash,
            "artifact_status": self.artifact_status,
            "terminal_status": self.terminal_status.value,
            "paths": dict(self.paths),
            "checks": [dict(check) for check in self.checks],
            "commands": {name: dict(value) for name, value in self.commands.items()},
            "actions": list(self.actions),
            "non_actions": list(self.non_actions),
            "mcp": dict(self.mcp),
            "cron": dict(self.cron),
            "database": dict(self.database),
            "verification": dict(self.verification),
            "fresh_session_required": self.fresh_session_required,
            "caveats": list(self.caveats),
        }


def build_package_report(
    *,
    request_id: str,
    preflight: PreflightReport,
    terminal_status: PackageReportStatus,
    actions: Sequence[str] = (),
    non_actions: Sequence[str] = (),
    mcp: Mapping[str, Any] | None = None,
    cron: Mapping[str, Any] | None = None,
    database: Mapping[str, Any] | None = None,
    verification: Mapping[str, Any] | None = None,
    caveats: Sequence[str] = (),
) -> PackageReport:
    artifact = preflight.artifact
    return PackageReport(
        request_id=request_id,
        package_name=artifact.name if artifact is not None else "unknown",
        package_version=artifact.version if artifact is not None else "unknown",
        artifact_hash=artifact.artifact_hash if artifact is not None else "unavailable",
        artifact_status=artifact.artifact_status if artifact is not None else "unavailable",
        terminal_status=terminal_status,
        paths={name: str(path) for name, path in preflight.paths.items()},
        checks=tuple(
            {
                "code": check.code,
                "status": check.status.value,
                "detail": check.detail,
            }
            for check in preflight.checks
        ),
        commands={
            name: {
                "command": evidence.command,
                "args": list(evidence.args),
                "output": evidence.output,
                "status": evidence.status.value,
                "error": evidence.error,
            }
            for name, evidence in preflight.commands.items()
        },
        actions=tuple(actions),
        non_actions=tuple(non_actions),
        mcp=dict(mcp or {"mutated": False}),
        cron=dict(cron or {"mutated": False}),
        database=dict(
            database
            or {
                "path": str(preflight.paths.get("data_directory", "")),
                "mutated": False,
            }
        ),
        verification=dict(verification or {}),
        caveats=tuple(caveats),
    )


def write_package_report(path: Path, report: PackageReport) -> None:
    """Atomically write one complete report and never leave a temp on success."""

    destination = Path(path).expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            json.dump(report.as_dict(), handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary_path, destination)
        temporary_path = None
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass
