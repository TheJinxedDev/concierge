"""Fail-closed package-file installation, upgrade, uninstall, and recovery.

This module owns only Concierge package/runtime files and the installed skill
tree. It never opens SQLite, edits Hermes MCP configuration, creates cron jobs,
or changes capture enablement. Those are separate ownership boundaries.
"""

from __future__ import annotations

from collections.abc import Mapping
import ctypes
from dataclasses import dataclass
from enum import Enum
from functools import wraps
import hashlib
import inspect
import json
import os
from pathlib import Path
import re
import shutil
import sys
import uuid

from .package_preflight import ArtifactIdentity, PackageArtifactError, load_artifact
from .file_lock import exclusive_file_lock


INSTALLATION_SCHEMA_VERSION = "1"
RUNTIME_ROOT_PARTS = ("Concierge", "packages")
ARTIFACT_DIRECTORY = "artifact"
SKILL_SUPPORT_ROOTS = ("references", "templates", "scripts", "assets")
STAGING_PREFIX = ".concierge-install-"
STAGE_MARKER_FILENAME = ".concierge-stage.json"
PACKAGE_LIFECYCLE_LOCK_FILENAME = ".concierge-package-lifecycle.lock"
PACKAGE_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class LifecycleAction(str, Enum):
    INSTALLED = "installed"
    NOOP = "noop"
    UPDATED = "updated"
    REMOVED = "removed"
    RECOVERED = "recovered"
    CONFLICT = "conflict"
    MISSING = "missing"
    FAILED = "failed"


@dataclass(frozen=True)
class PackageInstallation:
    package_name: str
    version: str
    artifact_hash: str
    artifact_files: tuple[str, ...]
    skill_files: tuple[str, ...]
    skill_tree_hash: str
    runtime_path: Path
    skill_path: Path

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": INSTALLATION_SCHEMA_VERSION,
            "package_name": self.package_name,
            "version": self.version,
            "artifact_directory": ARTIFACT_DIRECTORY,
            "artifact_hash": self.artifact_hash,
            "artifact_files": list(self.artifact_files),
            "skill_files": list(self.skill_files),
            "skill_tree_hash": self.skill_tree_hash,
        }


@dataclass(frozen=True)
class LifecycleResult:
    action: LifecycleAction
    reason: str
    mutated: bool
    installation: PackageInstallation | None = None
    cleaned_paths: tuple[Path, ...] = ()


@dataclass(frozen=True)
class _PathIdentity:
    device: int
    inode: int
    size: int
    modified_ns: int
    content_hash: str


def _path_identity(path: Path) -> _PathIdentity:
    _assert_no_symlink_component(path, "package source")
    try:
        stat = path.stat()
    except OSError as exc:
        raise PackageArtifactError(f"package source disappeared: {path}") from exc
    return _PathIdentity(
        device=stat.st_dev,
        inode=stat.st_ino,
        size=stat.st_size,
        modified_ns=stat.st_mtime_ns,
        content_hash=(
            _tree_hash(path)
            if path.is_dir()
            else hashlib.sha256(path.read_bytes()).hexdigest()
        ),
    )


def _assert_path_identity(path: Path, expected: _PathIdentity) -> None:
    if _path_identity(path) != expected:
        raise PackageArtifactError(
            f"package source identity changed before mutation: {path}"
        )


def _runtime_path(local_appdata: Path, version: str) -> Path:
    if not isinstance(version, str) or not PACKAGE_VERSION_PATTERN.fullmatch(version):
        raise PackageArtifactError("safe package version is required")
    return Path(local_appdata).expanduser() / RUNTIME_ROOT_PARTS[0] / RUNTIME_ROOT_PARTS[1] / version


def _skill_path(hermes_home: Path, package_name: str) -> Path:
    return Path(hermes_home).expanduser() / "skills" / package_name


def _canonical_root(path: Path, label: str) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    current = candidate
    while True:
        if current.is_symlink():
            raise PackageArtifactError(f"{label} must not use a symlinked root or parent")
        if current == current.parent:
            break
        current = current.parent
    return candidate.resolve(strict=False)


def _safe_target(path: Path, label: str) -> Path:
    candidate = Path(path)
    current = candidate
    while True:
        if current.is_symlink():
            raise PackageArtifactError(f"{label} must not use a symlinked root or parent")
        if current == current.parent:
            break
        current = current.parent
    return candidate


def _assert_no_symlink_component(path: Path, label: str) -> None:
    """Fence one filesystem operation against symlinked roots or parents."""

    current = Path(path)
    while True:
        if current.is_symlink():
            raise PackageArtifactError(f"{label} must not use a symlinked root or parent")
        if current == current.parent:
            break
        current = current.parent


def _package_lifecycle_guard(method):
    @wraps(method)
    def guarded(*args, **kwargs):
        name = method.__name__
        bound = inspect.signature(method).bind(*args, **kwargs)
        if name in {"install_artifact", "upgrade_artifact"}:
            bound.arguments["artifact_root"] = _canonical_root(
                bound.arguments["artifact_root"], "artifact root"
            )
            bound.arguments["hermes_home"] = _canonical_root(
                bound.arguments["hermes_home"], "Hermes home"
            )
        else:
            bound.arguments["hermes_home"] = _canonical_root(
                bound.arguments["hermes_home"], "Hermes home"
            )
        bound.arguments["local_appdata"] = _canonical_root(
            bound.arguments["local_appdata"], "local appdata"
        )
        local_appdata = bound.arguments["local_appdata"]
        lock_path = local_appdata / RUNTIME_ROOT_PARTS[0] / PACKAGE_LIFECYCLE_LOCK_FILENAME
        with exclusive_file_lock(lock_path):
            return method(*bound.args, **bound.kwargs)

    return guarded


def _write_stage_marker(path: Path, *, token: str, role: str, operation: str) -> None:
    _write_json(
        path / STAGE_MARKER_FILENAME,
        {
            "schema_version": INSTALLATION_SCHEMA_VERSION,
            "package_name": "concierge",
            "token": token,
            "role": role,
            "operation": operation,
        },
    )


def _is_owned_stage(path: Path, *, token: str | None = None) -> bool:
    token = path.name.removeprefix(STAGING_PREFIX) if token is None else token
    if not token:
        return False
    record = _read_record(path / STAGE_MARKER_FILENAME)
    return bool(
        record is not None
        and record.get("schema_version") == INSTALLATION_SCHEMA_VERSION
        and record.get("package_name") == "concierge"
        and record.get("token") == token
        and record.get("role") in {"runtime", "skill"}
        and record.get("operation") in {"install", "upgrade"}
    )


def _remove_stage_marker(path: Path) -> None:
    _assert_no_symlink_component(path, "stage marker")
    marker = path / STAGE_MARKER_FILENAME
    if marker.exists() or marker.is_symlink():
        marker.unlink()


def _skill_files(artifact: ArtifactIdentity) -> tuple[str, ...]:
    return tuple(
        sorted(
            relative_path
            for relative_path in artifact.files
            if relative_path == "SKILL.md"
            or relative_path.split("/", 1)[0] in SKILL_SUPPORT_ROOTS
        )
    )


def _skill_tree_matches_artifact(skill_path: Path, artifact: ArtifactIdentity) -> bool:
    """Allow an exact raw-skill bootstrap tree to be adopted safely.

    ``hermes skills install <raw SKILL.md>`` materializes only the skill and
    support roots.  The versioned package installer may then add the complete
    runtime, but only when that pre-existing tree has the same owned files and
    content, allowing only platform newline normalization for UTF-8 text files.
    Any user edit, extra file, symlink, or missing file keeps the fail-closed
    conflict behavior.
    """

    if not skill_path.is_dir():
        return False
    _assert_no_symlink_component(skill_path, "existing skill target")
    skill_files = _skill_files(artifact)
    return (
        _tree_files(skill_path) == skill_files
        and _skill_tree_hash(skill_path, skill_files)
        == _skill_tree_hash(artifact.root, skill_files)
    )


def _hash_files(root: Path, files: tuple[str, ...]) -> str:
    digest = hashlib.sha256()
    for relative_path in files:
        path = root / Path(relative_path)
        data = path.read_bytes()
        encoded_path = relative_path.encode("utf-8")
        digest.update(len(encoded_path).to_bytes(4, "big"))
        digest.update(encoded_path)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return "sha256:" + digest.hexdigest()


def _skill_tree_hash(root: Path, files: tuple[str, ...]) -> str:
    """Hash skill support content with only newline normalization.

    Hermes may materialize a raw Markdown/Python support tree using Windows
    CRLF line endings even when the immutable archive stores LF.  Normalize
    only UTF-8 text newlines so that this transport detail is not mistaken for
    user drift; all filenames and all non-newline content remain exact.
    """

    digest = hashlib.sha256()
    for relative_path in files:
        path = root / Path(relative_path)
        data = path.read_bytes()
        try:
            data = data.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
        except UnicodeDecodeError:
            pass
        encoded_path = relative_path.encode("utf-8")
        digest.update(len(encoded_path).to_bytes(4, "big"))
        digest.update(encoded_path)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return "sha256:" + digest.hexdigest()


def _tree_files(root: Path, *, ignore_interpreter_cache: bool = False) -> tuple[str, ...]:
    if not root.is_dir():
        return ()
    return tuple(
        sorted(
            path.relative_to(root).as_posix()
            for path in root.rglob("*")
            if path.is_file()
            and not path.is_symlink()
            and not (
                ignore_interpreter_cache
                and ("__pycache__" in path.parts or path.suffix == ".pyc")
            )
        )
    )


def _runtime_artifact_files(root: Path) -> tuple[str, ...]:
    """Return runtime files while excluding caches generated by Python imports."""
    return _tree_files(root, ignore_interpreter_cache=True)


def _tree_hash(root: Path) -> str:
    return _hash_files(root, _tree_files(root))


def _copy_files(source_root: Path, destination_root: Path, files: tuple[str, ...]) -> None:
    for relative_path in files:
        source = source_root / Path(relative_path)
        destination = destination_root / Path(relative_path)
        _assert_no_symlink_component(source, "artifact source")
        source_identity = _path_identity(source)
        content = source.read_bytes()
        _assert_path_identity(source, source_identity)
        _assert_no_symlink_component(destination, "artifact destination")
        destination.parent.mkdir(parents=True, exist_ok=True)
        _assert_no_symlink_component(destination, "artifact destination")
        try:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            if hasattr(os, "O_BINARY"):
                flags |= os.O_BINARY
            descriptor = os.open(destination, flags, 0o644)
        except FileExistsError as exc:
            raise PackageArtifactError(
                f"artifact destination appeared during staging: {destination}"
            ) from exc
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())


def _write_json(path: Path, payload: Mapping[str, object]) -> None:
    _assert_no_symlink_component(path, "package metadata")
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _read_record(path: Path) -> dict[str, object] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _installation_from_record(
    record: Mapping[str, object],
    runtime_path: Path,
    skill_path: Path,
) -> PackageInstallation | None:
    try:
        if record.get("schema_version") != INSTALLATION_SCHEMA_VERSION:
            return None
        package_name = record["package_name"]
        version = record["version"]
        artifact_hash = record["artifact_hash"]
        artifact_files = record["artifact_files"]
        skill_files = record["skill_files"]
        skill_tree_hash = record["skill_tree_hash"]
        if not all(isinstance(value, str) for value in (package_name, version, artifact_hash, skill_tree_hash)):
            return None
        if not isinstance(artifact_files, list) or not all(isinstance(value, str) for value in artifact_files):
            return None
        if not isinstance(skill_files, list) or not all(isinstance(value, str) for value in skill_files):
            return None
    except (KeyError, TypeError):
        return None
    return PackageInstallation(
        package_name=package_name,
        version=version,
        artifact_hash=artifact_hash,
        artifact_files=tuple(artifact_files),
        skill_files=tuple(skill_files),
        skill_tree_hash=skill_tree_hash,
        runtime_path=runtime_path,
        skill_path=skill_path,
    )


def _read_installation(runtime_path: Path, skill_path: Path) -> PackageInstallation | None:
    record = _read_record(runtime_path / "installation.json")
    if record is None:
        return None
    return _installation_from_record(record, runtime_path, skill_path)


def _record_matches_artifact(
    installation: PackageInstallation,
    artifact: ArtifactIdentity,
) -> bool:
    if (
        installation.package_name != artifact.name
        or installation.version != artifact.version
        or installation.artifact_hash != artifact.artifact_hash
        or installation.artifact_files != artifact.files
        or installation.skill_files != _skill_files(artifact)
    ):
        return False
    artifact_root = installation.runtime_path / ARTIFACT_DIRECTORY
    if _runtime_artifact_files(artifact_root) != artifact.files:
        return False
    if _hash_files(artifact_root, artifact.files) != artifact.artifact_hash:
        return False
    if _tree_files(installation.skill_path) != installation.skill_files:
        return False
    return _skill_tree_hash(installation.skill_path, installation.skill_files) == installation.skill_tree_hash


def _record_matches_hash(installation: PackageInstallation, expected_hash: str | None) -> bool:
    return expected_hash is None or installation.artifact_hash == expected_hash


def _stage_artifact(
    artifact: ArtifactIdentity,
    runtime_stage: Path,
    skill_stage: Path,
) -> PackageInstallation:
    artifact_stage = runtime_stage / ARTIFACT_DIRECTORY
    _copy_files(artifact.root, artifact_stage, artifact.files)
    if _hash_files(artifact_stage, artifact.files) != artifact.artifact_hash:
        raise PackageArtifactError("artifact source changed during staging")
    skill_files = _skill_files(artifact)
    _copy_files(artifact.root, skill_stage, skill_files)
    installation = PackageInstallation(
        package_name=artifact.name,
        version=artifact.version,
        artifact_hash=artifact.artifact_hash,
        artifact_files=artifact.files,
        skill_files=skill_files,
        skill_tree_hash=_skill_tree_hash(skill_stage, skill_files),
        runtime_path=runtime_stage,
        skill_path=skill_stage,
    )
    _write_json(runtime_stage / "installation.json", installation.as_dict())
    return installation


def _atomic_move_no_replace(
    source: Path,
    target: Path,
    *,
    expected_source_identity: _PathIdentity | None = None,
) -> None:
    """Move a directory without ever replacing an existing destination."""

    if expected_source_identity is not None:
        _assert_path_identity(source, expected_source_identity)
    if sys.platform == "win32":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        move_file = kernel32.MoveFileExW
        move_file.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
        move_file.restype = ctypes.c_int
        # MOVEFILE_COPY_ALLOWED is safe here; deliberately omit
        # MOVEFILE_REPLACE_EXISTING so a destination race fails closed.
        if not move_file(str(source), str(target), 0x00000002):
            error = ctypes.get_last_error()
            raise PackageArtifactError(
                f"atomic no-replace move failed ({error}): {source} -> {target}"
            )
        return

    try:
        libc = ctypes.CDLL(None, use_errno=True)
        renameat2 = libc.renameat2
    except (AttributeError, OSError) as error:
        raise PackageArtifactError(
            "atomic no-replace directory moves are unavailable on this platform"
        ) from error
    renameat2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameat2.restype = ctypes.c_int
    result = renameat2(
        -100,
        os.fsencode(source),
        -100,
        os.fsencode(target),
        0x1,  # RENAME_NOREPLACE
    )
    if result != 0:
        error = ctypes.get_errno()
        raise PackageArtifactError(
            f"atomic no-replace move failed ({error}): {source} -> {target}"
        )


def _promote(
    source: Path,
    target: Path,
    *,
    expected_source_identity: _PathIdentity | None = None,
) -> None:
    _assert_no_symlink_component(source, "promotion source")
    _assert_no_symlink_component(target, "promotion target")
    target.parent.mkdir(parents=True, exist_ok=True)
    _assert_no_symlink_component(target, "promotion target")
    _atomic_move_no_replace(
        source,
        target,
        expected_source_identity=expected_source_identity,
    )
    _assert_no_symlink_component(target, "promotion target")


def _remove_tree(path: Path, *, require_owned_stage: bool = False) -> None:
    _assert_no_symlink_component(path, "package removal target")
    if not path.exists():
        return
    source_identity = _path_identity(path)
    stage_token = path.name.removeprefix(STAGING_PREFIX) if require_owned_stage else None
    quarantine = path.with_name(f".{path.name}.delete-{uuid.uuid4().hex}")
    _atomic_move_no_replace(
        path,
        quarantine,
        expected_source_identity=source_identity,
    )
    try:
        _assert_no_symlink_component(quarantine, "package quarantine target")
        if require_owned_stage and not _is_owned_stage(quarantine, token=stage_token):
            raise PackageArtifactError(
                "quarantined staging candidate ownership changed before removal"
            )
        shutil.rmtree(quarantine)
    except Exception:
        # Leave an unremoved quarantine in place for explicit recovery rather
        # than retrying against a path that may have changed identity.
        raise


def _conflict(reason: str, installation: PackageInstallation | None = None) -> LifecycleResult:
    return LifecycleResult(
        action=LifecycleAction.CONFLICT,
        reason=reason,
        mutated=False,
        installation=installation,
    )


@_package_lifecycle_guard
def install_artifact(
    artifact_root: Path,
    hermes_home: Path,
    local_appdata: Path,
) -> LifecycleResult:
    """Install a local artifact exactly once into the selected target paths."""

    artifact = load_artifact(artifact_root)
    runtime_path = _safe_target(
        _runtime_path(local_appdata, artifact.version), "runtime target"
    )
    skill_path = _safe_target(_skill_path(hermes_home, artifact.name), "skill target")
    reusable_raw_skill = (
        not runtime_path.exists()
        and _skill_tree_matches_artifact(skill_path, artifact)
    )
    if runtime_path.exists() or skill_path.exists():
        existing = _read_installation(runtime_path, skill_path)
        if existing is not None and _record_matches_artifact(existing, artifact):
            return LifecycleResult(LifecycleAction.NOOP, "exact package installation already exists", False, existing)
        if not reusable_raw_skill:
            return _conflict("package or skill path already exists but is not an exact owned installation", existing)

    token = uuid.uuid4().hex
    runtime_stage = runtime_path.parent / f"{STAGING_PREFIX}{token}"
    skill_stage = skill_path.parent / f"{STAGING_PREFIX}{token}"
    runtime_promoted = False
    skill_promoted = False
    try:
        runtime_stage.mkdir(parents=True, exist_ok=False)
        skill_stage.mkdir(parents=True, exist_ok=False)
        _write_stage_marker(runtime_stage, token=token, role="runtime", operation="install")
        _write_stage_marker(skill_stage, token=token, role="skill", operation="install")
        staged = _stage_artifact(artifact, runtime_stage, skill_stage)
        _promote(
            runtime_stage,
            runtime_path,
            expected_source_identity=_path_identity(runtime_stage),
        )
        runtime_promoted = True
        if reusable_raw_skill:
            _remove_stage_marker(skill_stage)
            _remove_tree(skill_stage)
        else:
            _promote(
                skill_stage,
                skill_path,
                expected_source_identity=_path_identity(skill_stage),
            )
            skill_promoted = True
        _remove_stage_marker(runtime_path)
        _remove_stage_marker(skill_path)
        installation = PackageInstallation(
            **{
                **staged.__dict__,
                "runtime_path": runtime_path,
                "skill_path": skill_path,
            }
        )
        readback = _read_installation(runtime_path, skill_path)
        if readback is None or not _record_matches_artifact(readback, artifact):
            raise RuntimeError("installed artifact failed exact readback")
        return LifecycleResult(LifecycleAction.INSTALLED, "installed exact package artifact", True, readback)
    except Exception as exc:
        if skill_promoted:
            _remove_tree(skill_path)
        if runtime_promoted:
            _remove_tree(runtime_path)
        _remove_tree(runtime_stage)
        _remove_tree(skill_stage)
        return LifecycleResult(LifecycleAction.FAILED, f"installation rolled back: {exc}", runtime_promoted or skill_promoted)


@_package_lifecycle_guard
def upgrade_artifact(
    artifact_root: Path,
    hermes_home: Path,
    local_appdata: Path,
    *,
    previous_version: str,
    previous_artifact_hash: str | None,
) -> LifecycleResult:
    """Replace the active skill only after proving the prior install is exact."""

    artifact = load_artifact(artifact_root)
    previous_runtime = _safe_target(
        _runtime_path(local_appdata, previous_version), "previous runtime target"
    )
    skill_path = _safe_target(_skill_path(hermes_home, artifact.name), "skill target")
    previous = _read_installation(previous_runtime, skill_path)
    if previous is None:
        return LifecycleResult(LifecycleAction.MISSING, "prior package installation is missing", False)
    if not _record_matches_hash(previous, previous_artifact_hash):
        return _conflict("prior package artifact hash does not match the installed manifest", previous)
    if not previous.skill_path.exists() or _tree_files(previous.skill_path) != previous.skill_files:
        return _conflict("prior installed skill tree has drifted", previous)
    if _skill_tree_hash(previous.skill_path, previous.skill_files) != previous.skill_tree_hash:
        return _conflict("prior installed skill content has drifted", previous)
    previous_artifact_root = previous.runtime_path / ARTIFACT_DIRECTORY
    if _runtime_artifact_files(previous_artifact_root) != previous.artifact_files:
        return _conflict("prior installed runtime artifact file set has drifted", previous)
    if _hash_files(previous_artifact_root, previous.artifact_files) != previous.artifact_hash:
        return _conflict("prior installed runtime artifact content has drifted", previous)
    if artifact.version == previous.version:
        return _conflict("upgrade requires a different package version", previous)

    next_runtime = _safe_target(
        _runtime_path(local_appdata, artifact.version), "runtime target"
    )
    if next_runtime.exists():
        return _conflict("target package version already exists; refusing to overwrite", previous)

    token = uuid.uuid4().hex
    runtime_stage = next_runtime.parent / f"{STAGING_PREFIX}{token}"
    skill_stage = skill_path.parent / f"{STAGING_PREFIX}{token}"
    skill_backup = skill_path.parent / f".concierge-backup-{token}"
    runtime_promoted = False
    old_skill_backed_up = False
    new_skill_promoted = False
    try:
        runtime_stage.mkdir(parents=True, exist_ok=False)
        skill_stage.mkdir(parents=True, exist_ok=False)
        _write_stage_marker(runtime_stage, token=token, role="runtime", operation="upgrade")
        _write_stage_marker(skill_stage, token=token, role="skill", operation="upgrade")
        staged = _stage_artifact(artifact, runtime_stage, skill_stage)
        _promote(
            runtime_stage,
            next_runtime,
            expected_source_identity=_path_identity(runtime_stage),
        )
        runtime_promoted = True
        _promote(
            skill_path,
            skill_backup,
            expected_source_identity=_path_identity(skill_path),
        )
        old_skill_backed_up = True
        _promote(
            skill_stage,
            skill_path,
            expected_source_identity=_path_identity(skill_stage),
        )
        new_skill_promoted = True
        _remove_stage_marker(next_runtime)
        _remove_stage_marker(skill_path)
        readback = _read_installation(next_runtime, skill_path)
        if readback is None or not _record_matches_artifact(readback, artifact):
            raise RuntimeError("upgraded artifact failed exact readback")
        _remove_tree(skill_backup)
        return LifecycleResult(LifecycleAction.UPDATED, "updated exact package installation", True, readback)
    except Exception as exc:
        if new_skill_promoted:
            _remove_tree(skill_path)
        if old_skill_backed_up and skill_backup.exists():
            _promote(skill_backup, skill_path)
        if runtime_promoted:
            _remove_tree(next_runtime)
        _remove_tree(runtime_stage)
        _remove_tree(skill_stage)
        _remove_tree(skill_backup)
        return LifecycleResult(LifecycleAction.FAILED, f"upgrade rolled back: {exc}", runtime_promoted or new_skill_promoted, previous)


@_package_lifecycle_guard
def uninstall_artifact(
    hermes_home: Path,
    local_appdata: Path,
    *,
    version: str,
    expected_artifact_hash: str | None,
) -> LifecycleResult:
    """Remove only an exact package installation; never remove user data."""

    runtime_path = _safe_target(
        _runtime_path(local_appdata, version), "runtime target"
    )
    skill_path = _safe_target(_skill_path(hermes_home, "concierge"), "skill target")
    if not runtime_path.exists() and not skill_path.exists():
        return LifecycleResult(LifecycleAction.NOOP, "exact package installation is absent", False)
    installation = _read_installation(runtime_path, skill_path)
    if installation is None:
        return _conflict("package runtime is present without a readable ownership manifest")
    if not _record_matches_hash(installation, expected_artifact_hash):
        return _conflict("installed artifact hash does not match the requested uninstall", installation)
    if _tree_files(skill_path) != installation.skill_files or _tree_hash(skill_path) != installation.skill_tree_hash:
        return _conflict("installed skill tree has drifted; refusing to delete user changes", installation)
    artifact_root = runtime_path / ARTIFACT_DIRECTORY
    if (
        _runtime_artifact_files(artifact_root) != installation.artifact_files
        or _hash_files(artifact_root, installation.artifact_files) != installation.artifact_hash
    ):
        return _conflict("installed runtime artifact has drifted; refusing to delete user changes", installation)

    try:
        _remove_tree(skill_path)
        _remove_tree(runtime_path)
    except OSError as exc:
        return LifecycleResult(LifecycleAction.FAILED, f"uninstall failed after mutation: {exc}", True, installation)
    if skill_path.exists() or runtime_path.exists():
        return LifecycleResult(LifecycleAction.FAILED, "uninstall failed exact absence readback", True, installation)
    return LifecycleResult(LifecycleAction.REMOVED, "removed exact package-owned files", True, installation)


@_package_lifecycle_guard
def recover_interrupted_install(hermes_home: Path, local_appdata: Path) -> LifecycleResult:
    """Clean only package-owned staging directories left by an interrupted run."""

    roots = (
        _skill_path(hermes_home, "concierge").parent,
        _runtime_path(local_appdata, "0").parent,
    )
    cleaned: list[Path] = []
    for root in roots:
        _assert_no_symlink_component(root, "recovery root")
        if not root.is_dir():
            continue
        for candidate in root.iterdir():
            if not candidate.name.startswith(STAGING_PREFIX):
                continue
            _assert_no_symlink_component(candidate, "staging candidate")
            if candidate.is_dir() and _is_owned_stage(candidate):
                _assert_no_symlink_component(candidate, "staging candidate")
                if not _is_owned_stage(candidate):
                    raise PackageArtifactError(
                        "staging candidate ownership changed before recovery"
                    )
                _remove_tree(candidate, require_owned_stage=True)
                cleaned.append(candidate)
    if not cleaned:
        return LifecycleResult(LifecycleAction.NOOP, "no package-owned interrupted staging was present", False)
    return LifecycleResult(
        LifecycleAction.RECOVERED,
        "removed package-owned interrupted staging directories",
        True,
        cleaned_paths=tuple(cleaned),
    )
