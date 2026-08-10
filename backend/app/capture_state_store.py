"""Caller-owned JSON persistence for the versioned capture workflow state.

This module is the P3.2 persistence seam. It validates every read/write through
``capture_state`` and replaces the state file atomically, but it deliberately does
not acquire locks, reclaim stale ownership, schedule work, or access live capture
sources. P3.3 owns the lock/claim coordination around this store.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import tempfile

from .capture_state import (
    ClaimStatus,
    CaptureState,
    ProcessedAction,
    SourceCursor,
    parse_capture_state,
)
from .completed_session_source import (
    CompletedSessionWatermark,
    completed_session_order_key,
)
from .file_lock import exclusive_file_lock


class CaptureStateStoreError(RuntimeError):
    """Base error for state-store decisions that prevent a safe write."""


class StateActionConflictError(CaptureStateStoreError):
    """A stable action ID was reused with a different terminal payload."""


class StateCursorRegressionError(CaptureStateStoreError):
    """A new action attempted to move a same-session cursor backwards."""


class StateCursorSourceBindingError(CaptureStateStoreError):
    """A cursor move was not tied to a discovered, exact source identity."""


class StateWatermarkRegressionError(CaptureStateStoreError):
    """A completed-session watermark attempted to move backwards."""


class StateWatermarkBlockedError(CaptureStateStoreError):
    """A run still owns non-terminal work, so its watermark must remain held."""


class StateDiscoveryRegressionError(CaptureStateStoreError):
    """A discovery snapshot or boundary attempted to move backwards."""


class StateDiscoverySourceBindingError(CaptureStateStoreError):
    """A discovered source conflicted with an existing exact source identity."""


@dataclass(frozen=True)
class ActionLedgerResult:
    """Outcome of one conflict-safe action-ledger write attempt."""

    state: CaptureState
    recorded: bool
    duplicate_noop: bool


@dataclass(frozen=True)
class WatermarkAdvanceResult:
    """Outcome of one conflict-safe completed-session watermark advance."""

    state: CaptureState
    recorded: bool
    duplicate_noop: bool


@dataclass(frozen=True)
class DiscoveryAdvanceResult:
    """Outcome of one monotonic completed-session discovery advancement."""

    state: CaptureState
    recorded: bool
    duplicate_noop: bool


class CaptureStateStore:
    """Read and atomically replace one caller-owned capture-state JSON file."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def _mutation_lock_path(self) -> Path:
        return self.path.with_name(f".{self.path.name}.mutation.lock")

    def mutation_guard(self):
        return exclusive_file_lock(self._mutation_lock_path())

    def create(self, state: CaptureState) -> None:
        """Create the state file once; refuse to overwrite an existing handoff."""

        with exclusive_file_lock(self._mutation_lock_path()):
            if self.path.exists():
                raise FileExistsError(self.path)
            self._replace(state)

    def read(self) -> CaptureState:
        """Read and validate the complete state document from disk."""

        payload = json.loads(self.path.read_text(encoding="utf-8"))
        return parse_capture_state(payload)

    def replace(self, state: CaptureState) -> None:
        """Atomically replace the complete validated state document."""

        with exclusive_file_lock(self._mutation_lock_path()):
            self._replace(state)

    def advance_completed_session_watermark(
        self,
        watermark: CompletedSessionWatermark,
        *,
        run_id: str,
    ) -> WatermarkAdvanceResult:
        """Persist one monotonic completed-session watermark after terminal work.

        Exact replay is a no-op. A regression is rejected without replacing the
        state file. Claims owned by this run must all be terminal before a new
        watermark can be recorded; blocked, retryable, or in-progress work
        keeps the source boundary held for a later retry.
        """

        if not isinstance(watermark, CompletedSessionWatermark):
            raise TypeError("watermark must be a CompletedSessionWatermark")
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("run_id must be nonblank text")

        with exclusive_file_lock(self._mutation_lock_path()):
            state = self.read()
            current = state.completed_session_watermark
            if current == watermark:
                return WatermarkAdvanceResult(
                    state=state,
                    recorded=False,
                    duplicate_noop=True,
                )
            if current is not None and _watermark_key(watermark) < _watermark_key(current):
                raise StateWatermarkRegressionError(
                    "a completed-session watermark cannot move backwards"
                )
            nonterminal_claims = [
                claim
                for claim in state.claims.values()
                if claim.owner_run_id == run_id
                and claim.status is not ClaimStatus.TERMINAL
            ]
            if nonterminal_claims:
                claim_ids = ", ".join(sorted(claim.claim_id for claim in nonterminal_claims))
                raise StateWatermarkBlockedError(
                    f"watermark cannot advance past non-terminal claims: {claim_ids}"
                )

            next_state = state.model_copy(deep=True)
            next_state.schema_version = "1.1"
            next_state.completed_session_watermark = watermark
            self._replace(next_state)
            return WatermarkAdvanceResult(
                state=next_state,
                recorded=True,
                duplicate_noop=False,
            )

    def advance_completed_session_discovery(
        self,
        *,
        discovery_as_of: datetime,
        discovery_boundary: SourceCursor,
        discovered_sources: list[SourceCursor],
    ) -> DiscoveryAdvanceResult:
        """Append newly observed sources and widen the current discovery window.

        ``discovery_as_of`` and ``discovery_boundary`` describe one completed
        discovery pass, not a lifetime fence.  The snapshot and exact source
        ledger move only forward.  Processing cursors remain untouched here;
        the worker advances those separately after terminal proposal work.
        """

        if discovery_as_of.tzinfo is None or discovery_as_of.utcoffset() is None:
            raise ValueError("discovery_as_of must include a timezone")
        if not isinstance(discovery_boundary, SourceCursor):
            raise TypeError("discovery_boundary must be a SourceCursor")
        if discovery_boundary.session_ended_at is None:
            raise ValueError("completed-session discovery boundary requires session_ended_at")
        candidates = tuple(discovered_sources)
        if any(not isinstance(source, SourceCursor) for source in candidates):
            raise TypeError("discovered_sources must contain SourceCursor values")

        with exclusive_file_lock(self._mutation_lock_path()):
            state = self.read()
            if state.schema_version != "1.1" or state.discovery_as_of is None:
                raise StateDiscoveryRegressionError(
                    "completed-session discovery progression requires schema 1.1 state"
                )
            if discovery_as_of < state.discovery_as_of:
                raise StateDiscoveryRegressionError(
                    "discovery_as_of cannot move backwards"
                )

            current_boundary_key = _cursor_order_key(state.discovery_boundary)
            next_boundary_key = _cursor_order_key(discovery_boundary)
            if current_boundary_key is None or next_boundary_key is None:
                raise StateDiscoveryRegressionError(
                    "completed-session discovery requires ordered source boundaries"
                )
            if next_boundary_key < current_boundary_key:
                raise StateDiscoveryRegressionError(
                    "discovery boundary cannot move backwards"
                )
            if discovery_boundary.session_ended_at >= discovery_as_of:
                raise ValueError(
                    "discovery boundary must end before discovery_as_of"
                )

            existing_by_identity = {
                _source_identity(source): source for source in state.discovered_sources
            }
            new_sources: list[SourceCursor] = []
            for source in candidates:
                source_key = _cursor_order_key(source)
                if source_key is None:
                    raise ValueError(
                        "completed-session discovered sources require session_ended_at"
                    )
                if source_key > next_boundary_key:
                    raise StateDiscoverySourceBindingError(
                        "discovered source exceeds the supplied discovery boundary"
                    )
                existing = existing_by_identity.get(_source_identity(source))
                if existing is not None:
                    if existing != source:
                        raise StateDiscoverySourceBindingError(
                            "discovered source identity changed its session end timestamp"
                        )
                    continue
                existing_by_identity[_source_identity(source)] = source
                new_sources.append(source)

            if (
                discovery_as_of == state.discovery_as_of
                and discovery_boundary == state.discovery_boundary
                and not new_sources
            ):
                return DiscoveryAdvanceResult(
                    state=state,
                    recorded=False,
                    duplicate_noop=True,
                )

            next_state = state.model_copy(deep=True)
            next_state.schema_version = "1.1"
            next_state.discovery_as_of = discovery_as_of
            next_state.discovery_boundary = discovery_boundary
            next_state.discovered_sources = sorted(
                (*state.discovered_sources, *new_sources),
                key=_cursor_order_key,
            )
            self._replace(next_state)
            return DiscoveryAdvanceResult(
                state=next_state,
                recorded=True,
                duplicate_noop=False,
            )

    def record_action(
        self,
        action: ProcessedAction,
        *,
        cursor: SourceCursor | None = None,
    ) -> ActionLedgerResult:
        with exclusive_file_lock(self._mutation_lock_path()):
            return self._record_action_unlocked(action, cursor=cursor)

    def _record_action_unlocked(
        self,
        action: ProcessedAction,
        *,
        cursor: SourceCursor | None = None,
    ) -> ActionLedgerResult:
        """Persist one terminal action, or replay it as a safe no-op.

        An exact stable-action replay does not rewrite the ledger or move the
        cursor. Reusing that ID with different source content, disposition, or
        proposal identity raises instead of overwriting the terminal record.
        """

        state = self.read()
        existing = state.processed_actions.get(action.action_id)
        if existing is not None:
            if existing != action:
                raise StateActionConflictError(
                    f"processed action {action.action_id!r} conflicts with its stored terminal record"
                )
            if cursor is not None:
                self._validate_cursor_advance(state, action, cursor)
            return ActionLedgerResult(
                state=state,
                recorded=False,
                duplicate_noop=True,
            )

        next_state = state.model_copy(deep=True)
        if cursor is not None:
            self._validate_cursor_advance(state, action, cursor)
            next_state.source_cursor = cursor
        next_state.processed_actions[action.action_id] = action
        self._replace(next_state)
        return ActionLedgerResult(
            state=next_state,
            recorded=True,
            duplicate_noop=False,
        )

    @staticmethod
    def _validate_cursor_advance(
        state: CaptureState,
        action: ProcessedAction,
        candidate: SourceCursor,
    ) -> None:
        if action.source_cursor is None or action.source_cursor != candidate:
            raise StateCursorSourceBindingError(
                "cursor advancement requires an action source_cursor matching the candidate"
            )
        if candidate not in state.discovered_sources:
            raise StateCursorSourceBindingError(
                "cursor advancement requires a discovered source identity"
            )
        boundary = state.discovery_boundary
        boundary_key = _cursor_order_key(boundary)
        candidate_key = _cursor_order_key(candidate)
        if boundary_key is None:
            if (
                candidate.session_id != boundary.session_id
                or candidate.last_user_message_id > boundary.last_user_message_id
            ):
                raise StateCursorSourceBindingError(
                    "cursor advancement cannot pass the discovery boundary"
                )
        elif candidate_key is None or candidate_key > boundary_key:
            raise StateCursorSourceBindingError(
                "cursor advancement cannot pass the discovery boundary"
            )
        current = state.source_cursor
        current_key = _cursor_order_key(current)
        if current_key is not None and candidate_key is not None:
            regressed = candidate_key < current_key
        elif current.session_id == candidate.session_id:
            regressed = candidate.last_user_message_id < current.last_user_message_id
        else:
            raise StateCursorRegressionError(
                "cross-session cursor comparison requires session_ended_at"
            )
        if regressed:
            raise StateCursorRegressionError(
                "a capture cursor cannot move backwards in session order"
            )

    def _replace(self, state: CaptureState) -> None:
        validated = parse_capture_state(state)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: str | None = None
        try:
            descriptor, temporary_path = tempfile.mkstemp(
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                dir=self.path.parent,
                text=True,
            )
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(
                    validated.model_dump(mode="json"),
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


def _watermark_key(watermark: CompletedSessionWatermark) -> tuple[datetime, str, int]:
    return completed_session_order_key(watermark)


def _source_identity(cursor: SourceCursor) -> tuple[str, int]:
    return (cursor.session_id, cursor.last_user_message_id)


def _cursor_order_key(cursor: SourceCursor) -> tuple[datetime, str, int] | None:
    if cursor.session_ended_at is None:
        return None
    return (
        cursor.session_ended_at,
        cursor.session_id,
        cursor.last_user_message_id,
    )
