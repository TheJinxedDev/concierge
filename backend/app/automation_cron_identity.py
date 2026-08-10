"""Deterministic identities for the three independent Concierge automation jobs."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol

from .automation_preferences import AutomationPreferences
from .file_lock import exclusive_file_lock
from .setup_contract import DeliveryTarget


class AutomationJobKind(str, Enum):
    BACKLOG = "backlog"
    RECENT_CAPTURE = "recent_capture"
    PROMOTION = "promotion"


@dataclass(frozen=True)
class AutomationJobSpec:
    kind: AutomationJobKind
    name: str
    owner_marker: str
    schedule: str
    prompt: str
    fingerprint: str
    skills: tuple[str, ...] = ("concierge",)
    delivery_target: DeliveryTarget = DeliveryTarget.LOCAL
    origin: dict[str, str] | None = None


def _fingerprint(payload: object) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _job(
    kind: AutomationJobKind,
    name: str,
    schedule: str,
    body: str,
) -> AutomationJobSpec:
    owner_marker = f"concierge/automation/{kind.value}"
    metadata = "\n".join(
        (
            f"CONCIERGE_JOB_OWNER={owner_marker}",
            f"CONCIERGE_JOB_NAME={name}",
            f"CONCIERGE_JOB_KIND={kind.value}",
            f"CONCIERGE_SCHEDULE={schedule}",
            "CONCIERGE_SCHEDULE_TIMEZONE=host_local",
            "CONCIERGE_SCHEDULE_CATCH_UP=false",
            "CONCIERGE_JOB_METADATA_END",
        )
    )
    prompt_body = f"{metadata}\n\n{body.strip()}\n"
    return AutomationJobSpec(
        kind=kind,
        name=name,
        owner_marker=owner_marker,
        schedule=schedule,
        prompt=prompt_body,
        fingerprint=_fingerprint(
            {
                "kind": kind.value,
                "name": name,
                "owner_marker": owner_marker,
                "schedule": schedule,
                "prompt": prompt_body,
            }
        ),
    )


class AutomationJobOwnership(str, Enum):
    EXACT = "exact_owned"
    FINGERPRINT_CONFLICT = "owned_name_fingerprint_conflict"
    NAME_COLLISION = "same_name_unowned"
    UNRELATED = "unrelated"


class AutomationLifecycleAction(str, Enum):
    NOOP = "noop"
    CREATED = "created"
    REMOVED = "removed"
    CONFLICT = "conflict"
    FAILED = "failed"


@dataclass(frozen=True)
class AutomationLifecycleResult:
    action: AutomationLifecycleAction
    reason: str
    job: dict | None = None
    mutated: bool = False


class AutomationCronStore(Protocol):
    def list_jobs(self) -> list[dict]: ...

    def create(self, spec: AutomationJobSpec) -> dict: ...

    def remove(self, job_id: str) -> bool: ...

    def mutation_guard(self): ...


class HermesAutomationCronStore:
    """Minimal real-Hermes adapter for the independent automation jobs."""

    def __init__(self, home: Path) -> None:
        self.home = Path(home).expanduser().resolve()

    def mutation_guard(self):
        return exclusive_file_lock(self.home / "cron" / ".concierge-automation.lock")

    def list_jobs(self) -> list[dict]:
        from cron.jobs import list_jobs, use_cron_store

        with use_cron_store(self.home):
            return list_jobs(include_disabled=True)

    def create(self, spec: AutomationJobSpec) -> dict:
        from cron.jobs import create_job, use_cron_store

        with use_cron_store(self.home):
            return create_job(
                prompt=spec.prompt,
                schedule=spec.schedule,
                name=spec.name,
                deliver=spec.delivery_target.value,
                origin=spec.origin,
                skills=list(spec.skills),
            )

    def remove(self, job_id: str) -> bool:
        from cron.jobs import remove_job, use_cron_store

        with use_cron_store(self.home):
            return remove_job(job_id)


def _prompt_metadata(prompt: object) -> dict[str, str] | None:
    if not isinstance(prompt, str) or "\n\n" not in prompt:
        return None
    header, _body = prompt.split("\n\n", 1)
    lines = header.splitlines()
    if not lines or lines[-1] != "CONCIERGE_JOB_METADATA_END":
        return None
    metadata: dict[str, str] = {}
    for line in lines[:-1]:
        key, separator, value = line.partition("=")
        if not separator or not key or key in metadata:
            return None
        metadata[key] = value
    return metadata


def _record_skills(record: Mapping[str, object]) -> tuple[str, ...]:
    skills = record.get("skills")
    if isinstance(skills, str):
        return (skills,)
    if isinstance(skills, (list, tuple)):
        return tuple(str(skill) for skill in skills)
    return ()


def _record_schedule(record: Mapping[str, object]) -> str | None:
    schedule = record.get("schedule")
    if isinstance(schedule, Mapping):
        if schedule.get("kind") != "cron":
            return None
        expression = schedule.get("expr")
        return expression.strip() if isinstance(expression, str) else None
    return schedule.strip() if isinstance(schedule, str) else None


def classify_automation_job_record(
    record: Mapping[str, object],
    spec: AutomationJobSpec,
) -> AutomationJobOwnership:
    """Classify a scheduler record without adopting or mutating it."""
    if record.get("name") != spec.name:
        return AutomationJobOwnership.UNRELATED
    metadata = _prompt_metadata(record.get("prompt"))
    if metadata is None:
        return AutomationJobOwnership.NAME_COLLISION
    if (
        metadata.get("CONCIERGE_JOB_OWNER") != spec.owner_marker
        or metadata.get("CONCIERGE_JOB_NAME") != spec.name
        or metadata.get("CONCIERGE_JOB_KIND") != spec.kind.value
    ):
        return AutomationJobOwnership.NAME_COLLISION
    if (
        record.get("prompt") != spec.prompt
        or _record_skills(record) != spec.skills
        or _record_schedule(record) != spec.schedule
        or record.get("deliver") != spec.delivery_target.value
        or record.get("origin") != spec.origin
    ):
        return AutomationJobOwnership.FINGERPRINT_CONFLICT
    if spec.fingerprint != _fingerprint(
        {
            "kind": spec.kind.value,
            "name": spec.name,
            "owner_marker": spec.owner_marker,
            "schedule": spec.schedule,
            "prompt": spec.prompt,
        }
    ):
        return AutomationJobOwnership.FINGERPRINT_CONFLICT
    return AutomationJobOwnership.EXACT


def reconcile_automation_jobs(
    store: AutomationCronStore,
    preferences: AutomationPreferences,
    *,
    schedule: str = "0 4 * * 0",
    runtime_root: Path | None = None,
    data_directory: Path | None = None,
    hermes_home: Path | None = None,
) -> tuple[AutomationLifecycleResult, ...]:
    """Create/remove only the exact jobs implied by durable preferences."""
    guard_factory = getattr(store, "mutation_guard", None)
    if not callable(guard_factory):
        return (
            AutomationLifecycleResult(
                AutomationLifecycleAction.CONFLICT,
                "scheduler store lacks an atomic lifecycle coordination guard",
            ),
        )
    desired = {
        spec.name: spec
        for spec in build_automation_job_specs(
            preferences,
            schedule=schedule,
            runtime_root=runtime_root,
            data_directory=data_directory,
            hermes_home=hermes_home,
        )
    }
    all_specs = {
        spec.name: spec
        for spec in build_automation_job_specs(
            AutomationPreferences(
                decision_id="known-automation-job-names",
                decided_at="known",
                backlog_cron_enabled=True,
                recent_capture_cron_enabled=True,
                promotion_cron_enabled=True,
            ),
            schedule=schedule,
            runtime_root=runtime_root,
            data_directory=data_directory,
            hermes_home=hermes_home,
        )
    }
    results: list[AutomationLifecycleResult] = []
    with guard_factory():
        jobs = store.list_jobs()
        for name, spec in all_specs.items():
            matches = [job for job in jobs if job.get("name") == name]
            if len(matches) > 1:
                results.append(
                    AutomationLifecycleResult(
                        AutomationLifecycleAction.CONFLICT,
                        "multiple same-name jobs exist; refusing to choose one",
                    )
                )
                continue
            match = matches[0] if matches else None
            desired_spec = desired.get(name)
            if desired_spec is not None:
                if match is not None:
                    ownership = classify_automation_job_record(match, desired_spec)
                    if ownership is AutomationJobOwnership.EXACT:
                        results.append(
                            AutomationLifecycleResult(
                                AutomationLifecycleAction.NOOP,
                                "exact automation job already exists",
                                job=match,
                            )
                        )
                    else:
                        results.append(
                            AutomationLifecycleResult(
                                AutomationLifecycleAction.CONFLICT,
                                f"same-name job classified as {ownership.value}; refusing overwrite",
                                job=match,
                            )
                        )
                    continue
                created = store.create(desired_spec)
                readback = next(
                    (job for job in store.list_jobs() if job.get("id") == created.get("id")),
                    None,
                )
                if readback is None or classify_automation_job_record(readback, desired_spec) is not AutomationJobOwnership.EXACT:
                    results.append(
                        AutomationLifecycleResult(
                            AutomationLifecycleAction.FAILED,
                            "created automation job failed exact readback",
                            job=readback or created,
                            mutated=True,
                        )
                    )
                else:
                    jobs = store.list_jobs()
                    results.append(
                        AutomationLifecycleResult(
                            AutomationLifecycleAction.CREATED,
                            "created exact automation job",
                            job=readback,
                            mutated=True,
                        )
                    )
                continue
            if match is None:
                results.append(
                    AutomationLifecycleResult(
                        AutomationLifecycleAction.NOOP,
                        "automation job is not enabled and is absent",
                    )
                )
                continue
            ownership = classify_automation_job_record(match, spec)
            if ownership is not AutomationJobOwnership.EXACT:
                results.append(
                    AutomationLifecycleResult(
                        AutomationLifecycleAction.CONFLICT,
                        f"disabled same-name job classified as {ownership.value}; refusing removal",
                        job=match,
                    )
                )
                continue
            removed = store.remove(match["id"])
            remaining = [job for job in store.list_jobs() if job.get("id") == match["id"]]
            results.append(
                AutomationLifecycleResult(
                    AutomationLifecycleAction.REMOVED if removed and not remaining else AutomationLifecycleAction.FAILED,
                    "removed exact disabled automation job" if removed and not remaining else "disabled automation job remained after removal",
                    job=match,
                    mutated=removed,
                )
            )
    return tuple(results)


def retire_automation_job(
    store: AutomationCronStore,
    spec: AutomationJobSpec,
) -> AutomationLifecycleResult:
    """Remove one exact automation job after its bounded run is verified."""

    guard_factory = getattr(store, "mutation_guard", None)
    if not callable(guard_factory):
        return AutomationLifecycleResult(
            AutomationLifecycleAction.CONFLICT,
            "scheduler store lacks an atomic lifecycle coordination guard",
        )
    with guard_factory():
        matches = [job for job in store.list_jobs() if job.get("name") == spec.name]
        if not matches:
            return AutomationLifecycleResult(
                AutomationLifecycleAction.NOOP,
                "exact automation job is already absent",
            )
        if len(matches) > 1:
            return AutomationLifecycleResult(
                AutomationLifecycleAction.CONFLICT,
                "multiple same-name jobs exist; refusing to choose one",
            )
        match = matches[0]
        ownership = classify_automation_job_record(match, spec)
        if ownership is not AutomationJobOwnership.EXACT:
            return AutomationLifecycleResult(
                AutomationLifecycleAction.CONFLICT,
                f"backlog job classified as {ownership.value}; refusing removal",
                job=match,
            )
        removed = store.remove(match["id"])
        remaining = [job for job in store.list_jobs() if job.get("id") == match["id"]]
        if not removed or remaining:
            return AutomationLifecycleResult(
                AutomationLifecycleAction.FAILED,
                "exact automation job remained after retirement",
                job=match,
                mutated=removed,
            )
        return AutomationLifecycleResult(
            AutomationLifecycleAction.REMOVED,
            "retired exact finite backlog automation job",
            job=match,
            mutated=True,
        )


def build_automation_job_specs(
    preferences: AutomationPreferences,
    *,
    schedule: str = "0 4 * * 0",
    runtime_root: Path | None = None,
    data_directory: Path | None = None,
    hermes_home: Path | None = None,
) -> tuple[AutomationJobSpec, ...]:
    """Emit only the explicitly enabled, independently owned jobs."""
    if not schedule.strip():
        raise ValueError("automation schedule must not be blank")

    def runtime_details(script: str, *extra: str) -> str:
        if runtime_root is None or data_directory is None or hermes_home is None:
            return ""
        command = (
            f'uv run --project "{runtime_root}" python '
            f'"{runtime_root / "scripts" / script}" '
            f'--hermes-home "{hermes_home}" --data-dir "{data_directory}"'
        )
        if extra:
            command += " " + " ".join(extra)
        return (
            "\n\nPackage-owned runtime contract:\n"
            f"- runtime root: {runtime_root}\n"
            f"- Hermes home: {hermes_home}\n"
            f"- Concierge data directory: {data_directory}\n"
            f"- execute exactly: `{command}`\n"
            "A nonzero command result is a failed run; do not claim success "
            "from scheduler status alone."
        )

    jobs: list[AutomationJobSpec] = []
    if preferences.backlog_cron_enabled:
        jobs.append(
            _job(
                AutomationJobKind.BACKLOG,
                "concierge-backlog-capture",
                schedule,
                "Run the finite completed-session backlog capture pass. Process only "
                "the backlog boundary selected during onboarding, submit reviewable "
                "proposals, and retire this exact owned job after a verified terminal "
                "pass. Never promote or mutate canonical records."
                + runtime_details(
                    "run_automatic_capture.py",
                    "--backlog",
                    f'--schedule "{schedule}"',
                ),
            )
        )
    if preferences.recent_capture_cron_enabled:
        jobs.append(
            _job(
                AutomationJobKind.RECENT_CAPTURE,
                "concierge-session-capture",
                schedule,
                "Run the ongoing recent completed-session capture pass. Read only "
                "ended sessions after the durable watermark, submit reviewable "
                "proposals, and never promote or mutate canonical records. There is "
                "no passive active-session observer."
                + runtime_details(
                    "run_automatic_capture.py",
                    f'--schedule "{schedule}"',
                ),
            )
        )
    if preferences.promotion_cron_enabled:
        jobs.append(
            _job(
                AutomationJobKind.PROMOTION,
                "concierge-auto-promotion",
                schedule,
                "Run the fully automatic promotion pass over pending Concierge "
                "capture candidates. Apply only the documented beta confidence rubric "
                "and threshold; leave abstentions pending and report the decision "
                "without inventing or rewriting canonical records."
                + runtime_details("run_automatic_promotion.py"),
            )
        )
    return tuple(jobs)
