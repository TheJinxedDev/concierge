"""Pure Concierge setup and consent decisions for the semantic-beta boundary.

This module resolves paths and describes the selected beta setup policy without
creating directories, writing configuration, scheduling jobs, or enabling
capture.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
import os
from pathlib import Path

from .capture_boundary import CaptureMode


CONCIERGE_DATA_DIR_ENV = "CONCIERGE_DATA_DIR"
LOCALAPPDATA_ENV = "LOCALAPPDATA"
COMPATIBILITY_DATA_DIRECTORY_NAME = "taste-database"


class DeliveryTarget(str, Enum):
    """Where a future package-owned capture run may deliver its report."""

    LOCAL = "local"
    ORIGIN = "origin"


class BacklogPolicy(str, Enum):
    """Whether scheduled capture may inspect sessions completed before first run."""

    PROCESS_EXISTING = "process_existing"
    START_FRESH = "start_fresh"


class BrowserScope(str, Enum):
    """Whether the local browser surface belongs to the beta package."""

    INCLUDED = "included"
    EXCLUDED = "excluded"


@dataclass(frozen=True)
class WeeklySchedule:
    """A non-live schedule description for the future capture job."""

    cadence: str = "weekly"
    weekday: str = "sunday"
    local_time: str = "04:00"
    timezone_policy: str = "host_local"
    catch_up: bool = False


@dataclass(frozen=True)
class SetupPolicy:
    """The selected setup boundary without performing any setup action."""

    delivery_target: DeliveryTarget
    browser_scope: BrowserScope
    schedule: WeeklySchedule
    capture_default_mode: CaptureMode
    backlog_policy_choice_required: bool
    backlog_default: BacklogPolicy | None
    explicit_capture_consent_required: bool
    create_capture_job_by_default: bool
    package_owns_runtime: bool
    runtime_relative_path: Path
    capture_requires_browser: bool


def resolve_data_directory(
    environ: Mapping[str, str] | None = None,
    *,
    home: Path | None = None,
    platform: str | None = None,
) -> Path:
    """Resolve the data directory without touching the filesystem.

    A nonblank ``CONCIERGE_DATA_DIR`` is an exact, absolute override. An
    invalid explicit value fails closed rather than silently falling back to a
    different directory. Without that override, Windows honors an absolute
    ``LOCALAPPDATA`` value and otherwise uses the conventional compatibility
    path. The POSIX fallback remains available for development and tests.
    """

    values = os.environ if environ is None else environ
    selected_platform = os.name if platform is None else platform

    explicit_value = values.get(CONCIERGE_DATA_DIR_ENV)
    if explicit_value is not None and explicit_value.strip():
        explicit_path = Path(explicit_value.strip())
        if not explicit_path.is_absolute():
            raise ValueError("CONCIERGE_DATA_DIR must be an absolute path")
        return explicit_path

    local_appdata = values.get(LOCALAPPDATA_ENV)
    if local_appdata:
        local_appdata_path = Path(local_appdata)
        if local_appdata_path.is_absolute():
            return local_appdata_path / COMPATIBILITY_DATA_DIRECTORY_NAME

    home_path = Path.home() if home is None else home
    if selected_platform == "nt":
        return home_path / "AppData" / "Local" / COMPATIBILITY_DATA_DIRECTORY_NAME
    return home_path / ".local" / "share" / COMPATIBILITY_DATA_DIRECTORY_NAME


def default_setup_policy() -> SetupPolicy:
    """Return the ratified non-live semantic-beta setup policy."""

    return SetupPolicy(
        delivery_target=DeliveryTarget.LOCAL,
        browser_scope=BrowserScope.INCLUDED,
        schedule=WeeklySchedule(),
        capture_default_mode=CaptureMode.OFF,
        backlog_policy_choice_required=True,
        backlog_default=None,
        explicit_capture_consent_required=True,
        create_capture_job_by_default=False,
        package_owns_runtime=True,
        runtime_relative_path=Path("Concierge") / "packages" / "<version>",
        capture_requires_browser=False,
    )
