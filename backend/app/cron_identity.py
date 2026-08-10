"""Pure identity and fingerprint contract for the future Concierge cron job.

This module describes an owned job without importing Hermes' scheduler or
writing any cron/configuration state. Lifecycle operations belong to later P5
slices and must use this contract before touching a disposable cron store.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
from collections.abc import Mapping
from typing import Any

from .capture_boundary import CaptureMode
from .setup_contract import BacklogPolicy, DeliveryTarget


JOB_NAME = "concierge-session-capture"
OWNER_MARKER = "concierge/session-capture"
PACKAGE_MARKER = "concierge@0.1.16-dev"
PACKAGE_SKILLS = ("concierge",)


class JobOwnership(str, Enum):
    """Read-only classification used by later lifecycle operations."""

    EXACT = "exact_owned"
    FINGERPRINT_CONFLICT = "owned_name_fingerprint_conflict"
    NAME_COLLISION = "same_name_unowned"
    UNRELATED = "unrelated"


@dataclass(frozen=True)
class CaptureSchedule:
    """Exact non-live schedule policy carried by an owned-job fingerprint."""

    expression: str
    timezone_policy: str = "host_local"
    catch_up: bool = False

    def __post_init__(self) -> None:
        if not self.expression.strip():
            raise ValueError("capture schedule expression must not be blank")
        if self.timezone_policy != "host_local":
            raise ValueError("capture schedule must use host_local time")
        if self.catch_up:
            raise ValueError("capture schedule must not enable catch-up")


DEFAULT_CAPTURE_SCHEDULE = CaptureSchedule(expression="0 4 * * 0")


@dataclass(frozen=True)
class CronOrigin:
    """Exact destination routing metadata included in owned-job identity."""

    platform: str
    chat_id: str
    thread_id: str | None = None

    def __post_init__(self) -> None:
        if not self.platform.strip():
            raise ValueError("cron origin platform must not be blank")
        if not self.chat_id.strip():
            raise ValueError("cron origin chat_id must not be blank")
        if self.thread_id is not None and not self.thread_id.strip():
            raise ValueError("cron origin thread_id must not be blank")

    def as_mapping(self) -> dict[str, str]:
        result = {"platform": self.platform, "chat_id": self.chat_id}
        if self.thread_id is not None:
            result["thread_id"] = self.thread_id
        return result


DEFAULT_CAPTURE_PROMPT_BODY = """Run one bounded Concierge conversation-capture pass over completed sessions.

Use the `concierge` skill and the application-owned proposal boundary. Read only
completed source material after the package-owned cursor, preserve exact
session/message references and user wording, and process the bounded batch in
chronological order. Capture clear consumption/progress facts and direct
opinions as reviewable proposals; keep consumption separate from evaluation,
never invent dates or scores, and stop on ambiguity or source change.

Submit only `needs_review` proposals. Never accept, reject, promote, import,
archive, overwrite, or otherwise mutate canonical Concierge records. Verify
proposal readback, the cursor/action/report state, and the unchanged canonical
snapshot before reporting. If there is no visible evidence, return `[SILENT]`.
"""


@dataclass(frozen=True)
class PackageOwnedJobSpec:
    """Complete deterministic identity for one package-owned job record."""

    name: str
    owner_marker: str
    package_marker: str
    skills: tuple[str, ...]
    prompt: str
    schedule: CaptureSchedule
    delivery_target: DeliveryTarget
    origin: CronOrigin | None
    capture_mode: CaptureMode
    backlog_policy: BacklogPolicy
    prompt_fingerprint: str
    skill_fingerprint: str
    fingerprint: str


def _digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _render_prompt(
    *,
    package_marker: str,
    schedule: CaptureSchedule,
    delivery_target: DeliveryTarget,
    origin: CronOrigin | None,
    capture_mode: CaptureMode,
    backlog_policy: BacklogPolicy,
    prompt_fingerprint: str,
    fingerprint: str,
    prompt_body: str,
) -> str:
    header = "\n".join(
        (
            f"CONCIERGE_JOB_OWNER={OWNER_MARKER}",
            f"CONCIERGE_JOB_NAME={JOB_NAME}",
            f"CONCIERGE_PACKAGE={package_marker}",
            f"CONCIERGE_SCHEDULE={schedule.expression}",
            f"CONCIERGE_SCHEDULE_TIMEZONE={schedule.timezone_policy}",
            f"CONCIERGE_SCHEDULE_CATCH_UP={'true' if schedule.catch_up else 'false'}",
            f"CONCIERGE_DELIVERY={delivery_target.value}",
            f"CONCIERGE_ORIGIN={_canonical_json(origin.as_mapping() if origin else None)}",
            f"CONCIERGE_CAPTURE_MODE={capture_mode.value}",
            f"CONCIERGE_BACKLOG_POLICY={backlog_policy.value}",
            f"CONCIERGE_PROMPT_FINGERPRINT={prompt_fingerprint}",
            f"CONCIERGE_JOB_FINGERPRINT={fingerprint}",
            "CONCIERGE_JOB_METADATA_END",
        )
    )
    return f"{header}\n\n{prompt_body}"


def _prompt_metadata(prompt: Any) -> tuple[dict[str, str], str] | None:
    """Read the exact ownership header and return it with the body."""

    if not isinstance(prompt, str) or "\n\n" not in prompt:
        return None
    header, body = prompt.split("\n\n", 1)
    expected_keys = (
        "CONCIERGE_JOB_OWNER",
        "CONCIERGE_JOB_NAME",
        "CONCIERGE_PACKAGE",
        "CONCIERGE_SCHEDULE",
        "CONCIERGE_SCHEDULE_TIMEZONE",
        "CONCIERGE_SCHEDULE_CATCH_UP",
        "CONCIERGE_DELIVERY",
        "CONCIERGE_ORIGIN",
        "CONCIERGE_CAPTURE_MODE",
        "CONCIERGE_BACKLOG_POLICY",
        "CONCIERGE_PROMPT_FINGERPRINT",
        "CONCIERGE_JOB_FINGERPRINT",
    )
    lines = header.splitlines()
    if len(lines) != len(expected_keys) + 1 or lines[-1] != "CONCIERGE_JOB_METADATA_END":
        return None

    values: dict[str, str] = {}
    for line, key in zip(lines[:-1], expected_keys):
        prefix = f"{key}="
        if not line.startswith(prefix):
            return None
        values[key] = line[len(prefix):]
    return values, body


def _record_skills(record: Mapping[str, Any]) -> tuple[str, ...]:
    skills = record.get("skills")
    if isinstance(skills, str):
        return (skills,)
    if isinstance(skills, (list, tuple)):
        return tuple(str(item) for item in skills)
    skill = record.get("skill")
    return (str(skill),) if isinstance(skill, str) and skill else ()


def _record_schedule_expression(record: Mapping[str, Any]) -> str | None:
    schedule = record.get("schedule")
    if isinstance(schedule, Mapping):
        if schedule.get("kind") != "cron":
            return None
        expression = schedule.get("expr")
        return expression.strip() if isinstance(expression, str) else None
    return schedule.strip() if isinstance(schedule, str) else None


def _origin_payload(origin: CronOrigin | None) -> dict[str, str] | None:
    return origin.as_mapping() if origin is not None else None


def _record_matches_spec(record: Mapping[str, Any], spec: PackageOwnedJobSpec) -> bool:
    metadata_result = _prompt_metadata(record.get("prompt"))
    if metadata_result is None:
        return False
    metadata, body = metadata_result
    if record.get("name") != spec.name:
        return False
    if record.get("prompt") != spec.prompt:
        return False
    if _record_skills(record) != spec.skills:
        return False
    if _record_schedule_expression(record) != spec.schedule.expression:
        return False
    if record.get("deliver") != spec.delivery_target.value:
        return False
    if record.get("origin") != _origin_payload(spec.origin):
        return False
    return (
        metadata["CONCIERGE_JOB_OWNER"] == spec.owner_marker
        and metadata["CONCIERGE_JOB_NAME"] == spec.name
        and metadata["CONCIERGE_PACKAGE"] == spec.package_marker
        and metadata["CONCIERGE_SCHEDULE"] == spec.schedule.expression
        and metadata["CONCIERGE_SCHEDULE_TIMEZONE"] == spec.schedule.timezone_policy
        and metadata["CONCIERGE_SCHEDULE_CATCH_UP"] == "false"
        and metadata["CONCIERGE_DELIVERY"] == spec.delivery_target.value
        and metadata["CONCIERGE_CAPTURE_MODE"] == spec.capture_mode.value
        and metadata["CONCIERGE_BACKLOG_POLICY"] == spec.backlog_policy.value
        and metadata["CONCIERGE_ORIGIN"] == _canonical_json(_origin_payload(spec.origin))
        and metadata["CONCIERGE_PROMPT_FINGERPRINT"] == spec.prompt_fingerprint
        and metadata["CONCIERGE_PROMPT_FINGERPRINT"] == _digest(body)
        and metadata["CONCIERGE_JOB_FINGERPRINT"] == spec.fingerprint
    )


def classify_job_record(
    record: Mapping[str, Any],
    spec: PackageOwnedJobSpec,
) -> JobOwnership:
    """Classify a stored Hermes record without changing it.

    A same-name record without the exact owner header is deliberately not
    adopted. A record with the owner header but any changed fingerprint input
    is a conflict; later update code must not overwrite it implicitly.
    """

    if record.get("name") != spec.name:
        return JobOwnership.UNRELATED
    metadata_result = _prompt_metadata(record.get("prompt"))
    if metadata_result is None:
        return JobOwnership.NAME_COLLISION
    metadata, _ = metadata_result
    if (
        metadata.get("CONCIERGE_JOB_OWNER") != spec.owner_marker
        or metadata.get("CONCIERGE_JOB_NAME") != spec.name
    ):
        return JobOwnership.NAME_COLLISION
    if _record_matches_spec(record, spec):
        return JobOwnership.EXACT
    return JobOwnership.FINGERPRINT_CONFLICT


def _scoped_prompt_body(
    prompt_body: str,
    *,
    capture_mode: CaptureMode,
    backlog_policy: BacklogPolicy,
) -> str:
    if capture_mode is CaptureMode.OFF:
        scope = (
            "This is a backlog-only pass for a fully manual setup. Process only the "
            "completed-session backlog that existed at the first-run boundary; do "
            "not create ongoing capture or process sessions created after that boundary. "
            "After a verified terminal catch-up with no remaining backlog, the "
            "package lifecycle must remove this exact owned job; keep it for retry "
            "when the run is partial, blocked, failed, or uncertain."
        )
    elif backlog_policy is BacklogPolicy.PROCESS_EXISTING:
        scope = (
            "Process the eligible completed-session backlog from the first-run "
            "boundary, then continue from the durable watermark for later sessions."
        )
    else:
        scope = (
            "Start fresh at the first-run boundary. Do not process completed sessions "
            "that existed before it; process only later sessions after the durable watermark."
        )
    return f"{scope}\n\n{prompt_body.strip()}\n"


def build_package_owned_job_spec(
    *,
    schedule: CaptureSchedule,
    delivery_target: DeliveryTarget,
    origin: CronOrigin | None = None,
    capture_mode: CaptureMode = CaptureMode.PENDING_ONLY,
    backlog_policy: BacklogPolicy = BacklogPolicy.START_FRESH,
    prompt_body: str = DEFAULT_CAPTURE_PROMPT_BODY,
    package_marker: str = PACKAGE_MARKER,
) -> PackageOwnedJobSpec:
    """Build a non-live owned-job identity from explicit setup choices."""

    if not isinstance(delivery_target, DeliveryTarget):
        raise TypeError("delivery_target must be a DeliveryTarget")
    if not isinstance(capture_mode, CaptureMode):
        raise TypeError("capture_mode must be a CaptureMode")
    if not isinstance(backlog_policy, BacklogPolicy):
        raise TypeError("backlog_policy must be a BacklogPolicy")
    if capture_mode is CaptureMode.OFF and backlog_policy is BacklogPolicy.START_FRESH:
        raise ValueError(
            "an off capture mode requires process_existing for a backlog-only job"
        )
    if delivery_target is DeliveryTarget.ORIGIN and origin is None:
        raise ValueError("origin delivery requires explicit origin routing context")
    if delivery_target is DeliveryTarget.LOCAL and origin is not None:
        raise ValueError("local delivery cannot carry origin routing context")
    if not isinstance(prompt_body, str) or not prompt_body.strip():
        raise ValueError("prompt_body must not be blank")
    if not isinstance(package_marker, str) or not package_marker.strip():
        raise ValueError("package_marker must not be blank")

    normalized_prompt_body = _scoped_prompt_body(
        prompt_body,
        capture_mode=capture_mode,
        backlog_policy=backlog_policy,
    )
    package_marker = package_marker.strip()
    skills = PACKAGE_SKILLS
    prompt_fingerprint = _digest(normalized_prompt_body)
    skill_fingerprint = _digest(_canonical_json({"skills": list(skills)}))
    fingerprint_payload = {
        "capture_mode": capture_mode.value,
        "backlog_policy": backlog_policy.value,
        "delivery_target": delivery_target.value,
        "name": JOB_NAME,
        "owner_marker": OWNER_MARKER,
        "package_marker": package_marker,
        "prompt_fingerprint": prompt_fingerprint,
        "origin": _origin_payload(origin),
        "schedule": {
            "catch_up": schedule.catch_up,
            "expression": schedule.expression,
            "timezone_policy": schedule.timezone_policy,
        },
        "skill_fingerprint": skill_fingerprint,
    }
    fingerprint = _digest(_canonical_json(fingerprint_payload))
    prompt = _render_prompt(
        package_marker=package_marker,
        schedule=schedule,
        delivery_target=delivery_target,
        origin=origin,
        capture_mode=capture_mode,
        backlog_policy=backlog_policy,
        prompt_fingerprint=prompt_fingerprint,
        fingerprint=fingerprint,
        prompt_body=normalized_prompt_body,
    )
    return PackageOwnedJobSpec(
        name=JOB_NAME,
        owner_marker=OWNER_MARKER,
        package_marker=package_marker,
        skills=skills,
        prompt=prompt,
        schedule=schedule,
        delivery_target=delivery_target,
        origin=origin,
        capture_mode=capture_mode,
        backlog_policy=backlog_policy,
        prompt_fingerprint=prompt_fingerprint,
        skill_fingerprint=skill_fingerprint,
        fingerprint=fingerprint,
    )
