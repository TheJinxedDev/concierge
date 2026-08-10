"""Atomic local persistence for per-invocation capture run reports."""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
import json
import os
from pathlib import Path
import tempfile
from typing import Any
from urllib.parse import quote

from .capture_state import CaptureRunReport, CaptureStats, parse_capture_run_report
from .file_lock import exclusive_file_lock


class ReportStoreError(RuntimeError):
    """Base error for invalid or unsafe local report persistence."""


class ReportIdentityConflictError(ReportStoreError):
    """A run ID was reused with a different report payload."""


class ReportStateBindingError(ReportStoreError):
    """A complete or cursor-moving report lacks matching durable state."""


@dataclass(frozen=True)
class ReportWriteResult:
    report_path: Path
    report: CaptureRunReport
    written: bool
    replayed: bool
    delivery_marker: str


class CaptureRunReportStore:
    """Persist one validated report file per run ID under a caller path."""

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)

    def path_for(self, run_id: str) -> Path:
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("run_id must be nonblank text")
        encoded_run_id = quote(run_id, safe="-_.")
        return self.directory / f"capture-run-{encoded_run_id}.json"

    def read(self, run_id: str) -> CaptureRunReport:
        path = self.path_for(run_id)
        payload = json.loads(path.read_text(encoding="utf-8"))
        return parse_capture_run_report(payload)

    def write(
        self,
        report: CaptureRunReport,
        *,
        delivery_silent: bool = False,
        state_store: Any | None = None,
    ) -> ReportWriteResult:
        """Write or replay one report without overwriting a different payload."""

        validated = parse_capture_run_report(report)
        requires_binding = (
            validated.terminal_status.value == "complete"
            or validated.source_cursor_end != validated.source_cursor_start
            or validated.completed_session_watermark is not None
        )
        if requires_binding and state_store is None:
            raise ReportStateBindingError(
                "complete or cursor-moving reports require durable state binding"
            )
        path = self.path_for(validated.run_id)
        state_context = (
            state_store.mutation_guard()
            if state_store is not None
            else nullcontext()
        )
        with state_context:
            if state_store is not None:
                self._validate_state_binding(validated, state_store)
            with exclusive_file_lock(self._mutation_lock_path(path)):
                if path.exists():
                    existing = self.read(validated.run_id)
                    if existing != validated:
                        raise ReportIdentityConflictError(
                            f"run report {validated.run_id!r} conflicts with its stored payload"
                        )
                    return ReportWriteResult(
                        report_path=path,
                        report=existing,
                        written=False,
                        replayed=True,
                        delivery_marker="[SILENT]" if delivery_silent else "REPORT_REPLAYED",
                    )
                self._write_atomic(path, validated)
        return ReportWriteResult(
            report_path=path,
            report=validated,
            written=True,
            replayed=False,
            delivery_marker="[SILENT]" if delivery_silent else "REPORT_WRITTEN",
        )

    @staticmethod
    def _validate_state_binding(report: CaptureRunReport, state_store: Any) -> None:
        state = state_store.read()
        try:
            report_state_path = Path(report.state_path).expanduser().resolve(strict=False)
            durable_state_path = Path(state_store.path).expanduser().resolve(strict=False)
        except (OSError, RuntimeError) as error:
            raise ReportStateBindingError("report state path cannot be resolved") from error
        if report_state_path != durable_state_path:
            raise ReportStateBindingError("report state_path does not match durable state")
        if state.discovery_boundary != report.discovery_boundary:
            raise ReportStateBindingError("report discovery boundary disagrees with durable state")
        if state.discovery_as_of != report.discovery_as_of:
            raise ReportStateBindingError("report discovery_as_of disagrees with durable state")
        if state.source_cursor != report.source_cursor_end:
            raise ReportStateBindingError("report source cursor does not match durable state")
        if state.completed_session_watermark != report.completed_session_watermark:
            raise ReportStateBindingError(
                "report completed-session watermark does not match durable state"
            )
        if state.backlog != report.backlog:
            raise ReportStateBindingError("report backlog state does not match durable state")
        if state.canonical_media_changed != report.canonical_media_changed:
            raise ReportStateBindingError("report canonical mutation status disagrees with durable state")
        if state.last_run is None:
            raise ReportStateBindingError("durable state has no matching last run")
        last_run = state.last_run
        if last_run.run_id != report.run_id:
            raise ReportStateBindingError("report run ID does not match durable last run")
        if last_run.started_at != report.started_at:
            raise ReportStateBindingError("report start time disagrees with durable last run")
        if last_run.discovery_boundary != report.discovery_boundary:
            raise ReportStateBindingError("report discovery boundary disagrees with durable last run")
        if last_run.discovery_as_of != report.discovery_as_of:
            raise ReportStateBindingError("report discovery_as_of disagrees with durable last run")
        if last_run.source_cursor_start != report.source_cursor_start:
            raise ReportStateBindingError("report starting cursor disagrees with durable last run")
        if last_run.terminal_status != report.terminal_status:
            raise ReportStateBindingError("report terminal status disagrees with durable state")
        if last_run.finished_at != report.finished_at:
            raise ReportStateBindingError("report finish time disagrees with durable state")
        if last_run.source_cursor != report.source_cursor_end:
            raise ReportStateBindingError("report ending cursor disagrees with durable last run")
        if last_run.completed_session_watermark != report.completed_session_watermark:
            raise ReportStateBindingError(
                "report completed-session watermark disagrees with durable last run"
            )
        if last_run.batch_count != report.batch_count:
            raise ReportStateBindingError("report batch count disagrees with durable last run")
        if last_run.reviewed_sessions != report.reviewed_sessions:
            raise ReportStateBindingError("report reviewed-session count disagrees with durable last run")
        if last_run.reviewed_messages != report.reviewed_messages:
            raise ReportStateBindingError("report reviewed-message count disagrees with durable last run")
        if last_run.source_characters != report.source_characters:
            raise ReportStateBindingError("report source-character count disagrees with durable last run")
        if last_run.proposal_ids != report.proposal_ids:
            raise ReportStateBindingError("report proposal IDs disagree with durable last run")
        if last_run.reason_codes != report.reason_codes:
            raise ReportStateBindingError("report reason codes disagree with durable last run")
        if last_run.backlog != report.backlog:
            raise ReportStateBindingError("report backlog state disagrees with durable last run")
        if last_run.backlog_remaining != report.backlog.remaining:
            raise ReportStateBindingError("report backlog status disagrees with durable state")
        if last_run.lock_outcome != report.lock_outcome:
            raise ReportStateBindingError("report lock outcome disagrees with durable last run")
        if last_run.lock != report.lock or state.lock != report.lock:
            raise ReportStateBindingError("report lock record disagrees with durable state")
        if last_run.canonical_media_changed != report.canonical_media_changed:
            raise ReportStateBindingError("report canonical mutation status disagrees with durable last run")
        if last_run.retryable_claim_ids != report.retryable_claim_ids:
            raise ReportStateBindingError("report retryable claim IDs disagree with durable last run")
        if last_run.errors != report.errors:
            raise ReportStateBindingError("report errors disagree with durable last run")
        report_action_ids = [action.action_id for action in report.actions]
        if last_run.action_ids != report_action_ids:
            raise ReportStateBindingError("report action ledger IDs disagree with durable last run")
        report_claim_ids = [claim.claim_id for claim in report.claims]
        if last_run.claim_ids != report_claim_ids:
            raise ReportStateBindingError("report claim ledger IDs disagree with durable last run")
        durable_action_ids = sorted(state.processed_actions)
        if sorted(last_run.durable_action_ids) != durable_action_ids:
            raise ReportStateBindingError(
                "durable action ledger IDs disagree with durable last run"
            )
        if sorted(report.durable_action_ids) != durable_action_ids:
            raise ReportStateBindingError(
                "report durable action ledger is incomplete"
            )
        durable_claim_ids = sorted(state.claims)
        if sorted(last_run.durable_claim_ids) != durable_claim_ids:
            raise ReportStateBindingError(
                "durable claim ledger IDs disagree with durable last run"
            )
        if sorted(report.durable_claim_ids) != durable_claim_ids:
            raise ReportStateBindingError(
                "report durable claim ledger is incomplete"
            )
        zero_stats = CaptureStats(
            reviewed_sessions=0,
            reviewed_messages=0,
            source_characters=0,
            proposals_submitted=0,
            duplicate_noops=0,
        )
        state_stats = state.stats
        if last_run.durable_batches_completed != state.batches_completed:
            raise ReportStateBindingError(
                "durable batch count disagrees with durable last run"
            )
        if (last_run.durable_stats or zero_stats) != state_stats:
            raise ReportStateBindingError(
                "durable cumulative stats disagree with durable last run"
            )
        if last_run.durable_discovered_sources != state.discovered_sources:
            raise ReportStateBindingError(
                "durable discovered-source ledger disagrees with durable last run"
            )
        if last_run.durable_errors != state.errors:
            raise ReportStateBindingError(
                "durable cumulative errors disagree with durable last run"
            )
        if report.durable_batches_completed != state.batches_completed:
            raise ReportStateBindingError(
                "report durable batch count is incomplete"
            )
        if (report.durable_stats or zero_stats) != state_stats:
            raise ReportStateBindingError(
                "report durable cumulative stats are incomplete"
            )
        if report.durable_discovered_sources != state.discovered_sources:
            raise ReportStateBindingError(
                "report durable discovered-source ledger is incomplete"
            )
        if report.durable_errors != state.errors:
            raise ReportStateBindingError(
                "report durable cumulative errors are incomplete"
            )
        for action_id in last_run.action_ids:
            action = next(action for action in report.actions if action.action_id == action_id)
            if state.processed_actions.get(action_id) != action:
                raise ReportStateBindingError(
                    f"report action {action_id!r} does not match durable state"
                )
        for claim_id in last_run.claim_ids:
            claim = next(claim for claim in report.claims if claim.claim_id == claim_id)
            if state.claims.get(claim_id) != claim:
                raise ReportStateBindingError(
                    f"report claim {claim_id!r} does not match durable state"
                )
        durable_run_claim_ids = [
            claim_id
            for claim_id, claim in state.claims.items()
            if claim.owner_run_id == report.run_id
        ]
        if sorted(durable_run_claim_ids) != sorted(report_claim_ids):
            raise ReportStateBindingError("report claim ledger is not complete for the durable run")

    @staticmethod
    def _mutation_lock_path(path: Path) -> Path:
        return path.with_name(f".{path.name}.mutation.lock")

    @staticmethod
    def _write_atomic(path: Path, report: CaptureRunReport) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: str | None = None
        try:
            descriptor, temporary_path = tempfile.mkstemp(
                prefix=f".{path.name}.",
                suffix=".tmp",
                dir=path.parent,
                text=True,
            )
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(
                    report.model_dump(mode="json"),
                    handle,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, path)
            temporary_path = None
        finally:
            if temporary_path is not None:
                try:
                    Path(temporary_path).unlink()
                except FileNotFoundError:
                    pass
