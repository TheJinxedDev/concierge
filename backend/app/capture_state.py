"""Pure state, claim, cursor, lock, and run-report contracts for capture.

This module describes durable workflow facts without implementing file I/O,
locking, scheduling, persistence, MCP calls, or runtime session capture.
"""

from datetime import datetime
from enum import Enum
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    AliasChoices,
    Field,
    StringConstraints,
    TypeAdapter,
    model_validator,
)

from .completed_session_source import CompletedSessionWatermark
from .domain import ContractModel, NonBlankText


SCHEMA_VERSION = "1.1"
Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
StableText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


def _require_timezone(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("workflow timestamps must include a timezone")
    return value


AwareDatetime = Annotated[datetime, AfterValidator(_require_timezone)]
CaptureSchemaVersion = Literal["1.0", "1.1"]


class TerminalDisposition(str, Enum):
    """Dispositions that are safe to record as processed cursor work."""

    PROPOSED_SUCCESSFULLY = "proposed_successfully"
    INTENTIONALLY_IRRELEVANT = "intentionally_irrelevant"
    AMBIGUOUS_RECORDED = "ambiguous_recorded"
    MALFORMED_PERMANENTLY_REJECTED = "malformed_permanently_rejected"
    ALREADY_PROCESSED = "already_processed"


class ClaimStatus(str, Enum):
    IN_PROGRESS = "in_progress"
    TERMINAL = "terminal"
    RETRYABLE = "retryable"
    BLOCKED = "blocked"


class FailureClass(str, Enum):
    TRANSIENT = "transient"
    AMBIGUOUS = "ambiguous"
    SOURCE_UNREADABLE = "source_unreadable"
    SOURCE_CHANGED = "source_changed"
    CONTRACT = "contract"
    UNKNOWN_COMMIT = "unknown_commit"
    PERMANENT = "permanent"


class RunTerminalStatus(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"
    BLOCKED = "blocked"
    NO_VISIBLE_EVIDENCE = "no_visible_evidence"


class ReasonCode(str, Enum):
    NO_VISIBLE_EVIDENCE = "NO_VISIBLE_EVIDENCE"
    AMBIGUOUS_IDENTITY = "AMBIGUOUS_IDENTITY"
    CONFLICT_REQUIRES_REVIEW = "CONFLICT_REQUIRES_REVIEW"
    SOURCE_UNREADABLE = "SOURCE_UNREADABLE"
    SOURCE_CHANGED_AFTER_DISCOVERY = "SOURCE_CHANGED_AFTER_DISCOVERY"
    MCP_UNAVAILABLE = "MCP_UNAVAILABLE"
    CONTRACT_MISMATCH = "CONTRACT_MISMATCH"
    LOCK_SKIPPED = "LOCK_SKIPPED"
    STALE_LOCK_RECLAIMED = "STALE_LOCK_RECLAIMED"
    DUPLICATE_NOOP = "DUPLICATE_NOOP"
    UNKNOWN_COMMIT_OUTCOME = "UNKNOWN_COMMIT_OUTCOME"


class BacklogReason(str, Enum):
    BATCH_LIMIT = "batch_limit"
    DISCOVERY_BOUNDARY = "discovery_boundary"
    RETRY_PENDING = "retry_pending"
    SOURCE_BLOCKED = "source_blocked"


class LockOutcome(str, Enum):
    ACQUIRED = "acquired"
    RECLAIMED = "reclaimed"
    RELEASED = "released"
    SKIPPED = "skipped"
    RELEASE_FAILED = "release_failed"


class SourceCursor(ContractModel):
    """Chronological session/message watermark."""

    session_id: StableText
    last_user_message_id: int = Field(ge=0)
    session_ended_at: AwareDatetime | None = None


def _cursor_order_key(cursor: SourceCursor) -> tuple[datetime, str, int] | None:
    if cursor.session_ended_at is None:
        return None
    return (
        cursor.session_ended_at,
        cursor.session_id,
        cursor.last_user_message_id,
    )


def _require_cursor_within_boundary(
    cursor: SourceCursor,
    boundary: SourceCursor,
    *,
    message: str,
    require_same_session: bool = False,
) -> None:
    boundary_key = _cursor_order_key(boundary)
    cursor_key = _cursor_order_key(cursor)
    if boundary_key is None:
        if (
            (require_same_session and cursor.session_id != boundary.session_id)
            or (
                cursor.session_id == boundary.session_id
                and cursor.last_user_message_id > boundary.last_user_message_id
            )
        ):
            raise ValueError(message)
        return
    if cursor_key is None or cursor_key > boundary_key:
        raise ValueError(message)


def _validate_discovery_boundary(
    *,
    schema_version: CaptureSchemaVersion,
    discovery_as_of: datetime | None,
    discovery_boundary: SourceCursor,
) -> None:
    if discovery_as_of is None:
        if discovery_boundary.session_ended_at is not None:
            raise ValueError(
                "multi-session discovery boundaries require discovery_as_of"
            )
        return
    if schema_version != "1.1":
        raise ValueError("discovery_as_of requires capture schema 1.1")
    if discovery_boundary.session_ended_at is None:
        raise ValueError(
            "discovery_as_of requires a session-ended discovery boundary"
        )
    if discovery_boundary.session_ended_at >= discovery_as_of:
        raise ValueError("discovery boundary must end before discovery_as_of")


class ProcessedAction(ContractModel):
    """One terminal action-ledger entry tied to exact source content."""

    action_id: NonBlankText
    source_ref: StableText
    source_cursor: SourceCursor
    content_hash: Sha256Hex
    disposition: TerminalDisposition
    processed_at: AwareDatetime
    proposal_id: NonBlankText | None = None
    reason_code: ReasonCode | None = None

    @property
    def cursor_advance_allowed(self) -> bool:
        return True

    @model_validator(mode="after")
    def require_proposal_for_successful_action(self) -> "ProcessedAction":
        if (
            self.disposition is TerminalDisposition.PROPOSED_SUCCESSFULLY
            and self.proposal_id is None
        ):
            raise ValueError(
                "proposed_successfully actions require a proposal_id"
            )
        return self


class ClaimRecord(ContractModel):
    """Ownership and retry state for one source/action claim."""

    claim_id: NonBlankText
    source_ref: StableText
    content_hash: Sha256Hex
    status: ClaimStatus
    owner_run_id: NonBlankText
    attempt_count: int = Field(ge=1)
    claimed_at: AwareDatetime
    updated_at: AwareDatetime
    expires_at: AwareDatetime
    result_id: NonBlankText | None = None
    proposal_id: NonBlankText | None = None
    disposition: TerminalDisposition | None = None
    failure_class: FailureClass | None = None

    @model_validator(mode="after")
    def validate_claim_transition(self) -> "ClaimRecord":
        if self.updated_at < self.claimed_at:
            raise ValueError("updated_at cannot precede claimed_at")
        if self.expires_at <= self.claimed_at:
            raise ValueError("expires_at must be after claimed_at")

        if self.status is ClaimStatus.TERMINAL:
            if self.disposition is None:
                raise ValueError("terminal claims require a disposition")
        elif self.disposition is not None:
            raise ValueError("only terminal claims may carry a disposition")

        if self.status in {ClaimStatus.RETRYABLE, ClaimStatus.BLOCKED}:
            if self.failure_class is None:
                raise ValueError(
                    "retryable and blocked claims require a failure_class"
                )
        elif self.failure_class is not None:
            raise ValueError(
                "in-progress and terminal claims cannot carry a failure_class"
            )

        if (
            self.failure_class is FailureClass.UNKNOWN_COMMIT
            and self.status is not ClaimStatus.BLOCKED
        ):
            raise ValueError("unknown commit outcomes must remain blocked")
        if (
            self.disposition is TerminalDisposition.PROPOSED_SUCCESSFULLY
            and (self.result_id is None or self.proposal_id is None)
        ):
            raise ValueError(
                "successful terminal claims require result_id and proposal_id"
            )
        return self


class CaptureLock(ContractModel):
    """Pure record of lock ownership, expiry, and stale-lock reclamation."""

    lock_name: NonBlankText
    owner_run_id: NonBlankText
    owner_token: NonBlankText
    acquired_at: AwareDatetime
    expires_at: AwareDatetime
    reclaimed_from_run_id: NonBlankText | None = None
    reclaimed_at: AwareDatetime | None = None

    def is_stale(self, at: datetime) -> bool:
        return at >= self.expires_at

    @model_validator(mode="after")
    def validate_reclaim_record(self) -> "CaptureLock":
        if self.expires_at <= self.acquired_at:
            raise ValueError("expires_at must be after acquired_at")
        if (self.reclaimed_from_run_id is None) != (self.reclaimed_at is None):
            raise ValueError(
                "reclaimed_from_run_id and reclaimed_at must be provided together"
            )
        if self.reclaimed_from_run_id == self.owner_run_id:
            raise ValueError("a lock cannot be reclaimed from its current owner")
        if self.reclaimed_at is not None and self.reclaimed_at < self.acquired_at:
            raise ValueError("reclaimed_at cannot precede acquired_at")
        return self


class BacklogState(ContractModel):
    """Whether unprocessed source remains and why the run stopped."""

    remaining: bool
    reason: BacklogReason | None = None

    @model_validator(mode="after")
    def require_reason_only_for_remaining_backlog(self) -> "BacklogState":
        if self.remaining and self.reason is None:
            raise ValueError("remaining backlog requires a backlog reason")
        if not self.remaining and self.reason is not None:
            raise ValueError("completed backlog cannot carry a backlog reason")
        return self


class CaptureStats(ContractModel):
    """Cumulative counters stored with the durable handoff state."""

    reviewed_sessions: int = Field(ge=0)
    reviewed_messages: int = Field(ge=0)
    source_characters: int = Field(ge=0)
    proposals_submitted: int = Field(ge=0)
    duplicate_noops: int = Field(ge=0)


class LastRunSummary(ContractModel):
    run_id: NonBlankText
    started_at: AwareDatetime
    discovery_boundary: SourceCursor
    discovery_as_of: AwareDatetime | None = None
    source_cursor_start: SourceCursor
    terminal_status: RunTerminalStatus
    finished_at: AwareDatetime
    source_cursor: SourceCursor
    completed_session_watermark: CompletedSessionWatermark | None = None
    batch_count: int = Field(ge=0)
    reviewed_sessions: int = Field(ge=0)
    reviewed_messages: int = Field(ge=0)
    source_characters: int = Field(ge=0)
    action_ids: list[NonBlankText]
    claim_ids: list[NonBlankText]
    durable_action_ids: list[NonBlankText] = Field(default_factory=list)
    durable_claim_ids: list[NonBlankText] = Field(default_factory=list)
    durable_batches_completed: int = Field(default=0, ge=0)
    durable_stats: CaptureStats | None = None
    durable_discovered_sources: list[SourceCursor] = Field(default_factory=list)
    durable_errors: list[NonBlankText] = Field(default_factory=list)
    proposal_ids: list[NonBlankText]
    reason_codes: list[ReasonCode]
    backlog: BacklogState
    backlog_remaining: bool
    lock_outcome: LockOutcome
    lock: CaptureLock | None
    canonical_media_changed: bool
    retryable_claim_ids: list[NonBlankText]
    errors: list[NonBlankText]


class CaptureState(ContractModel):
    """Versioned handoff state for a future cursor-bearing capture job."""

    schema_version: CaptureSchemaVersion
    discovery_boundary: SourceCursor
    discovery_as_of: AwareDatetime | None = None
    discovered_sources: list[SourceCursor] = Field(default_factory=list)
    source_cursor: SourceCursor = Field(
        validation_alias=AliasChoices("source_cursor", "watermark")
    )
    completed_session_watermark: CompletedSessionWatermark | None = None
    processed_actions: dict[str, ProcessedAction] = Field(default_factory=dict)
    claims: dict[str, ClaimRecord] = Field(default_factory=dict)
    lock: CaptureLock | None = None
    backlog: BacklogState
    batches_completed: int = Field(ge=0)
    stats: CaptureStats
    errors: list[NonBlankText] = Field(default_factory=list)
    canonical_media_changed: bool = False
    last_run: LastRunSummary | None = None

    @model_validator(mode="after")
    def validate_ledger_keys(self) -> "CaptureState":
        _validate_discovery_boundary(
            schema_version=self.schema_version,
            discovery_as_of=self.discovery_as_of,
            discovery_boundary=self.discovery_boundary,
        )
        if (
            self.completed_session_watermark is not None
            and self.schema_version != "1.1"
        ):
            raise ValueError(
                "completed_session_watermark requires capture schema 1.1"
            )
        for key, action in self.processed_actions.items():
            if not key.strip():
                raise ValueError("processed action keys must be nonblank")
            if key != action.action_id:
                raise ValueError("processed action key must match action_id")
        for key, claim in self.claims.items():
            if not key.strip():
                raise ValueError("claim keys must be nonblank")
            if key != claim.claim_id:
                raise ValueError("claim key must match claim_id")
        discovered = [
            (
                cursor.session_id,
                cursor.session_ended_at,
                cursor.last_user_message_id,
            )
            for cursor in self.discovered_sources
        ]
        if len(discovered) != len(set(discovered)):
            raise ValueError("discovered source cursors must be unique")
        for source_cursor in self.discovered_sources:
            _require_cursor_within_boundary(
                source_cursor,
                self.discovery_boundary,
                message="discovered sources must remain within the discovery boundary",
                require_same_session=True,
            )
        _require_cursor_within_boundary(
            self.source_cursor,
            self.discovery_boundary,
            message="source cursor must remain within the discovery boundary",
        )
        return self


class CaptureRunReport(ContractModel):
    """Versioned, per-invocation report with explicit cursor safety."""

    schema_version: CaptureSchemaVersion
    run_id: NonBlankText
    started_at: AwareDatetime
    finished_at: AwareDatetime
    discovery_boundary: SourceCursor
    discovery_as_of: AwareDatetime | None = None
    source_cursor_start: SourceCursor
    source_cursor_end: SourceCursor = Field(
        validation_alias=AliasChoices("source_cursor_end", "final_cursor")
    )
    completed_session_watermark: CompletedSessionWatermark | None = None
    batch_count: int = Field(ge=0)
    reviewed_sessions: int = Field(ge=0)
    reviewed_messages: int = Field(ge=0)
    source_characters: int = Field(ge=0)
    claims: list[ClaimRecord] = Field(default_factory=list)
    actions: list[ProcessedAction] = Field(default_factory=list)
    durable_action_ids: list[NonBlankText] = Field(default_factory=list)
    durable_claim_ids: list[NonBlankText] = Field(default_factory=list)
    durable_batches_completed: int = Field(default=0, ge=0)
    durable_stats: CaptureStats | None = None
    durable_discovered_sources: list[SourceCursor] = Field(default_factory=list)
    durable_errors: list[NonBlankText] = Field(default_factory=list)
    proposal_ids: list[NonBlankText] = Field(default_factory=list)
    terminal_status: RunTerminalStatus
    reason_codes: list[ReasonCode] = Field(default_factory=list)
    backlog: BacklogState
    lock_outcome: LockOutcome
    lock: CaptureLock | None = None
    state_path: NonBlankText
    canonical_media_changed: bool
    retryable_claim_ids: list[NonBlankText] = Field(default_factory=list)
    errors: list[NonBlankText] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_report_safety(self) -> "CaptureRunReport":
        if self.finished_at < self.started_at:
            raise ValueError("finished_at cannot precede started_at")
        _validate_discovery_boundary(
            schema_version=self.schema_version,
            discovery_as_of=self.discovery_as_of,
            discovery_boundary=self.discovery_boundary,
        )
        if (
            self.completed_session_watermark is not None
            and self.schema_version != "1.1"
        ):
            raise ValueError(
                "completed_session_watermark requires capture schema 1.1"
            )

        action_ids = [action.action_id for action in self.actions]
        if len(action_ids) != len(set(action_ids)):
            raise ValueError("action_id values must be unique in a run report")
        claim_ids = [claim.claim_id for claim in self.claims]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("claim_id values must be unique in a run report")
        if len(self.proposal_ids) != len(set(self.proposal_ids)):
            raise ValueError("proposal_ids must be unique in a run report")

        action_proposals = {
            action.proposal_id
            for action in self.actions
            if action.proposal_id is not None
        }
        if not action_proposals.issubset(set(self.proposal_ids)):
            raise ValueError("proposal_ids must include every action proposal_id")

        claims_by_id = {claim.claim_id: claim for claim in self.claims}
        for claim_id in self.retryable_claim_ids:
            claim = claims_by_id.get(claim_id)
            if claim is None:
                raise ValueError("retryable_claim_ids must reference report claims")
            if claim.status not in {ClaimStatus.RETRYABLE, ClaimStatus.BLOCKED}:
                raise ValueError(
                    "retryable_claim_ids must reference retryable or blocked claims"
                )

        for cursor_name, cursor in (
            ("source_cursor_start", self.source_cursor_start),
            ("source_cursor_end", self.source_cursor_end),
        ):
            _require_cursor_within_boundary(
                cursor,
                self.discovery_boundary,
                message=f"{cursor_name} cannot pass the discovery boundary",
            )

        source_cursors = [action.source_cursor for action in self.actions]
        for source_cursor in source_cursors:
            _require_cursor_within_boundary(
                source_cursor,
                self.discovery_boundary,
                message="action source cursors must remain within the discovery boundary",
                require_same_session=True,
            )

        cursor_moved = self.source_cursor_end != self.source_cursor_start
        if cursor_moved:
            if not self.actions:
                raise ValueError("cursor cannot advance without processed actions")
            if any(not action.cursor_advance_allowed for action in self.actions):
                raise ValueError("cursor cannot advance past a non-terminal action")
            if any(claim.status is not ClaimStatus.TERMINAL for claim in self.claims):
                raise ValueError("cursor cannot advance past a non-terminal claim")
            if self.source_cursor_end not in source_cursors:
                raise ValueError(
                    "cursor advancement must end at an action source_cursor"
                )
        unknown_commit_claims = [
            claim
            for claim in self.claims
            if claim.failure_class is FailureClass.UNKNOWN_COMMIT
        ]
        if unknown_commit_claims:
            if ReasonCode.UNKNOWN_COMMIT_OUTCOME not in self.reason_codes:
                raise ValueError(
                    "unknown commit claims require UNKNOWN_COMMIT_OUTCOME"
                )
            if cursor_moved:
                raise ValueError("unknown commit outcomes must hold the cursor")
            if self.terminal_status is RunTerminalStatus.COMPLETE:
                raise ValueError(
                    "unknown commit outcomes cannot produce a complete report"
                )

        if self.terminal_status is RunTerminalStatus.COMPLETE and self.backlog.remaining:
            raise ValueError("complete reports cannot carry remaining backlog")
        if (
            self.terminal_status is RunTerminalStatus.NO_VISIBLE_EVIDENCE
            and ReasonCode.NO_VISIBLE_EVIDENCE not in self.reason_codes
        ):
            raise ValueError("no_visible_evidence reports require NO_VISIBLE_EVIDENCE")
        if (
            ReasonCode.LOCK_SKIPPED in self.reason_codes
            and self.lock_outcome is not LockOutcome.SKIPPED
        ):
            raise ValueError("LOCK_SKIPPED requires a skipped lock outcome")
        if (
            self.lock_outcome is LockOutcome.RECLAIMED
            and ReasonCode.STALE_LOCK_RECLAIMED not in self.reason_codes
        ):
            raise ValueError("reclaimed locks require STALE_LOCK_RECLAIMED")
        if self.lock_outcome is LockOutcome.RELEASED and self.lock is not None:
            raise ValueError("released reports cannot retain a durable capture lock")
        if (
            self.lock_outcome
            in {
                LockOutcome.ACQUIRED,
                LockOutcome.RECLAIMED,
                LockOutcome.SKIPPED,
                LockOutcome.RELEASE_FAILED,
            }
            and self.lock is None
        ):
            raise ValueError("non-released reports require their durable capture lock")
        if (
            self.lock_outcome
            in {
                LockOutcome.ACQUIRED,
                LockOutcome.RECLAIMED,
                LockOutcome.RELEASE_FAILED,
            }
            and self.lock is not None
            and self.lock.owner_run_id != self.run_id
        ):
            raise ValueError("non-skipped report lock owner must match the run ID")
        return self


_CAPTURE_STATE_ADAPTER = TypeAdapter(CaptureState)
_CAPTURE_RUN_REPORT_ADAPTER = TypeAdapter(CaptureRunReport)


def parse_capture_state(payload: object) -> CaptureState:
    """Validate state without reading or writing the filesystem."""

    return _CAPTURE_STATE_ADAPTER.validate_python(payload)


def parse_capture_run_report(payload: object) -> CaptureRunReport:
    """Validate one per-invocation report without executing the run."""

    return _CAPTURE_RUN_REPORT_ADAPTER.validate_python(payload)
