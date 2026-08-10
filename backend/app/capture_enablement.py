"""Durable, explicit capture-enable decisions without scheduler side effects.

A missing ledger means capture is off.  This module records only an explicit
user decision and never creates, updates, or enables a Hermes cron job.  The
scheduler lifecycle consumes this evidence in a later P5 slice.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import json
import os
from pathlib import Path
import tempfile

from .capture_boundary import CaptureMode
from .cron_identity import CronOrigin, CaptureSchedule
from .file_lock import exclusive_file_lock
from .setup_contract import BacklogPolicy, DeliveryTarget


SCHEMA_VERSION = "1.1"
LEGACY_SCHEMA_VERSION = "1.0"
CAPTURE_SCOPE = "scheduled_conversation_capture"
EXPLICIT_ENABLE_CONFIRMATION = "I explicitly enable Concierge capture"


class EnablementAction(str, Enum):
    ENABLE = "enable"


class EnablementConflictError(RuntimeError):
    """A decision ID was reused with different durable content."""


@dataclass(frozen=True)
class EnablementRequest:
    """All consequential fields required for one explicit decision."""

    decision_id: str
    mode: CaptureMode
    delivery_target: DeliveryTarget
    origin: CronOrigin | None
    schedule: CaptureSchedule
    decided_at: datetime
    confirmation: str
    backlog_policy: BacklogPolicy = BacklogPolicy.START_FRESH
    scope: str = CAPTURE_SCOPE


@dataclass(frozen=True)
class EnablementDecision:
    """One append-only, inspectable enablement decision."""

    schema_version: str
    decision_id: str
    action: EnablementAction
    mode: CaptureMode
    delivery_target: DeliveryTarget
    origin: CronOrigin | None
    schedule: CaptureSchedule
    decided_at: datetime
    confirmation: str
    backlog_policy: BacklogPolicy
    scope: str = CAPTURE_SCOPE


@dataclass(frozen=True)
class CaptureEnablementState:
    """The durable decision history and its current decision pointer."""

    schema_version: str = SCHEMA_VERSION
    decisions: tuple[EnablementDecision, ...] = ()
    current_decision_id: str | None = None

    @property
    def current_decision(self) -> EnablementDecision | None:
        if self.current_decision_id is None:
            return None
        return next(
            (
                decision
                for decision in self.decisions
                if decision.decision_id == self.current_decision_id
            ),
            None,
        )

    @property
    def is_enabled(self) -> bool:
        current = self.current_decision
        return bool(
            current is not None
            and current.action is EnablementAction.ENABLE
            and (
                current.mode is not CaptureMode.OFF
                or current.backlog_policy is BacklogPolicy.PROCESS_EXISTING
            )
        )


@dataclass(frozen=True)
class EnablementWriteResult:
    state: CaptureEnablementState
    recorded: bool
    duplicate_noop: bool


class CaptureEnablementStore:
    """Atomically persist explicit capture decisions in one local JSON file."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def read(self) -> CaptureEnablementState:
        """Read the ledger, or return the non-persisted default-off state."""

        if not self.path.exists():
            return CaptureEnablementState()
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        return _parse_state(payload)

    def enable(self, request: EnablementRequest) -> EnablementWriteResult:
        """Record one explicit enable decision without touching Hermes."""

        if request.confirmation != EXPLICIT_ENABLE_CONFIRMATION:
            raise ValueError(
                "enablement requires the exact explicit confirmation"
            )
        if (
            CaptureMode(request.mode) is CaptureMode.OFF
            and BacklogPolicy(request.backlog_policy) is BacklogPolicy.START_FRESH
        ):
            raise ValueError(
                "capture enablement with mode off requires a process_existing backlog policy"
            )
        if request.scope != CAPTURE_SCOPE:
            raise ValueError(f"enablement scope must be {CAPTURE_SCOPE!r}")
        decision = _decision_from_request(request, EnablementAction.ENABLE)
        return self._record(decision)

    def _record(self, decision: EnablementDecision) -> EnablementWriteResult:
        with exclusive_file_lock(self._mutation_lock_path()):
            return self._record_unlocked(decision)

    def _record_unlocked(self, decision: EnablementDecision) -> EnablementWriteResult:
        state = self.read()
        existing = next(
            (
                item
                for item in state.decisions
                if item.decision_id == decision.decision_id
            ),
            None,
        )
        if existing is not None:
            if existing != decision:
                raise EnablementConflictError(
                    f"enablement decision {decision.decision_id!r} conflicts "
                    "with its stored durable record"
                )
            return EnablementWriteResult(
                state=state,
                recorded=False,
                duplicate_noop=True,
            )

        next_state = CaptureEnablementState(
            decisions=state.decisions + (decision,),
            current_decision_id=decision.decision_id,
        )
        _validate_state(next_state)
        self._replace(next_state)
        return EnablementWriteResult(
            state=next_state,
            recorded=True,
            duplicate_noop=False,
        )

    def _mutation_lock_path(self) -> Path:
        return self.path.with_name(f".{self.path.name}.mutation.lock")

    def mutation_guard(self):
        return exclusive_file_lock(self._mutation_lock_path())

    def _replace(self, state: CaptureEnablementState) -> None:
        validated = _validate_state(state)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: str | None = None
        try:
            descriptor, temporary_path = tempfile.mkstemp(
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                dir=self.path.parent,
                text=True,
            )
            with os.fdopen(
                descriptor,
                "w",
                encoding="utf-8",
                newline="\n",
            ) as handle:
                json.dump(
                    _state_payload(validated),
                    handle,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.path)
            temporary_path = None
        finally:
            if temporary_path is not None:
                try:
                    Path(temporary_path).unlink()
                except FileNotFoundError:
                    pass


def _decision_from_request(
    request: EnablementRequest,
    action: EnablementAction,
) -> EnablementDecision:
    if not isinstance(request.decision_id, str) or not request.decision_id.strip():
        raise ValueError("enablement decision_id must not be blank")
    if request.decided_at.tzinfo is None or request.decided_at.utcoffset() is None:
        raise ValueError("enablement decided_at must include a timezone")
    if not isinstance(request.delivery_target, DeliveryTarget):
        raise TypeError("enablement delivery_target must be a DeliveryTarget")
    if request.delivery_target is DeliveryTarget.ORIGIN and request.origin is None:
        raise ValueError("origin delivery requires an explicit durable origin")
    if request.delivery_target is not DeliveryTarget.ORIGIN and request.origin is not None:
        raise ValueError("non-origin delivery must not carry an origin")
    if request.origin is not None and not isinstance(request.origin, CronOrigin):
        raise TypeError("enablement origin must be a CronOrigin")
    if not isinstance(request.schedule, CaptureSchedule):
        raise TypeError("enablement schedule must be a CaptureSchedule")
    if not isinstance(request.confirmation, str) or not request.confirmation.strip():
        raise ValueError("enablement confirmation must not be blank")
    return EnablementDecision(
        schema_version=SCHEMA_VERSION,
        decision_id=request.decision_id.strip(),
        action=action,
        mode=CaptureMode(request.mode),
        delivery_target=request.delivery_target,
        origin=request.origin,
        schedule=request.schedule,
        decided_at=request.decided_at,
        confirmation=request.confirmation,
        backlog_policy=BacklogPolicy(request.backlog_policy),
        scope=request.scope,
    )


def _state_payload(state: CaptureEnablementState) -> dict[str, object]:
    return {
        "schema_version": state.schema_version,
        "current_decision_id": state.current_decision_id,
        "decisions": [
            {
                "action": decision.action.value,
                "confirmation": decision.confirmation,
                "decided_at": decision.decided_at.isoformat(),
                "decision_id": decision.decision_id,
                "delivery_target": decision.delivery_target.value,
                "origin": decision.origin.as_mapping() if decision.origin is not None else None,
                "mode": decision.mode.value,
                "backlog_policy": decision.backlog_policy.value,
                "schema_version": decision.schema_version,
                "scope": decision.scope,
                "schedule": {
                    "catch_up": decision.schedule.catch_up,
                    "expression": decision.schedule.expression,
                    "timezone_policy": decision.schedule.timezone_policy,
                },
            }
            for decision in state.decisions
        ],
    }


def _parse_state(payload: object) -> CaptureEnablementState:
    if not isinstance(payload, dict):
        raise ValueError("enablement ledger must be a JSON object")
    payload_schema = payload.get("schema_version")
    if payload_schema not in {SCHEMA_VERSION, LEGACY_SCHEMA_VERSION}:
        raise ValueError("unsupported enablement ledger schema version")
    raw_decisions = payload.get("decisions")
    if not isinstance(raw_decisions, list):
        raise ValueError("enablement ledger decisions must be a list")
    decisions = tuple(
        _parse_decision(item, legacy=payload_schema == LEGACY_SCHEMA_VERSION)
        for item in raw_decisions
    )
    state = CaptureEnablementState(
        schema_version=SCHEMA_VERSION,
        decisions=decisions,
        current_decision_id=payload.get("current_decision_id"),
    )
    return _validate_state(state)


def _parse_decision(payload: object, *, legacy: bool = False) -> EnablementDecision:
    if not isinstance(payload, dict):
        raise ValueError("enablement decision must be a JSON object")
    raw_schedule = payload.get("schedule")
    if not isinstance(raw_schedule, dict):
        raise ValueError("enablement decision schedule must be an object")
    raw_origin = payload.get("origin")
    if raw_origin is not None and not isinstance(raw_origin, dict):
        raise ValueError("enablement decision origin must be an object or null")
    return EnablementDecision(
        schema_version=SCHEMA_VERSION if legacy else payload.get("schema_version", ""),
        decision_id=payload.get("decision_id", ""),
        action=EnablementAction(payload.get("action")),
        mode=CaptureMode(payload.get("mode")),
        delivery_target=DeliveryTarget(payload.get("delivery_target")),
        origin=CronOrigin(**raw_origin) if raw_origin is not None else None,
        schedule=CaptureSchedule(
            expression=raw_schedule.get("expression", ""),
            timezone_policy=raw_schedule.get("timezone_policy", ""),
            catch_up=raw_schedule.get("catch_up", False),
        ),
        decided_at=datetime.fromisoformat(payload.get("decided_at", "")),
        confirmation=payload.get("confirmation", ""),
        backlog_policy=BacklogPolicy(
            payload.get("backlog_policy", BacklogPolicy.START_FRESH.value)
        ),
        scope=payload.get("scope", ""),
    )


def _validate_state(state: CaptureEnablementState) -> CaptureEnablementState:
    if state.schema_version != SCHEMA_VERSION:
        raise ValueError("unsupported enablement ledger schema version")
    seen: set[str] = set()
    for decision in state.decisions:
        if decision.schema_version != SCHEMA_VERSION:
            raise ValueError("unsupported enablement decision schema version")
        if not decision.decision_id.strip() or decision.decision_id in seen:
            raise ValueError("enablement decision IDs must be unique and nonblank")
        seen.add(decision.decision_id)
        if decision.decided_at.tzinfo is None or decision.decided_at.utcoffset() is None:
            raise ValueError("enablement decided_at must include a timezone")
        if decision.scope != CAPTURE_SCOPE:
            raise ValueError(f"enablement scope must be {CAPTURE_SCOPE!r}")
        if decision.delivery_target is DeliveryTarget.ORIGIN and decision.origin is None:
            raise ValueError("origin delivery requires an explicit durable origin")
        if decision.delivery_target is not DeliveryTarget.ORIGIN and decision.origin is not None:
            raise ValueError("non-origin delivery must not carry an origin")
        if decision.action is EnablementAction.ENABLE:
            if (
                decision.mode is CaptureMode.OFF
                and decision.backlog_policy is BacklogPolicy.START_FRESH
            ):
                raise ValueError(
                    "an enable decision with mode off requires a process_existing backlog policy"
                )
            if decision.confirmation != EXPLICIT_ENABLE_CONFIRMATION:
                raise ValueError("enable decision has invalid explicit confirmation")
    if state.current_decision_id is not None and state.current_decision_id not in seen:
        raise ValueError("current enablement decision must exist in the ledger")
    if state.decisions and state.current_decision_id != state.decisions[-1].decision_id:
        raise ValueError("current enablement decision must be the latest decision")
    return state
