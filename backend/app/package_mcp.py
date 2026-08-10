"""Pure package-owned MCP identity and conflict classification."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
from pathlib import Path
from collections.abc import Mapping
from typing import Any


MCP_SERVER_NAME = "taste_database"
MCP_TOOL_NAMES = (
    "search_media",
    "get_media",
    "get_taste_report",
    "get_dimension_profile",
    "get_rating_history",
    "list_evidence_dimensions",
    "list_pending_proposals",
    "get_proposal",
    "submit_pending_proposal",
)


class McpOwnership(str, Enum):
    EXACT = "exact_owned"
    FINGERPRINT_CONFLICT = "same_name_fingerprint_conflict"
    UNRELATED = "unrelated"


@dataclass(frozen=True)
class McpServerSpec:
    name: str
    command: str
    args: tuple[str, ...]
    runtime_root: Path
    fingerprint: str
    environment: tuple[str, ...] = ()
    fingerprint_inputs: tuple[str, ...] = ()
    expected_tool_names: tuple[str, ...] = MCP_TOOL_NAMES

    def as_config(self) -> dict[str, object]:
        environment: dict[str, str] = {}
        for entry in self.environment:
            key, separator, value = entry.partition("=")
            if not separator or not key:
                raise ValueError(f"invalid MCP environment entry: {entry!r}")
            environment[key] = value
        return {
            "command": self.command,
            "args": list(self.args),
            "env": environment,
        }


def _fingerprint(
    name: str,
    command: str,
    args: tuple[str, ...],
    environment: tuple[str, ...],
) -> str:
    payload = json.dumps(
        {
            "name": name,
            "command": command,
            "args": list(args),
            "environment": list(environment),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_mcp_server_spec(
    runtime_root: Path,
    *,
    data_directory: Path | None = None,
    environment_directory: Path | None = None,
) -> McpServerSpec:
    """Build a portable stdio command from one explicit absolute runtime root.

    A data directory is required for profile-scoped onboarding. The legacy
    no-data form remains available for the historical package identity tests;
    it launches the compatibility entry point and is not suitable for live
    profile setup.
    """

    runtime = Path(runtime_root).expanduser()
    if not runtime.is_absolute():
        raise ValueError("MCP runtime root must be an absolute path")
    args = [
        "run",
        "--locked",
        "--directory",
        str(runtime / "backend"),
        "--project",
        str(runtime),
        "python",
        "-m",
    ]
    if data_directory is None:
        args.extend(("app.mcp_server",))
    else:
        data = Path(data_directory).expanduser()
        if not data.is_absolute():
            raise ValueError("MCP data directory must be an absolute path")
        args.extend(("app.mcp_entry", "--data-dir", str(data)))
    args_tuple = tuple(args)
    environment: tuple[str, ...] = ()
    if data_directory is not None:
        if environment_directory is not None:
            environment_root = Path(environment_directory).expanduser()
        elif runtime.name == "artifact":
            # Installed packages expose the runnable project one level below
            # the versioned install directory. Keep the uv environment beside
            # that directory so uninstall can quarantine the whole version
            # without retaining a handle beneath it.
            environment_root = runtime.parent.parent / f".{runtime.parent.name}.venv"
        else:
            # Source artifacts use the versioned project directory directly.
            environment_root = runtime.parent / f".{runtime.name}.venv"
        if not environment_root.is_absolute():
            raise ValueError("MCP environment directory must be an absolute path")
        try:
            environment_root.relative_to(runtime)
        except ValueError:
            pass
        else:
            raise ValueError("MCP environment directory must be outside the immutable artifact")
        environment = (
            f"UV_PROJECT_ENVIRONMENT={environment_root}",
            "PYTHONDONTWRITEBYTECODE=1",
        )
    return McpServerSpec(
        name=MCP_SERVER_NAME,
        command="uv",
        args=args_tuple,
        runtime_root=runtime,
        fingerprint=_fingerprint(MCP_SERVER_NAME, "uv", args_tuple, environment),
        environment=environment,
        fingerprint_inputs=environment,
    )


def classify_mcp_record(record: dict[str, Any], spec: McpServerSpec) -> McpOwnership:
    """Classify one config record without adopting same-name drift."""

    if record.get("name") != spec.name:
        return McpOwnership.UNRELATED
    record_environment = record.get("env") or record.get("environment") or {}
    if isinstance(record_environment, Mapping):
        normalized_environment = tuple(
            f"{key}={record_environment[key]}" for key in sorted(record_environment)
        )
    else:
        normalized_environment = tuple(record_environment)
    if (
        record.get("command") == spec.command
        and tuple(record.get("args") or ()) == spec.args
        and normalized_environment == spec.environment
    ):
        return McpOwnership.EXACT
    return McpOwnership.FINGERPRINT_CONFLICT
