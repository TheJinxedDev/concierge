"""Fixture-only synthetic execution bridge for the P5.4 proof.

This module is deliberately not imported by application startup, MCP startup, or
live cron execution. It lets a disposable scheduler test exercise the existing
synthetic tracer plus the P3 state/report stores without invoking an LLM or live
Hermes session source.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from .capture_enablement import CaptureEnablementState
from .capture_report import CaptureRunReportStore
from .capture_state import (
    BacklogState,
    CaptureRunReport,
    CaptureStats,
    LastRunSummary,
    LockOutcome,
    ProcessedAction,
    RunTerminalStatus,
    SourceCursor,
    TerminalDisposition,
    parse_capture_run_report,
    parse_capture_state,
)
from .capture_state_store import CaptureStateStore
from .cron_identity import JobOwnership, PackageOwnedJobSpec, classify_job_record
from .library_service import LibraryService
from .synthetic_tracer import trace_synthetic_session


@dataclass(frozen=True)
class SyntheticExecutionEvidence:
    """Complete readback packet returned by one synthetic owned-job run."""

    job_id: str
    run_id: str
    tracer_result: dict[str, Any]
    report: CaptureRunReport
    state: Any
    report_path: Any
    state_path: Any
    pending_proposals: list[dict[str, Any]]
    canonical_before: list[dict[str, Any]]
    canonical_after: list[dict[str, Any]]


def execute_synthetic_capture_job(
    *,
    job: Mapping[str, Any],
    spec: PackageOwnedJobSpec,
    enablement: CaptureEnablementState,
    library: LibraryService,
    catalog_payload: object,
    state_store: CaptureStateStore,
    report_store: CaptureRunReportStore,
    run_id: str,
    now: datetime,
) -> SyntheticExecutionEvidence:
    """Run one bounded synthetic capture pass and read back every artifact."""

    job_id = job.get("id")
    if not isinstance(job_id, str) or not job_id.strip():
        raise ValueError("synthetic execution requires a nonblank scheduler job ID")
    if classify_job_record(job, spec) is not JobOwnership.EXACT:
        raise ValueError("synthetic execution requires an exact package-owned job")
    if not enablement.is_enabled:
        raise ValueError("synthetic execution requires enabled capture consent")
    decision = enablement.current_decision
    assert decision is not None
    if decision.delivery_target is not spec.delivery_target:
        raise ValueError("synthetic execution delivery disagrees with enablement")
    if decision.schedule != spec.schedule:
        raise ValueError("synthetic execution schedule disagrees with enablement")
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("synthetic execution timestamp must include a timezone")

    canonical_before = [
        item.model_dump(mode="json") for item in library.list_media_items()
    ]
    tracer_result = trace_synthetic_session(library, catalog_payload)
    tracer_report = tracer_result.get("run_report")
    if not isinstance(tracer_report, dict):
        raise ValueError("synthetic tracer did not return a run report")
    if tracer_report.get("status") != "complete":
        raise ValueError("synthetic tracer did not complete")
    if tracer_report.get("canonical_media_changed") is not False:
        raise ValueError("synthetic tracer reported canonical media mutation")

    cases = _catalog_cases(catalog_payload)
    session_ref = _required_text(tracer_result, "session_ref")
    start_cursor = SourceCursor(session_id=session_ref, last_user_message_id=0)
    actions = _processed_actions(cases, tracer_result, now, session_ref)
    end_cursor = SourceCursor(
        session_id=session_ref,
        last_user_message_id=max(_message_number(case["message_ref"]) for case in cases),
    )
    discovery_boundary = end_cursor

    initial_state = parse_capture_state(
        {
            "schema_version": "1.0",
            "discovery_boundary": discovery_boundary.model_dump(mode="json"),
            "discovered_sources": [
                {
                    "session_id": session_ref,
                    "last_user_message_id": _message_number(case["message_ref"]),
                }
                for case in cases
            ],
            "source_cursor": start_cursor.model_dump(mode="json"),
            "processed_actions": {},
            "claims": {},
            "lock": None,
            "backlog": {"remaining": False, "reason": None},
            "batches_completed": 0,
            "stats": {
                "reviewed_sessions": 0,
                "reviewed_messages": 0,
                "source_characters": 0,
                "proposals_submitted": 0,
                "duplicate_noops": 0,
            },
            "errors": [],
            "last_run": None,
        }
    )
    state_store.create(initial_state)
    for action, case in zip(actions, cases, strict=True):
        state_store.record_action(
            action,
            cursor=SourceCursor(
                session_id=session_ref,
                last_user_message_id=_message_number(case["message_ref"]),
            ),
        )

    source_characters = sum(len(_required_text(case, "quoted_evidence")) for case in cases)
    durable_discovered_sources = state_store.read().discovered_sources
    final_stats = CaptureStats(
        reviewed_sessions=1,
        reviewed_messages=len(cases),
        source_characters=source_characters,
        proposals_submitted=int(tracer_report.get("submitted_count", 0)),
        duplicate_noops=int(tracer_report.get("replayed_count", 0)),
    )
    final_state = state_store.read().model_copy(
        update={
            "batches_completed": 1,
            "stats": final_stats,
            "canonical_media_changed": False,
            "last_run": LastRunSummary(
                run_id=run_id,
                started_at=now,
                discovery_boundary=discovery_boundary,
                source_cursor_start=start_cursor,
                terminal_status=RunTerminalStatus.COMPLETE,
                finished_at=now,
                source_cursor=end_cursor,
                batch_count=1,
                reviewed_sessions=1,
                reviewed_messages=len(cases),
                source_characters=source_characters,
                action_ids=[action.action_id for action in actions],
                claim_ids=[],
                durable_action_ids=[action.action_id for action in actions],
                durable_claim_ids=[],
                durable_batches_completed=1,
                durable_stats=final_stats,
                durable_discovered_sources=durable_discovered_sources,
                durable_errors=[],
                proposal_ids=[],
                reason_codes=[],
                backlog=BacklogState(remaining=False),
                backlog_remaining=False,
                lock_outcome=LockOutcome.RELEASED,
                lock=None,
                canonical_media_changed=False,
                retryable_claim_ids=[],
                errors=[],
            ),
        }
    )
    state_store.replace(final_state)
    state_readback = state_store.read()

    proposal_ids = [
        _required_text(receipt.get("proposal"), "id")
        for receipt in _receipts(tracer_result)
    ]
    report = parse_capture_run_report(
        CaptureRunReport(
            schema_version="1.0",
            run_id=run_id,
            started_at=now,
            finished_at=now,
            discovery_boundary=discovery_boundary,
            source_cursor_start=start_cursor,
            source_cursor_end=end_cursor,
            batch_count=1,
            reviewed_sessions=1,
            reviewed_messages=len(cases),
            source_characters=source_characters,
            claims=[],
            actions=actions,
            durable_action_ids=[action.action_id for action in actions],
            durable_claim_ids=[],
            durable_batches_completed=1,
            durable_stats=final_stats,
            durable_discovered_sources=durable_discovered_sources,
            durable_errors=[],
            proposal_ids=proposal_ids,
            terminal_status=RunTerminalStatus.COMPLETE,
            reason_codes=[],
            backlog=BacklogState(remaining=False),
            lock_outcome=LockOutcome.RELEASED,
            state_path=str(state_store.path),
            canonical_media_changed=False,
            retryable_claim_ids=[],
            errors=[],
        )
    )
    durable_state = state_store.read()
    state_store.replace(
        durable_state.model_copy(
            update={
                "last_run": LastRunSummary(
                    run_id=report.run_id,
                    started_at=report.started_at,
                    discovery_boundary=report.discovery_boundary,
                    source_cursor_start=report.source_cursor_start,
                    terminal_status=report.terminal_status,
                    finished_at=report.finished_at,
                    source_cursor=report.source_cursor_end,
                    batch_count=report.batch_count,
                    reviewed_sessions=report.reviewed_sessions,
                    reviewed_messages=report.reviewed_messages,
                    source_characters=report.source_characters,
                    action_ids=[action.action_id for action in report.actions],
                    claim_ids=[claim.claim_id for claim in report.claims],
                    durable_action_ids=sorted(state_store.read().processed_actions),
                    durable_claim_ids=sorted(state_store.read().claims),
                    durable_batches_completed=durable_state.batches_completed,
                    durable_stats=durable_state.stats,
                    durable_discovered_sources=durable_state.discovered_sources,
                    durable_errors=durable_state.errors,
                    proposal_ids=report.proposal_ids,
                    reason_codes=report.reason_codes,
                    backlog=report.backlog,
                    backlog_remaining=report.backlog.remaining,
                    lock_outcome=report.lock_outcome,
                    lock=report.lock,
                    canonical_media_changed=report.canonical_media_changed,
                    retryable_claim_ids=report.retryable_claim_ids,
                    errors=report.errors,
                )
            }
        )
    )
    state_readback = state_store.read()
    report_write = report_store.write(report, state_store=state_store)
    report_readback = report_store.read(run_id)
    if report_readback != report:
        raise ValueError("synthetic run report failed exact readback")

    pending_proposals = [
        proposal.model_dump(mode="json") for proposal in library.list_proposals()
    ]
    canonical_after = [
        item.model_dump(mode="json") for item in library.list_media_items()
    ]
    if canonical_after != canonical_before:
        raise ValueError("synthetic execution changed canonical media")
    if len(pending_proposals) != len(proposal_ids):
        raise ValueError("synthetic execution proposal readback count disagrees")

    return SyntheticExecutionEvidence(
        job_id=job_id,
        run_id=run_id,
        tracer_result=tracer_result,
        report=report_readback,
        state=state_readback,
        report_path=report_write.report_path,
        state_path=state_store.path,
        pending_proposals=pending_proposals,
        canonical_before=canonical_before,
        canonical_after=canonical_after,
    )


def _catalog_cases(payload: object) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("cases"), list):
        raise ValueError("synthetic execution requires a catalog case list")
    cases = payload["cases"]
    if not cases or any(not isinstance(case, dict) for case in cases):
        raise ValueError("synthetic execution cases must be nonempty objects")
    return cases


def _receipts(result: Mapping[str, Any]) -> list[dict[str, Any]]:
    receipts = result.get("receipts")
    if not isinstance(receipts, list) or any(not isinstance(item, dict) for item in receipts):
        raise ValueError("synthetic tracer receipts must be a list of objects")
    return receipts


def _processed_actions(
    cases: list[dict[str, Any]],
    result: Mapping[str, Any],
    now: datetime,
    session_ref: str,
) -> list[ProcessedAction]:
    receipts = _receipts(result)
    if len(receipts) != len(cases):
        raise ValueError("synthetic tracer receipt count disagrees with source cases")
    actions: list[ProcessedAction] = []
    for case, receipt in zip(cases, receipts, strict=True):
        proposal = receipt.get("proposal")
        proposal_id = _required_text(proposal, "id")
        actions.append(
            ProcessedAction(
                action_id=f"capture-action-{_required_text(case, 'case_id')}",
                source_ref=_required_text(case, "source_ref"),
                source_cursor=SourceCursor(
                    session_id=session_ref,
                    last_user_message_id=_message_number(case["message_ref"]),
                ),
                content_hash=_required_text(case, "content_hash"),
                disposition=TerminalDisposition.PROPOSED_SUCCESSFULLY,
                processed_at=now,
                proposal_id=proposal_id,
            )
        )
    return actions


def _message_number(value: object) -> int:
    text = value if isinstance(value, str) else ""
    prefix, separator, suffix = text.rpartition("-")
    if not separator or not prefix or not suffix.isdigit():
        raise ValueError("synthetic message_ref must end in a numeric suffix")
    return int(suffix)


def _required_text(payload: object, field: str) -> str:
    if not isinstance(payload, Mapping):
        raise ValueError(f"synthetic payload for {field!r} must be an object")
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"synthetic payload field {field!r} must be nonblank text")
    return value
