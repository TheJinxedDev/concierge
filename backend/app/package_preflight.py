"""Read-only artifact identity and setup preflight for Concierge.

The preflight layer deliberately stops before package installation, database
migration, MCP configuration, capture consent, or scheduler mutation. It is
safe to run against the real profile because it only reads artifact files,
resolves paths, and optionally executes version probes.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
import hashlib
import ntpath
import os
from pathlib import Path
import re
import subprocess
from typing import Any

from .setup_contract import resolve_data_directory


PACKAGE_NAME = "concierge"
DEFAULT_HERMES_HOME = ".hermes"
DEFAULT_LOCAL_APPDATA_DIRECTORY = "AppData/Local"
BASE_ARTIFACT_FILES = ("SKILL.md", "README.md", "manifest.yaml", "CHANGELOG.md")
MANIFEST_SUPPORT_FILES_KEY = "support_files"
MANIFEST_PACKAGE_FILES_KEY = "package_files"


class PackageArtifactError(ValueError):
    """The local artifact is missing, malformed, or internally inconsistent."""


class CheckStatus(str, Enum):
    PASS = "pass"
    PARTIAL = "partial"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class PreflightCheck:
    code: str
    status: CheckStatus
    detail: str


@dataclass(frozen=True)
class CommandEvidence:
    command: str
    args: tuple[str, ...]
    output: str
    status: CheckStatus = CheckStatus.PASS
    error: str | None = None


@dataclass(frozen=True)
class ArtifactIdentity:
    root: Path
    name: str
    version: str
    artifact_status: str
    files: tuple[str, ...]
    artifact_hash: str
    raw_skill_url: str


@dataclass(frozen=True)
class PreflightReport:
    status: CheckStatus
    publication_status: CheckStatus
    artifact: ArtifactIdentity | None
    paths: dict[str, Path]
    checks: tuple[PreflightCheck, ...]
    commands: dict[str, CommandEvidence]

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-safe report without exposing environment contents."""

        artifact: dict[str, Any] | None = None
        if self.artifact is not None:
            artifact = {
                "root": str(self.artifact.root),
                "name": self.artifact.name,
                "version": self.artifact.version,
                "artifact_status": self.artifact.artifact_status,
                "files": list(self.artifact.files),
                "artifact_hash": self.artifact.artifact_hash,
                "raw_skill_url": self.artifact.raw_skill_url,
            }
        return {
            "status": self.status.value,
            "publication_status": self.publication_status.value,
            "artifact": artifact,
            "paths": {name: str(path) for name, path in self.paths.items()},
            "checks": [
                {
                    "code": check.code,
                    "status": check.status.value,
                    "detail": check.detail,
                }
                for check in self.checks
            ],
            "commands": {
                name: {
                    "command": evidence.command,
                    "args": list(evidence.args),
                    "output": evidence.output,
                    "status": evidence.status.value,
                    "error": evidence.error,
                }
                for name, evidence in self.commands.items()
            },
            "side_effects": {
                "filesystem_mutated": False,
                "database_opened": False,
                "mcp_mutated": False,
                "cron_mutated": False,
                "capture_enabled": False,
            },
        }


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _manifest_values(
    text: str,
) -> tuple[dict[str, str], tuple[str, ...], tuple[str, ...]]:
    """Read the deliberately small manifest shape without a YAML dependency."""

    values: dict[str, str] = {}
    support_files: list[str] = []
    package_files: list[str] = []
    list_key: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if list_key is not None:
            list_match = re.match(r"^\s+-\s+(.+?)\s*$", line)
            if list_match:
                target = support_files if list_key == MANIFEST_SUPPORT_FILES_KEY else package_files
                target.append(_unquote(list_match.group(1)))
                continue
            list_key = None
        top_level = re.match(r"^([A-Za-z_][A-Za-z0-9_]*):\s*(.*?)\s*$", line)
        if not top_level:
            continue
        key, value = top_level.groups()
        if key in {MANIFEST_SUPPORT_FILES_KEY, MANIFEST_PACKAGE_FILES_KEY} and not value:
            list_key = key
            continue
        values[key] = _unquote(value)
    return values, tuple(support_files), tuple(package_files)


def _safe_relative_path(relative_path: str) -> str:
    candidate = Path(relative_path.replace("\\", "/"))
    windows_drive, _ = ntpath.splitdrive(relative_path.replace("/", "\\"))
    if (
        not relative_path
        or candidate.is_absolute()
        or bool(windows_drive)
        or ".." in candidate.parts
        or relative_path.startswith(("/", "\\"))
    ):
        raise PackageArtifactError(f"unsafe artifact support path: {relative_path!r}")
    normalized = "/".join(candidate.parts)
    if normalized in {"", "."}:
        raise PackageArtifactError("artifact support path must not be empty")
    return normalized


def _frontmatter_values(skill_text: str) -> dict[str, str]:
    if not skill_text.startswith("---\n"):
        raise PackageArtifactError("SKILL.md is missing YAML frontmatter")
    try:
        frontmatter = skill_text.split("\n---\n", 1)[0]
    except ValueError as exc:  # pragma: no cover - guarded by startswith
        raise PackageArtifactError("SKILL.md frontmatter is not closed") from exc
    values: dict[str, str] = {}
    for line in frontmatter.splitlines()[1:]:
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_-]*):\s*(.*?)\s*$", line)
        if match:
            values[match.group(1)] = _unquote(match.group(2))
    return values


def _artifact_hash_bytes(
    read_bytes: Callable[[str], bytes], files: tuple[str, ...]
) -> str:
    digest = hashlib.sha256()
    for relative_path in files:
        data = read_bytes(relative_path)
        encoded_path = relative_path.encode("utf-8")
        digest.update(len(encoded_path).to_bytes(4, "big"))
        digest.update(encoded_path)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return "sha256:" + digest.hexdigest()


def _load_artifact(
    artifact_root: Path,
    *,
    read_bytes: Callable[[str], bytes],
    is_symlink: Callable[[str], bool],
) -> ArtifactIdentity:
    if artifact_root.is_symlink():
        raise PackageArtifactError(f"artifact root must not be a symlink: {artifact_root}")
    if not artifact_root.is_dir():
        raise PackageArtifactError(f"artifact root is not a directory: {artifact_root}")

    values, support_files, package_files = _manifest_values(
        read_bytes("manifest.yaml").decode("utf-8")
    )
    for key in ("kind", "name", "version", "artifact_status", "skill_path"):
        if not values.get(key):
            raise PackageArtifactError(f"manifest is missing {key!r}")
    if values["kind"] != "hermes-skill-package":
        raise PackageArtifactError("manifest kind is not hermes-skill-package")
    if values["name"] != PACKAGE_NAME:
        raise PackageArtifactError(f"manifest package name is not {PACKAGE_NAME!r}")
    if values["skill_path"] != "SKILL.md":
        raise PackageArtifactError("manifest skill_path must be SKILL.md")

    files = tuple(
        sorted(
            {
                *_BASE_FILES_WITH_MANIFEST(),
                *(_safe_relative_path(path) for path in support_files),
                *(_safe_relative_path(path) for path in package_files),
            }
        )
    )
    for relative_path in files:
        if is_symlink(relative_path):
            raise PackageArtifactError(
                f"artifact path must not traverse a symlink: {relative_path}"
            )

    skill_values = _frontmatter_values(
        read_bytes("SKILL.md").decode("utf-8").replace("\r\n", "\n")
    )
    if skill_values.get("name") != values["name"]:
        raise PackageArtifactError("SKILL.md name does not match manifest")
    if skill_values.get("version") != values["version"]:
        raise PackageArtifactError("SKILL.md version does not match manifest")

    return ArtifactIdentity(
        root=artifact_root,
        name=values["name"],
        version=values["version"],
        artifact_status=values["artifact_status"],
        files=files,
        artifact_hash=_artifact_hash_bytes(read_bytes, files),
        raw_skill_url=values.get("raw_skill_url", "unresolved"),
    )


def load_artifact(root: Path) -> ArtifactIdentity:
    """Load and verify the exact files named by a local worktree manifest."""

    artifact_root = Path(root).expanduser()

    def read_bytes(relative_path: str) -> bytes:
        path = artifact_root / Path(relative_path)
        if not path.is_file():
            raise PackageArtifactError(f"artifact file is missing: {relative_path}")
        return path.read_bytes()

    return _load_artifact(
        artifact_root,
        read_bytes=read_bytes,
        is_symlink=lambda relative_path: _worktree_path_has_symlink(
            artifact_root, relative_path
        ),
    )


def load_artifact_from_index(root: Path) -> ArtifactIdentity:
    """Load the manifest and declared files from Git's staged index blobs."""

    artifact_root = Path(root).expanduser()

    def read_bytes(relative_path: str) -> bytes:
        completed = subprocess.run(
            ["git", "show", f":{relative_path}"],
            cwd=artifact_root,
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            raise PackageArtifactError(
                f"staged artifact file is missing: {relative_path}"
            )
        return completed.stdout

    def is_symlink(relative_path: str) -> bool:
        completed = subprocess.run(
            ["git", "ls-files", "--stage", "--", relative_path],
            cwd=artifact_root,
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0 or not completed.stdout.strip():
            raise PackageArtifactError(
                f"staged artifact file is missing: {relative_path}"
            )
        return any(line.split(maxsplit=1)[0] == "120000" for line in completed.stdout.splitlines())

    return _load_artifact(
        artifact_root,
        read_bytes=read_bytes,
        is_symlink=is_symlink,
    )


def _BASE_FILES_WITH_MANIFEST() -> tuple[str, ...]:
    return BASE_ARTIFACT_FILES


def _worktree_path_has_symlink(artifact_root: Path, relative_path: str) -> bool:
    """Reject symlinked roots, parent directories, and declared leaves."""

    candidate = artifact_root
    if candidate.is_symlink():
        return True
    for component in Path(relative_path).parts:
        candidate /= component
        if candidate.is_symlink():
            return True
    return False


def _default_local_appdata(environ: Mapping[str, str], home: Path) -> Path:
    value = environ.get("LOCALAPPDATA")
    if value:
        candidate = Path(value)
        if candidate.is_absolute():
            return candidate
    if os.name == "nt":
        return home / DEFAULT_LOCAL_APPDATA_DIRECTORY
    return home / ".local" / "share"


def _default_hermes_home(environ: Mapping[str, str], home: Path) -> Path:
    value = environ.get("HERMES_HOME")
    if value and Path(value).is_absolute():
        return Path(value)
    return home / DEFAULT_HERMES_HOME


def _default_command_runner(command: str, args: tuple[str, ...]) -> str:
    completed = subprocess.run(
        [command, *args],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    output = (completed.stdout or completed.stderr).strip()
    if completed.returncode != 0:
        raise RuntimeError(output or f"exit code {completed.returncode}")
    return output


def run_preflight(
    artifact_root: Path,
    *,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
    check_commands: bool = False,
    command_runner: Callable[[str, tuple[str, ...]], str] | None = None,
) -> PreflightReport:
    """Run read-only artifact, path, and optional prerequisite checks."""

    values = os.environ if environ is None else environ
    home_path = Path.home() if home is None else Path(home)
    checks: list[PreflightCheck] = []
    commands: dict[str, CommandEvidence] = {}
    try:
        artifact = load_artifact(artifact_root)
        checks.append(
            PreflightCheck(
                "artifact_identity",
                CheckStatus.PASS,
                f"{artifact.name}@{artifact.version} {artifact.artifact_hash}",
            )
        )
    except PackageArtifactError as exc:
        artifact = None
        checks.append(PreflightCheck("artifact_identity", CheckStatus.BLOCKED, str(exc)))

    hermes_home = _default_hermes_home(values, home_path)
    local_appdata = _default_local_appdata(values, home_path)
    paths: dict[str, Path] = {
        "hermes_home": hermes_home,
        "local_appdata": local_appdata,
    }

    try:
        data_directory = resolve_data_directory(
            values,
            home=home_path,
            platform=os.name,
        )
        checks.append(
            PreflightCheck(
                "data_path",
                CheckStatus.PASS,
                "explicit absolute override or compatibility fallback resolved",
            )
        )
    except ValueError as exc:
        data_directory = Path(values.get("CONCIERGE_DATA_DIR", "<invalid-data-path>"))
        checks.append(PreflightCheck("data_path_invalid", CheckStatus.BLOCKED, str(exc)))

    paths["data_directory"] = data_directory
    if artifact is not None:
        paths["runtime_path"] = (
            local_appdata / "Concierge" / "packages" / artifact.version
        )
        paths["skill_path"] = hermes_home / "skills" / artifact.name
        paths["install_report_path"] = paths["runtime_path"] / "install-report.json"
        checks.extend(
            (
                PreflightCheck(
                    "runtime_path",
                    CheckStatus.PASS,
                    "versioned runtime is separate from the data directory",
                ),
                PreflightCheck(
                    "profile_path",
                    CheckStatus.PASS,
                    "skill target is scoped beneath the selected Hermes home",
                ),
            )
        )

    if artifact is not None and artifact.raw_skill_url in {"", "unresolved"}:
        publication_status = CheckStatus.BLOCKED
        checks.append(
            PreflightCheck(
                "raw_url_unresolved",
                CheckStatus.PARTIAL,
                "local artifact is usable for disposable verification; publication URL is unresolved",
            )
        )
    else:
        publication_status = CheckStatus.PASS
        checks.append(
            PreflightCheck("raw_url_resolved", CheckStatus.PASS, "versioned raw skill URL is present")
        )

    if check_commands:
        runner = _default_command_runner if command_runner is None else command_runner
        for name in ("hermes", "python", "uv"):
            args = ("--version",)
            try:
                output = runner(name, args)
            except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                evidence = CommandEvidence(
                    command=name,
                    args=args,
                    output="",
                    status=CheckStatus.BLOCKED,
                    error=str(exc),
                )
                commands[name] = evidence
                checks.append(
                    PreflightCheck("command_" + name, CheckStatus.BLOCKED, str(exc))
                )
            else:
                evidence = CommandEvidence(command=name, args=args, output=output)
                commands[name] = evidence
                checks.append(
                    PreflightCheck("command_" + name, CheckStatus.PASS, output or "command succeeded")
                )

    status = (
        CheckStatus.BLOCKED
        if any(check.status is CheckStatus.BLOCKED for check in checks)
        else CheckStatus.PASS
    )
    return PreflightReport(
        status=status,
        publication_status=publication_status,
        artifact=artifact,
        paths=paths,
        checks=tuple(checks),
        commands=commands,
    )
