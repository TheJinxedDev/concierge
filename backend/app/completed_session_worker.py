"""Injected, review-only completed-session worker orchestration.

This module composes the completed-session adapter, selector, bounded batch
planner, claim ledger, and caller-owned state/report stores. It never opens a
Hermes store, schedules a job, calls an LLM, promotes a proposal, or mutates the
canonical library. The proposal submitter is an explicit injected seam.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
from typing import Any, Mapping

from .capture_batch import (
    BatchLimits,
    BatchPlan,
    BatchSource,
    FailureKind,
    plan_batch,
    resolve_failure,
)
from .capture_claims import CaptureClaimLedger, CaptureLockManager
from .capture_report import CaptureRunReportStore
from .capture_source import hash_source_content
from .capture_state import (
    BacklogReason,
    BacklogState,
    CaptureRunReport,
    CaptureStats,
    ClaimStatus,
    LastRunSummary,
    LockOutcome,
    ProcessedAction,
    ReasonCode,
    RunTerminalStatus,
    SourceCursor,
    TerminalDisposition,
)
from .capture_state_store import CaptureStateStore
from .completed_session_source import (
    CompletedSessionBatch,
    CompletedSessionSnapshot,
    CompletedSessionWatermark,
    HermesCompletedSessionSourceAdapter,
    select_completed_sessions,
)


class WorkerContractError(ValueError):
    """The caller supplied a state, plan, or receipt outside this seam."""


class WorkerFailure(RuntimeError):
    """A submitter failure with an explicit claim/cursor disposition."""

    def __init__(self, failure_kind: FailureKind | str, message: str) -> None:
        self.failure_kind = (
            failure_kind
            if isinstance(failure_kind, FailureKind)
            else FailureKind(failure_kind)
        )
        super().__init__(message)


@dataclass(frozen=True)
class WorkerSubmission:
    """Review-only receipt returned by the injected proposal submitter."""

    proposal_id: str
    result_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.proposal_id, str) or not self.proposal_id.strip():
            raise ValueError("proposal_id must be nonblank text")
        if not isinstance(self.result_id, str) or not self.result_id.strip():
            raise ValueError("result_id must be nonblank text")


@dataclass(frozen=True)
class CompletedSessionWorkItem:
    """One user-message source item prepared from a completed-session snapshot."""

    session_id: str
    message_id: int
    content: str
    cursor: SourceCursor
    source_ref: str
    content_hash: str
    source_characters: int


@dataclass(frozen=True)
class CompletedSessionWorkerPlan:
    """Pure adapter/selector/batch output consumed by the worker runner."""

    batch: CompletedSessionBatch
    batch_plan: BatchPlan
    items: tuple[CompletedSessionWorkItem, ...]
    next_watermark: CompletedSessionWatermark | None


@dataclass(frozen=True)
class CompletedSessionWorkerResult:
    """Verified readback from one injected worker run."""

    plan: CompletedSessionWorkerPlan
    report: CaptureRunReport
    state: Any
    report_write: Any
    claim_ids: tuple[str, ...]
    submitted_count: int
    replayed_count: int


def prepare_completed_session_work(
    adapter: HermesCompletedSessionSourceAdapter,
    *,
    as_of: datetime,
    watermark: CompletedSessionWatermark | None,
    limits: BatchLimits | None = None,
    include_archived: bool = False,
    snapshots: tuple[CompletedSessionSnapshot, ...] | None = None,
) -> CompletedSessionWorkerPlan:
    """Compose the injected source adapter, selector, and pure batch planner."""

    batch = select_completed_sessions(
        adapter.read_snapshots() if snapshots is None else snapshots,
        as_of=as_of,
        watermark=watermark,
        include_archived=include_archived,
    )
    items: list[CompletedSessionWorkItem] = []
    batch_sources: list[BatchSource] = []
    for session in batch.sessions:
        if session.ended_at is None:
            raise WorkerContractError("selected completed sessions require ended_at")
        for message in session.messages:
            if message.role != "user":
                continue
            content = message.content or ""
            source_ref = f"hermes://session/{session.session_id}/message/{message.message_id}"
            cursor = SourceCursor(
                session_id=session.session_id,
                last_user_message_id=message.message_id,
                session_ended_at=session.ended_at,
            )
            item = CompletedSessionWorkItem(
                session_id=session.session_id,
                message_id=message.message_id,
                content=content,
                cursor=cursor,
                source_ref=source_ref,
                content_hash=hash_source_content(content),
                source_characters=len(content),
            )
            items.append(item)
            batch_sources.append(
                BatchSource(
                    cursor=cursor,
                    source_ref=source_ref,
                    source_characters=len(content),
                )
            )

    planned, planned_items = _plan_item_prefix(items, batch_sources, limits=limits)
    next_watermark = None
    if planned_items:
        last_item = planned_items[-1]
        if last_item.cursor.session_ended_at is None:
            raise WorkerContractError("planned cursor requires session_ended_at")
        next_watermark = CompletedSessionWatermark(
            session_id=last_item.cursor.session_id,
            session_ended_at=last_item.cursor.session_ended_at,
            last_user_message_id=last_item.cursor.last_user_message_id,
        )
    return CompletedSessionWorkerPlan(
        batch=batch,
        batch_plan=planned,
        items=planned_items,
        next_watermark=next_watermark,
    )


def run_completed_session_worker(
    adapter: HermesCompletedSessionSourceAdapter,
    *,
    state_store: CaptureStateStore,
    lock_manager: CaptureLockManager,
    report_store: CaptureRunReportStore,
    run_id: str,
    owner_token: str,
    now: datetime,
    proposal_submitter: Callable[[CompletedSessionWorkItem], WorkerSubmission],
    as_of: datetime | None = None,
    limits: BatchLimits | None = None,
    include_archived: bool = False,
    progress_discovery: bool = False,
    lock_ttl: timedelta = timedelta(minutes=5),
    claim_ttl: timedelta = timedelta(minutes=2),
) -> CompletedSessionWorkerResult:
    """Run one bounded, review-only completed-session pass.

    State must already contain a fixed schema-1.1 discovery boundary and
    ``discovery_as_of``. With ``progress_discovery=True`` the supplied ``as_of``
    opens one newer monotonic discovery window and appends newly visible source
    cursors before planning work. The source-processing cursor and completed-
    session watermark still advance only after terminal proposal work. Any
    retryable or blocked claim leaves those processing frontiers unchanged.
    """

    if lock_manager.state_store is not state_store:
        raise WorkerContractError("lock_manager must use the supplied state_store")
    if now.tzinfo is None or now.utcoffset() is None:
        raise WorkerContractError("worker now must include a timezone")
    if not isinstance(run_id, str) or not run_id.strip():
        raise WorkerContractError("run_id must be nonblank text")
    if not isinstance(owner_token, str) or not owner_token.strip():
        raise WorkerContractError("owner_token must be nonblank text")
    if lock_ttl <= timedelta(0) or claim_ttl <= timedelta(0):
        raise WorkerContractError("lock and claim TTLs must be positive")

    initial_state = state_store.read()
    durable_as_of = initial_state.discovery_as_of
    if durable_as_of is None:
        raise WorkerContractError("worker requires a fixed discovery_as_of")
    if progress_discovery:
        if as_of is None:
            raise WorkerContractError(
                "progressive discovery requires an explicit as_of snapshot"
            )
        discovery_as_of = as_of
    else:
        discovery_as_of = durable_as_of
        if as_of is not None and as_of != durable_as_of:
            raise WorkerContractError(
                "worker as_of disagrees with durable discovery_as_of"
            )

    lock_result = lock_manager.acquire(
        run_id,
        owner_token,
        now=now,
        ttl=lock_ttl,
    )
    if lock_result.outcome is LockOutcome.SKIPPED:
        raise WorkerContractError("worker lock is owned by another active run")

    claim_ledger = CaptureClaimLedger(state_store, lock_manager)
    start_cursor = initial_state.source_cursor
    actions: list[ProcessedAction] = []
    claim_ids: list[str] = []
    proposal_ids: list[str] = []
    reason_codes: list[ReasonCode] = []
    errors: list[str] = []
    retryable_claim_ids: list[str] = []
    submitted_count = 0
    replayed_count = 0
    attempted_items: list[CompletedSessionWorkItem] = []
    held = False
    plan: CompletedSessionWorkerPlan

    try:
        state_before_discovery = state_store.read()
        snapshots = adapter.read_snapshots()
        if progress_discovery:
            discovery_batch = select_completed_sessions(
                snapshots,
                as_of=discovery_as_of,
                watermark=state_before_discovery.completed_session_watermark,
                include_archived=include_archived,
            )
            newly_discovered = _source_cursors(discovery_batch)
            discovery_boundary = _max_cursor(
                state_before_discovery.discovery_boundary,
                newly_discovered,
            )
            state_store.advance_completed_session_discovery(
                discovery_as_of=discovery_as_of,
                discovery_boundary=discovery_boundary,
                discovered_sources=newly_discovered,
            )
            state_before_discovery = state_store.read()

        plan = prepare_completed_session_work(
            adapter,
            as_of=discovery_as_of,
            watermark=state_before_discovery.completed_session_watermark,
            limits=limits,
            include_archived=include_archived,
            snapshots=snapshots,
        )
        _require_discovered_sources(state_before_discovery.discovered_sources, plan.items)

        for item in plan.items:
            attempted_items.append(item)
            claim_id = _claim_id(item)
            action_id = _action_id(claim_id)
            claim_result = claim_ledger.claim_action(
                claim_id,
                source_ref=item.source_ref,
                content_hash=item.content_hash,
                owner_run_id=run_id,
                owner_token=owner_token,
                now=now,
                expires_at=now + claim_ttl,
            )

            if claim_result.claim.status is ClaimStatus.TERMINAL:
                existing_action = state_store.read().processed_actions.get(action_id)
                if existing_action is None:
                    raise WorkerContractError(
                        f"terminal claim {claim_id!r} has no matching processed action"
                    )
                actions.append(existing_action)
                if existing_action.proposal_id is not None:
                    proposal_ids.append(existing_action.proposal_id)
                replayed_count += 1
                continue
            claim_ids.append(claim_id)

            try:
                submission = _coerce_submission(proposal_submitter(item))
            except WorkerFailure as failure:
                decision = resolve_failure(failure.failure_kind)
                failed = claim_ledger.fail_claim(
                    claim_id,
                    owner_run_id=run_id,
                    owner_token=owner_token,
                    now=now,
                    decision=decision,
                )
                if failed.claim.status is ClaimStatus.TERMINAL:
                    disposition = decision.disposition
                    assert disposition is not None
                    action = ProcessedAction(
                        action_id=action_id,
                        source_ref=item.source_ref,
                        source_cursor=item.cursor,
                        content_hash=item.content_hash,
                        disposition=disposition,
                        processed_at=now,
                        reason_code=_reason_for_failure(failure.failure_kind),
                    )
                    state_store.record_action(action)
                    actions.append(action)
                    reason_codes.append(_reason_for_failure(failure.failure_kind))
                    continue

                held = True
                retryable_claim_ids.append(claim_id)
                reason_code = _reason_for_failure(failure.failure_kind)
                reason_codes.append(reason_code)
                errors.append(str(failure))
                break

            claim_ledger.complete_claim(
                claim_id,
                source_ref=item.source_ref,
                content_hash=item.content_hash,
                owner_run_id=run_id,
                owner_token=owner_token,
                now=now,
                disposition=TerminalDisposition.PROPOSED_SUCCESSFULLY,
                result_id=submission.result_id,
                proposal_id=submission.proposal_id,
            )
            action = ProcessedAction(
                action_id=action_id,
                source_ref=item.source_ref,
                source_cursor=item.cursor,
                content_hash=item.content_hash,
                disposition=TerminalDisposition.PROPOSED_SUCCESSFULLY,
                processed_at=now,
                proposal_id=submission.proposal_id,
            )
            state_store.record_action(action)
            actions.append(action)
            proposal_ids.append(submission.proposal_id)
            submitted_count += 1

        all_planned_items_terminal = bool(plan.items) and not held and len(actions) == len(plan.items)
        if all_planned_items_terminal:
            state = state_store.read()
            advanced_state = state.model_copy(deep=True)
            advanced_state.source_cursor = plan.items[-1].cursor
            state_store.replace(advanced_state)
            if plan.next_watermark is not None:
                state_store.advance_completed_session_watermark(
                    plan.next_watermark,
                    run_id=run_id,
                )
    finally:
        release_result = lock_manager.release(run_id, owner_token)
        if release_result.outcome is not LockOutcome.RELEASED:
            raise WorkerContractError(
                f"worker lock release failed: {release_result.error or release_result.outcome.value}"
            )

    state_after = state_store.read()
    run_stats = CaptureStats(
        reviewed_sessions=len({item.session_id for item in attempted_items}),
        reviewed_messages=len(attempted_items),
        source_characters=sum(item.source_characters for item in attempted_items),
        proposals_submitted=submitted_count,
        duplicate_noops=replayed_count,
    )
    cumulative_stats = CaptureStats(
        reviewed_sessions=state_after.stats.reviewed_sessions + run_stats.reviewed_sessions,
        reviewed_messages=state_after.stats.reviewed_messages + run_stats.reviewed_messages,
        source_characters=state_after.stats.source_characters + run_stats.source_characters,
        proposals_submitted=state_after.stats.proposals_submitted + run_stats.proposals_submitted,
        duplicate_noops=state_after.stats.duplicate_noops + run_stats.duplicate_noops,
    )

    backlog = _backlog_state(plan, held, reason_codes)
    terminal_status = _terminal_status(plan, held)
    next_errors = [*state_after.errors, *errors]
    run_batch_count = 1 if plan.items else 0
    durable_batches = state_after.batches_completed + run_batch_count
    current_claims = [state_after.claims[claim_id] for claim_id in claim_ids]
    durable_action_ids = sorted(state_after.processed_actions)
    durable_claim_ids = sorted(state_after.claims)
    last_run = LastRunSummary(
        run_id=run_id,
        started_at=now,
        discovery_boundary=state_after.discovery_boundary,
        discovery_as_of=state_after.discovery_as_of,
        source_cursor_start=start_cursor,
        terminal_status=terminal_status,
        finished_at=now,
        source_cursor=state_after.source_cursor,
        completed_session_watermark=state_after.completed_session_watermark,
        batch_count=run_batch_count,
        reviewed_sessions=run_stats.reviewed_sessions,
        reviewed_messages=run_stats.reviewed_messages,
        source_characters=run_stats.source_characters,
        action_ids=[action.action_id for action in actions],
        claim_ids=claim_ids,
        durable_action_ids=durable_action_ids,
        durable_claim_ids=durable_claim_ids,
        durable_batches_completed=durable_batches,
        durable_stats=cumulative_stats,
        durable_discovered_sources=state_after.discovered_sources,
        durable_errors=next_errors,
        proposal_ids=proposal_ids,
        reason_codes=reason_codes or ([ReasonCode.NO_VISIBLE_EVIDENCE] if not plan.items else []),
        backlog=backlog,
        backlog_remaining=backlog.remaining,
        lock_outcome=LockOutcome.RELEASED,
        lock=None,
        canonical_media_changed=False,
        retryable_claim_ids=retryable_claim_ids,
        errors=errors,
    )
    final_state = state_after.model_copy(
        update={
            "batches_completed": durable_batches,
            "stats": cumulative_stats,
            "backlog": backlog,
            "errors": next_errors,
            "canonical_media_changed": False,
            "last_run": last_run,
        }
    )
    state_store.replace(final_state)
    final_state = state_store.read()

    report = CaptureRunReport(
        schema_version=final_state.schema_version,
        run_id=run_id,
        started_at=now,
        finished_at=now,
        discovery_boundary=final_state.discovery_boundary,
        discovery_as_of=final_state.discovery_as_of,
        source_cursor_start=start_cursor,
        source_cursor_end=final_state.source_cursor,
        completed_session_watermark=final_state.completed_session_watermark,
        batch_count=run_batch_count,
        reviewed_sessions=run_stats.reviewed_sessions,
        reviewed_messages=run_stats.reviewed_messages,
        source_characters=run_stats.source_characters,
        claims=current_claims,
        actions=actions,
        durable_action_ids=durable_action_ids,
        durable_claim_ids=durable_claim_ids,
        durable_batches_completed=final_state.batches_completed,
        durable_stats=final_state.stats,
        durable_discovered_sources=final_state.discovered_sources,
        durable_errors=final_state.errors,
        proposal_ids=proposal_ids,
        terminal_status=terminal_status,
        reason_codes=last_run.reason_codes,
        backlog=backlog,
        lock_outcome=LockOutcome.RELEASED,
        lock=None,
        state_path=str(state_store.path),
        canonical_media_changed=False,
        retryable_claim_ids=retryable_claim_ids,
        errors=errors,
    )
    report_write = report_store.write(report, state_store=state_store)
    report_readback = report_store.read(run_id)
    if report_readback != report:
        raise WorkerContractError("worker report failed exact readback")
    return CompletedSessionWorkerResult(
        plan=plan,
        report=report_readback,
        state=final_state,
        report_write=report_write,
        claim_ids=tuple(claim_ids),
        submitted_count=submitted_count,
        replayed_count=replayed_count,
    )


def _plan_item_prefix(
    items: list[CompletedSessionWorkItem],
    sources: list[BatchSource],
    *,
    limits: BatchLimits | None,
) -> tuple[BatchPlan, tuple[CompletedSessionWorkItem, ...]]:
    selected_sources_plan = plan_batch(sources, limits=limits)
    selected_refs = {source.source_ref for source in selected_sources_plan.items}
    selected_items = [item for item in items if item.source_ref in selected_refs]
    return BatchPlan(
        items=selected_sources_plan.items,
        completed_sessions=selected_sources_plan.completed_sessions,
        source_characters=selected_sources_plan.source_characters,
        backlog_remaining=selected_sources_plan.backlog_remaining,
        backlog_reason=selected_sources_plan.backlog_reason,
    ), tuple(selected_items)


def _source_cursors(batch: CompletedSessionBatch) -> list[SourceCursor]:
    sources: list[SourceCursor] = []
    for session in batch.sessions:
        if session.ended_at is None:
            raise WorkerContractError("discovered completed sessions require ended_at")
        sources.extend(
            SourceCursor(
                session_id=session.session_id,
                last_user_message_id=message.message_id,
                session_ended_at=session.ended_at,
            )
            for message in session.messages
            if message.role == "user"
        )
    return sources


def _max_cursor(current: SourceCursor, candidates: list[SourceCursor]) -> SourceCursor:
    if current.session_ended_at is None:
        raise WorkerContractError("progressive discovery requires an ordered boundary")
    return max(
        (current, *candidates),
        key=lambda cursor: (
            cursor.session_ended_at,
            cursor.session_id,
            cursor.last_user_message_id,
        ),
    )


def _require_discovered_sources(
    discovered_sources: list[SourceCursor],
    items: tuple[CompletedSessionWorkItem, ...],
) -> None:
    discovered = {
        (
            cursor.session_id,
            cursor.session_ended_at,
            cursor.last_user_message_id,
        )
        for cursor in discovered_sources
    }
    missing = [
        item.source_ref
        for item in items
        if (
            item.cursor.session_id,
            item.cursor.session_ended_at,
            item.cursor.last_user_message_id,
        )
        not in discovered
    ]
    if missing:
        raise WorkerContractError(
            "worker plan contains sources outside durable discovery ledger: "
            + ", ".join(missing)
        )


def _claim_id(item: CompletedSessionWorkItem) -> str:
    digest = hashlib.sha256(
        f"{item.source_ref}\n{item.content_hash}".encode("utf-8")
    ).hexdigest()
    return f"completed-session-claim-{digest}"


def _action_id(claim_id: str) -> str:
    return f"completed-session-action-{claim_id.removeprefix('completed-session-claim-')}"


def _coerce_submission(value: object) -> WorkerSubmission:
    if isinstance(value, WorkerSubmission):
        return value
    if isinstance(value, Mapping):
        return WorkerSubmission(
            proposal_id=value.get("proposal_id"),
            result_id=value.get("result_id"),
        )
    raise WorkerFailure(FailureKind.CONTRACT, "submitter returned an invalid receipt")


def _reason_for_failure(kind: FailureKind) -> ReasonCode:
    return {
        FailureKind.AMBIGUOUS: ReasonCode.AMBIGUOUS_IDENTITY,
        FailureKind.UNREADABLE: ReasonCode.SOURCE_UNREADABLE,
        FailureKind.CHANGED_SOURCE: ReasonCode.SOURCE_CHANGED_AFTER_DISCOVERY,
        FailureKind.UNKNOWN_COMMIT: ReasonCode.UNKNOWN_COMMIT_OUTCOME,
        FailureKind.CONTRACT: ReasonCode.CONTRACT_MISMATCH,
        FailureKind.PERMANENT: ReasonCode.CONTRACT_MISMATCH,
        FailureKind.TRANSIENT: ReasonCode.MCP_UNAVAILABLE,
        FailureKind.INTERRUPTED: ReasonCode.MCP_UNAVAILABLE,
    }[kind]


def _backlog_state(
    plan: CompletedSessionWorkerPlan,
    held: bool,
    reason_codes: list[ReasonCode],
) -> BacklogState:
    if held:
        reason = (
            BacklogReason.SOURCE_BLOCKED
            if ReasonCode.UNKNOWN_COMMIT_OUTCOME in reason_codes
            else BacklogReason.RETRY_PENDING
        )
        return BacklogState(remaining=True, reason=reason)
    if plan.batch_plan.backlog_remaining:
        return BacklogState(remaining=True, reason=BacklogReason.BATCH_LIMIT)
    return BacklogState(remaining=False)


def _terminal_status(
    plan: CompletedSessionWorkerPlan,
    held: bool,
) -> RunTerminalStatus:
    if held:
        return (
            RunTerminalStatus.BLOCKED
            if plan.items
            else RunTerminalStatus.PARTIAL
        )
    if not plan.items:
        return RunTerminalStatus.NO_VISIBLE_EVIDENCE
    if plan.batch_plan.backlog_remaining:
        return RunTerminalStatus.PARTIAL
    return RunTerminalStatus.COMPLETE
