"""Pure bounded-batch and failure-disposition rules for capture.

P3.4 keeps source enumeration and execution outside this module. It accepts an
already ordered sequence of caller-owned source summaries, plans only the work
that fits the ratified per-run ceilings, and maps failure kinds to explicit
claim/cursor behavior without reading or writing state.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from .capture_state import (
    BacklogReason,
    ClaimStatus,
    FailureClass,
    SourceCursor,
    TerminalDisposition,
)


MAX_COMPLETED_SESSIONS = 20
MAX_SOURCE_CHARACTERS = 45_000


@dataclass(frozen=True)
class BatchLimits:
    """Caller-configurable limits bounded by the semantic-beta ceiling."""

    max_completed_sessions: int = MAX_COMPLETED_SESSIONS
    max_source_characters: int = MAX_SOURCE_CHARACTERS

    def __post_init__(self) -> None:
        if self.max_completed_sessions < 1:
            raise ValueError("max_completed_sessions must be at least 1")
        if self.max_completed_sessions > MAX_COMPLETED_SESSIONS:
            raise ValueError("max_completed_sessions cannot exceed 20")
        if self.max_source_characters < 1:
            raise ValueError("max_source_characters must be at least 1")
        if self.max_source_characters > MAX_SOURCE_CHARACTERS:
            raise ValueError("max_source_characters cannot exceed 45,000")


@dataclass(frozen=True)
class BatchSource:
    """One already ordered source/message summary eligible for planning."""

    cursor: SourceCursor
    source_ref: str
    source_characters: int

    def __post_init__(self) -> None:
        if not self.source_ref or any(character.isspace() for character in self.source_ref):
            raise ValueError("source_ref must be nonblank and contain no whitespace")
        if self.source_characters < 0:
            raise ValueError("source_characters cannot be negative")


@dataclass(frozen=True)
class BatchPlan:
    items: tuple[BatchSource, ...]
    completed_sessions: int
    source_characters: int
    backlog_remaining: bool
    backlog_reason: BacklogReason | None


class FailureKind(str, Enum):
    TRANSIENT = "transient"
    PERMANENT = "permanent"
    AMBIGUOUS = "ambiguous"
    UNREADABLE = "unreadable"
    CHANGED_SOURCE = "changed_source"
    CONTRACT = "contract"
    UNKNOWN_COMMIT = "unknown_commit"
    INTERRUPTED = "interrupted"


@dataclass(frozen=True)
class FailureDecision:
    failure_kind: FailureKind
    failure_class: FailureClass
    claim_status: ClaimStatus
    disposition: TerminalDisposition | None
    cursor_advance_allowed: bool
    retryable: bool
    blocked: bool


_FAILURE_DECISIONS: dict[FailureKind, FailureDecision] = {
    FailureKind.TRANSIENT: FailureDecision(
        FailureKind.TRANSIENT,
        FailureClass.TRANSIENT,
        ClaimStatus.RETRYABLE,
        None,
        False,
        True,
        False,
    ),
    FailureKind.PERMANENT: FailureDecision(
        FailureKind.PERMANENT,
        FailureClass.PERMANENT,
        ClaimStatus.TERMINAL,
        TerminalDisposition.MALFORMED_PERMANENTLY_REJECTED,
        True,
        False,
        False,
    ),
    FailureKind.AMBIGUOUS: FailureDecision(
        FailureKind.AMBIGUOUS,
        FailureClass.AMBIGUOUS,
        ClaimStatus.TERMINAL,
        TerminalDisposition.AMBIGUOUS_RECORDED,
        True,
        False,
        False,
    ),
    FailureKind.UNREADABLE: FailureDecision(
        FailureKind.UNREADABLE,
        FailureClass.SOURCE_UNREADABLE,
        ClaimStatus.RETRYABLE,
        None,
        False,
        True,
        False,
    ),
    FailureKind.CHANGED_SOURCE: FailureDecision(
        FailureKind.CHANGED_SOURCE,
        FailureClass.SOURCE_CHANGED,
        ClaimStatus.RETRYABLE,
        None,
        False,
        True,
        False,
    ),
    FailureKind.CONTRACT: FailureDecision(
        FailureKind.CONTRACT,
        FailureClass.CONTRACT,
        ClaimStatus.TERMINAL,
        TerminalDisposition.MALFORMED_PERMANENTLY_REJECTED,
        True,
        False,
        False,
    ),
    FailureKind.UNKNOWN_COMMIT: FailureDecision(
        FailureKind.UNKNOWN_COMMIT,
        FailureClass.UNKNOWN_COMMIT,
        ClaimStatus.BLOCKED,
        None,
        False,
        False,
        True,
    ),
    FailureKind.INTERRUPTED: FailureDecision(
        FailureKind.INTERRUPTED,
        FailureClass.TRANSIENT,
        ClaimStatus.RETRYABLE,
        None,
        False,
        True,
        False,
    ),
}


def plan_batch(
    sources: Iterable[BatchSource],
    *,
    limits: BatchLimits | None = None,
) -> BatchPlan:
    """Take the largest chronological prefix that fits both hard ceilings."""

    effective_limits = limits or BatchLimits()
    selected: list[BatchSource] = []
    sessions: set[str] = set()
    characters = 0
    backlog_remaining = False

    for source in sources:
        is_new_session = source.cursor.session_id not in sessions
        if is_new_session and len(sessions) >= effective_limits.max_completed_sessions:
            backlog_remaining = True
            break
        if characters + source.source_characters > effective_limits.max_source_characters:
            backlog_remaining = True
            break
        selected.append(source)
        sessions.add(source.cursor.session_id)
        characters += source.source_characters

    return BatchPlan(
        items=tuple(selected),
        completed_sessions=len(sessions),
        source_characters=characters,
        backlog_remaining=backlog_remaining,
        backlog_reason=BacklogReason.BATCH_LIMIT if backlog_remaining else None,
    )


def resolve_failure(kind: FailureKind | str) -> FailureDecision:
    """Return the closed claim/retry/cursor decision for one failure kind."""

    parsed = kind if isinstance(kind, FailureKind) else FailureKind(kind)
    return _FAILURE_DECISIONS[parsed]
