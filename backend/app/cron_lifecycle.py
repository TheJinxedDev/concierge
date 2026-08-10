"""Fail-closed lifecycle operations for the package-owned Concierge cron job.

The module keeps enablement evidence separate from scheduler mutation.  It
supports an injected/real Hermes cron store, but never opens the application
database and never infers consent from a missing or similar job record.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from functools import wraps
from pathlib import Path
from typing import Protocol

from .capture_boundary import CaptureMode
from .capture_enablement import CaptureEnablementState, CaptureEnablementStore
from .capture_state import RunTerminalStatus
from .cron_identity import (
    CronOrigin,
    JobOwnership,
    PackageOwnedJobSpec,
    classify_job_record,
)
from .file_lock import exclusive_file_lock
from .setup_contract import BacklogPolicy, DeliveryTarget


class LifecycleAction(str, Enum):
    """Observable package-owned lifecycle outcomes."""

    DISABLED = "disabled"
    CREATED = "created"
    NOOP = "noop"
    CONFLICT = "conflict"
    UPDATED = "updated"
    REMOVED = "removed"
    MISSING = "missing"
    FAILED = "failed"


@dataclass(frozen=True)
class LifecycleResult:
    action: str
    reason: str
    job: dict | None = None
    mutated: bool = False


class CronStore(Protocol):
    def list_jobs(self) -> list[dict]: ...

    def create(self, spec: PackageOwnedJobSpec, origin: CronOrigin | None) -> dict: ...

    def update(
        self,
        job_id: str,
        spec: PackageOwnedJobSpec,
        origin: CronOrigin | None,
    ) -> dict | None: ...

    def remove(self, job_id: str) -> bool: ...

    def mutation_guard(self): ...


class HermesCronStore:
    """Adapter over Hermes' real cron store, scoped to one Hermes home."""

    def __init__(self, home: Path) -> None:
        self.home = Path(home).expanduser().resolve()

    def mutation_guard(self):
        return exclusive_file_lock(self.home / "cron" / ".concierge-package-lifecycle.lock")

    def list_jobs(self) -> list[dict]:
        from cron.jobs import list_jobs, use_cron_store

        with use_cron_store(self.home):
            return list_jobs(include_disabled=True)

    def create(self, spec: PackageOwnedJobSpec, origin: CronOrigin | None) -> dict:
        from cron.jobs import create_job, use_cron_store

        with use_cron_store(self.home):
            return create_job(
                prompt=spec.prompt,
                schedule=spec.schedule.expression,
                name=spec.name,
                deliver=spec.delivery_target.value,
                origin=origin.as_mapping() if origin is not None else None,
                skills=list(spec.skills),
            )

    def update(
        self,
        job_id: str,
        spec: PackageOwnedJobSpec,
        origin: CronOrigin | None,
    ) -> dict | None:
        from cron.jobs import update_job, use_cron_store

        updates = {
            "prompt": spec.prompt,
            "skills": list(spec.skills),
            "skill": spec.skills[0] if spec.skills else None,
            "schedule": spec.schedule.expression,
            "deliver": spec.delivery_target.value,
            "origin": origin.as_mapping() if origin is not None else None,
        }
        with use_cron_store(self.home):
            return update_job(job_id, updates)

    def remove(self, job_id: str) -> bool:
        from cron.jobs import remove_job, use_cron_store

        with use_cron_store(self.home):
            return remove_job(job_id)


def _require_scheduler_guard(method):
    @wraps(method)
    def guarded(store, *args, **kwargs):
        enablement = None
        enablement_store = kwargs.get("enablement_store")
        if method.__name__ in {"ensure_package_owned_job", "update_package_owned_job"}:
            enablement = kwargs.get("enablement")
            if enablement is None:
                enablement_index = 1 if method.__name__ == "ensure_package_owned_job" else 2
                enablement = args[enablement_index] if len(args) > enablement_index else None
            if enablement is not None and not enablement.is_enabled:
                return method(store, *args, **kwargs)
            if enablement is not None and enablement_store is None:
                return LifecycleResult(
                    action=LifecycleAction.CONFLICT,
                    reason="enabled scheduler mutation requires a durable enablement ledger",
                )
        guard_factory = getattr(store, "mutation_guard", None)
        if not callable(guard_factory):
            return LifecycleResult(
                action=LifecycleAction.CONFLICT,
                reason="scheduler store lacks an atomic lifecycle coordination guard",
            )
        with guard_factory():
            if enablement is not None and enablement.is_enabled:
                with enablement_store.mutation_guard():
                    durable = enablement_store.read()
                    if durable != enablement:
                        return LifecycleResult(
                            action=LifecycleAction.CONFLICT,
                            reason="durable enablement ledger changed since the supplied snapshot",
                        )
                    return method(store, *args, **kwargs)
            return method(store, *args, **kwargs)

    return guarded


@_require_scheduler_guard
def ensure_package_owned_job(
    store: CronStore,
    spec: PackageOwnedJobSpec,
    enablement: CaptureEnablementState,
    *,
    origin: CronOrigin | None = None,
    enablement_store: CaptureEnablementStore | None = None,
) -> LifecycleResult:
    """Create the exact job once, but only after explicit enablement."""

    scope_error = _enablement_scope_error(spec, enablement, origin)
    if scope_error is not None:
        action = (
            LifecycleAction.DISABLED
            if not enablement.is_enabled
            else LifecycleAction.CONFLICT
        )
        return LifecycleResult(action=action, reason=scope_error)

    matches = _same_name_jobs(store.list_jobs(), spec.name)
    if len(matches) > 1:
        return LifecycleResult(
            action=LifecycleAction.CONFLICT,
            reason="multiple same-name jobs exist; refusing to choose one",
        )
    if matches:
        job = matches[0]
        ownership = classify_job_record(job, spec)
        if ownership is JobOwnership.EXACT:
            return LifecycleResult(
                action=LifecycleAction.NOOP,
                reason="exact package-owned job already exists",
                job=job,
            )
        return LifecycleResult(
            action=LifecycleAction.CONFLICT,
            reason=f"same-name job classified as {ownership.value}; refusing overwrite",
            job=job,
        )

    created = store.create(spec, origin)
    readback = _find_job_by_id(store.list_jobs(), created.get("id"))
    if readback is None or classify_job_record(readback, spec) is not JobOwnership.EXACT:
        return LifecycleResult(
            action=LifecycleAction.FAILED,
            reason="created job did not pass exact owned readback",
            job=readback or created,
            mutated=True,
        )
    return LifecycleResult(
        action=LifecycleAction.CREATED,
        reason="created exact package-owned job",
        job=readback,
        mutated=True,
    )


@_require_scheduler_guard
def update_package_owned_job(
    store: CronStore,
    previous_spec: PackageOwnedJobSpec,
    next_spec: PackageOwnedJobSpec,
    enablement: CaptureEnablementState,
    *,
    origin: CronOrigin | None = None,
    enablement_store: CaptureEnablementStore | None = None,
) -> LifecycleResult:
    """Update only a stable-ID record proven exact against the prior spec."""

    scope_error = _enablement_scope_error(next_spec, enablement, origin)
    if scope_error is not None:
        action = (
            LifecycleAction.DISABLED
            if not enablement.is_enabled
            else LifecycleAction.CONFLICT
        )
        return LifecycleResult(action=action, reason=scope_error)
    if next_spec.name != previous_spec.name:
        return LifecycleResult(
            action=LifecycleAction.CONFLICT,
            reason="package-owned update cannot change the stable job name",
        )

    matches = _same_name_jobs(store.list_jobs(), previous_spec.name)
    if not matches:
        return LifecycleResult(
            action=LifecycleAction.MISSING,
            reason="no prior package-owned job exists",
        )
    if len(matches) > 1:
        return LifecycleResult(
            action=LifecycleAction.CONFLICT,
            reason="multiple same-name jobs exist; refusing to choose one",
        )

    prior = matches[0]
    if classify_job_record(prior, previous_spec) is not JobOwnership.EXACT:
        return LifecycleResult(
            action=LifecycleAction.CONFLICT,
            reason="prior job is not exact against the installed package spec",
            job=prior,
        )

    updated = store.update(prior["id"], next_spec, origin)
    readback = _find_job_by_id(store.list_jobs(), prior["id"])
    if updated is None or readback is None:
        return LifecycleResult(
            action=LifecycleAction.FAILED,
            reason="scheduler update did not produce a readable stable-ID record",
            job=readback or updated,
            mutated=True,
        )
    if classify_job_record(readback, next_spec) is not JobOwnership.EXACT:
        return LifecycleResult(
            action=LifecycleAction.FAILED,
            reason="updated job failed exact package-owned readback",
            job=readback,
            mutated=True,
        )
    return LifecycleResult(
        action=LifecycleAction.UPDATED,
        reason="updated the exact stable-ID package-owned job",
        job=readback,
        mutated=True,
    )


@_require_scheduler_guard
def uninstall_package_owned_job(
    store: CronStore,
    spec: PackageOwnedJobSpec,
) -> LifecycleResult:
    """Remove only an exact owned record; leave collisions untouched."""

    matches = _same_name_jobs(store.list_jobs(), spec.name)
    if not matches:
        return LifecycleResult(
            action=LifecycleAction.NOOP,
            reason="no package-owned job exists",
        )
    if len(matches) > 1:
        return LifecycleResult(
            action=LifecycleAction.CONFLICT,
            reason="multiple same-name jobs exist; refusing to choose one",
        )

    job = matches[0]
    if classify_job_record(job, spec) is not JobOwnership.EXACT:
        return LifecycleResult(
            action=LifecycleAction.CONFLICT,
            reason="job is not exact against the installed package spec",
            job=job,
        )
    if not store.remove(job["id"]):
        return LifecycleResult(
            action=LifecycleAction.FAILED,
            reason="scheduler refused to remove the exact stable-ID job",
            job=job,
            mutated=False,
        )
    if _find_job_by_id(store.list_jobs(), job["id"]) is not None:
        return LifecycleResult(
            action=LifecycleAction.FAILED,
            reason="removed job remained present on readback",
            job=job,
            mutated=True,
        )
    return LifecycleResult(
        action=LifecycleAction.REMOVED,
        reason="removed the exact stable-ID package-owned job",
        job=job,
        mutated=True,
    )


def retire_completed_manual_backlog_job(
    store: CronStore,
    spec: PackageOwnedJobSpec,
    *,
    terminal_status: RunTerminalStatus | str,
    backlog_remaining: bool,
    retryable_claim_ids: tuple[str, ...],
    canonical_media_changed: bool,
    verified_readback: bool,
) -> LifecycleResult:
    """Remove one exact manual backlog job after a verified terminal catch-up.

    This helper is intentionally stricter than ordinary uninstall.  The caller
    must supply the worker's final report/state/proposal readback result; a
    partial, blocked, failed, or uncertain run leaves the one-shot job in place
    for a safe retry.
    """

    if (
        spec.capture_mode is not CaptureMode.OFF
        or spec.backlog_policy is not BacklogPolicy.PROCESS_EXISTING
    ):
        return LifecycleResult(
            action=LifecycleAction.NOOP,
            reason="only an off process_existing job has a bounded manual backlog lifetime",
        )
    if not verified_readback:
        return LifecycleResult(
            action=LifecycleAction.NOOP,
            reason="manual backlog catch-up was not fully read back; retaining the job",
        )
    try:
        status = RunTerminalStatus(terminal_status)
    except (TypeError, ValueError):
        return LifecycleResult(
            action=LifecycleAction.NOOP,
            reason="manual backlog catch-up has an unknown terminal status; retaining the job",
        )
    if status not in {
        RunTerminalStatus.COMPLETE,
        RunTerminalStatus.NO_VISIBLE_EVIDENCE,
    }:
        return LifecycleResult(
            action=LifecycleAction.NOOP,
            reason=f"manual backlog catch-up is {status.value}; retaining the job",
        )
    if backlog_remaining:
        return LifecycleResult(
            action=LifecycleAction.NOOP,
            reason="manual backlog still has eligible work; retaining the job",
        )
    if retryable_claim_ids:
        return LifecycleResult(
            action=LifecycleAction.NOOP,
            reason="manual backlog has retryable or blocked claims; retaining the job",
        )
    if canonical_media_changed:
        return LifecycleResult(
            action=LifecycleAction.NOOP,
            reason="manual backlog reported canonical media mutation; retaining the job",
        )
    return uninstall_package_owned_job(store, spec)


def _enablement_scope_error(
    spec: PackageOwnedJobSpec,
    enablement: CaptureEnablementState,
    origin: CronOrigin | None,
) -> str | None:
    if not enablement.is_enabled:
        return "capture enablement is off; scheduler creation is disabled"
    decision = enablement.current_decision
    assert decision is not None
    if decision.delivery_target is not spec.delivery_target:
        return "job delivery does not match the durable enablement decision"
    if decision.schedule != spec.schedule:
        return "job schedule does not match the durable enablement decision"
    if decision.mode is CaptureMode.FULL_AUTO:
        return "fully_auto capture mode remains deferred and cannot create a package job"
    if decision.mode is not spec.capture_mode:
        return "job capture mode does not match the durable enablement decision"
    if decision.backlog_policy is not spec.backlog_policy:
        return "job backlog policy does not match the durable enablement decision"
    if (
        decision.mode is CaptureMode.OFF
        and decision.backlog_policy is not BacklogPolicy.PROCESS_EXISTING
    ):
        return "off capture mode requires a process_existing backlog policy"
    if spec.delivery_target is DeliveryTarget.ORIGIN and origin is None:
        return "origin delivery requires explicit origin routing context"
    if spec.delivery_target is DeliveryTarget.ORIGIN and decision.origin is None:
        return "durable enablement decision has no bound origin routing context"
    if spec.delivery_target is DeliveryTarget.ORIGIN and decision.origin != origin:
        return "caller origin does not match the durable enablement origin"
    if spec.origin != origin:
        return "job destination does not match the package-owned origin identity"
    return None


def _same_name_jobs(jobs: list[dict], name: str) -> list[dict]:
    return [job for job in jobs if job.get("name") == name]


def _find_job_by_id(jobs: list[dict], job_id: object) -> dict | None:
    if not isinstance(job_id, str):
        return None
    return next((job for job in jobs if job.get("id") == job_id), None)
