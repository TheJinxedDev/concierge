"""Native-Hermes job plans for Concierge's explicit automation choices.

Concierge owns its data, prompts, and promotion command. Hermes owns scheduled
job storage and execution through its public ``cronjob`` tool or ``hermes cron``
CLI. This module deliberately never imports Hermes implementation modules or
opens a Hermes session database.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
from pathlib import Path

from .automation_preferences import AutomationPreferences
from .setup_contract import DeliveryTarget


class AutomationJobKind(str, Enum):
    BACKLOG = "backlog"
    RECENT_CAPTURE = "recent_capture"
    PROMOTION = "promotion"


@dataclass(frozen=True)
class AutomationJobSpec:
    """A plan the caller must create through Hermes' public scheduler surface."""

    kind: AutomationJobKind
    name: str
    owner_marker: str
    schedule: str
    prompt: str
    fingerprint: str
    skills: tuple[str, ...] = ("concierge",)
    delivery_target: DeliveryTarget = DeliveryTarget.LOCAL
    workdir: str | None = None


def _fingerprint(payload: object) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _job(
    kind: AutomationJobKind,
    name: str,
    schedule: str,
    body: str,
    *,
    runtime_root: Path | None,
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
    prompt = f"{metadata}\n\n{body.strip()}\n"
    return AutomationJobSpec(
        kind=kind,
        name=name,
        owner_marker=owner_marker,
        schedule=schedule,
        prompt=prompt,
        fingerprint=_fingerprint(
            {
                "kind": kind.value,
                "name": name,
                "owner_marker": owner_marker,
                "schedule": schedule,
                "prompt": prompt,
            }
        ),
        workdir=str(runtime_root) if runtime_root is not None else None,
    )


def _capture_instructions(*, backlog: bool) -> str:
    scope = (
        "Run one finite review pass over the explicitly selected existing backlog. "
        "When the pass is complete, report that it is ready for its owner to retire."
        if backlog
        else "Run one bounded ongoing pass over ended prior sessions selected by native Hermes search."
    )
    return f"""
{scope}

Use Hermes' native `session_search` tool to inspect only prior, completed
sessions. Do not inspect the active conversation. Do not open a Hermes state
database, search for a Hermes source checkout, import private Hermes modules, or
install/use `croniter`.

For each conservative, supported capture candidate, use Concierge's configured
MCP surface to create only a reviewable pending proposal. Search canonical media
first; preserve source context and uncertainty; emit canonical before/after
receipts. Never silently create canonical media, accept/reject a proposal,
promote a proposal, generate a numeric taste score, infer dates/scores, or
observe a live session. If the session boundary or evidence is ambiguous, leave
it untouched and report the abstention.

This job is proposal-first capture only. It does not run automatic promotion.
"""


def _promotion_instructions(runtime_root: Path | None, data_directory: Path | None) -> str:
    command = "env -u PYTHONPATH -u VIRTUAL_ENV UV_PROJECT_ENVIRONMENT=<CONCIERGE_ENV> uv run --locked python scripts/run_automatic_promotion.py --data-dir <CONCIERGE_DATA>"
    if runtime_root is not None:
        command = (
            "env -u PYTHONPATH -u VIRTUAL_ENV UV_PROJECT_ENVIRONMENT=<CONCIERGE_ENV> uv run --locked "
            f'--directory "{runtime_root}" --project "{runtime_root}" python '
            f'"{runtime_root / "scripts" / "run_automatic_promotion.py"}" '
            f'--data-dir "{data_directory or "<CONCIERGE_DATA>"}"'
        )
    return f"""
Run Concierge's explicit automatic-promotion pass over its pending proposal
inbox only. The associated capture source is already enabled by the same
onboarding decision. Do not read session history or observe an active session.

Apply only the documented beta rubric and 0.85 threshold. Keep every abstention
pending. Do not generate numeric taste scores, infer missing facts, or use an
MCP review/mutation shortcut. Require and report the script's canonical
before/after receipt; a scheduler status alone is not evidence of success.

Run exactly this local Concierge command from the job workdir after resolving
the profile-scoped environment and data path:
`{command}`
"""


def build_automation_job_specs(
    preferences: AutomationPreferences,
    *,
    schedule: str = "0 4 * * 0",
    runtime_root: Path | None = None,
    data_directory: Path | None = None,
) -> tuple[AutomationJobSpec, ...]:
    """Emit plans for only the explicitly enabled native Hermes jobs."""
    if not schedule.strip():
        raise ValueError("automation schedule must not be blank")

    jobs: list[AutomationJobSpec] = []
    if preferences.backlog_cron_enabled:
        jobs.append(
            _job(
                AutomationJobKind.BACKLOG,
                "concierge-backlog-capture",
                schedule,
                _capture_instructions(backlog=True),
                runtime_root=runtime_root,
            )
        )
    if preferences.recent_capture_cron_enabled:
        jobs.append(
            _job(
                AutomationJobKind.RECENT_CAPTURE,
                "concierge-session-capture",
                schedule,
                _capture_instructions(backlog=False),
                runtime_root=runtime_root,
            )
        )
    if preferences.promotion_cron_enabled:
        jobs.append(
            _job(
                AutomationJobKind.PROMOTION,
                "concierge-auto-promotion",
                schedule,
                _promotion_instructions(runtime_root, data_directory),
                runtime_root=runtime_root,
            )
        )
    return tuple(jobs)
