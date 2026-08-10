#!/usr/bin/env python3
"""Run one real completed-session capture pass for Concierge automation.

The runner reads only ended Hermes sessions through the package-owned read-only
adapter, submits conservative pending observations, and never promotes or
mutates canonical media. Promotion is a separate cron/job and executable.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.automatic_capture import (  # noqa: E402
    build_automatic_observation_proposal,
    extract_automatic_capture,
)
from app.capture_batch import FailureKind  # noqa: E402
from app.capture_claims import CaptureLockManager  # noqa: E402
from app.capture_report import CaptureRunReportStore  # noqa: E402
from app.capture_state import SourceCursor, parse_capture_state  # noqa: E402
from app.capture_state_store import CaptureStateStore  # noqa: E402
from app.completed_session_source import (  # noqa: E402
    HermesCompletedSessionSourceAdapter,
    select_completed_sessions,
)
from app.completed_session_worker import (  # noqa: E402
    WorkerFailure,
    WorkerSubmission,
    run_completed_session_worker,
)
from app.hermes_state_reader import HermesStateSessionReader  # noqa: E402
from app.setup_contract import BacklogPolicy  # noqa: E402


def _absolute(value: str, label: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError(f"{label} must be an absolute path")
    return path.resolve(strict=False)


def hermes_source_candidates(
    *,
    home: Path | None = None,
    platform: str | None = None,
    python_version: tuple[int, int] | None = None,
    source: Path | str | None = None,
) -> tuple[Path, Path]:
    """Return the Hermes source and its platform-specific site-packages path."""
    selected_home = Path.home() if home is None else Path(home)
    selected_platform = os.name if platform is None else platform
    selected_version = (
        (sys.version_info.major, sys.version_info.minor)
        if python_version is None
        else python_version
    )
    source_path = (
        Path(source)
        if source is not None
        else (
            selected_home / "AppData" / "Local" / "hermes" / "hermes-agent"
            if selected_platform == "nt"
            else selected_home / ".hermes" / "hermes-agent"
        )
    )
    site_packages = (
        source_path / "venv" / "Lib" / "site-packages"
        if selected_platform == "nt"
        else source_path
        / "venv"
        / "lib"
        / f"python{selected_version[0]}.{selected_version[1]}"
        / "site-packages"
    )
    return source_path, site_packages


def _load_hermes_source() -> None:
    configured = os.environ.get("HERMES_AGENT_SOURCE")
    source, site_packages = hermes_source_candidates(source=configured)
    if not source.is_dir():
        raise RuntimeError(f"Hermes source checkout is unavailable: {source}")
    for import_root in (source, site_packages):
        if import_root.is_dir() and str(import_root) not in sys.path:
            sys.path.insert(0, str(import_root))


def _sentinel(now: datetime) -> SourceCursor:
    return SourceCursor(
        session_id="concierge-bootstrap",
        last_user_message_id=0,
        session_ended_at=now - timedelta(seconds=1),
    )


def _initial_state(
    *,
    adapter: HermesCompletedSessionSourceAdapter,
    now: datetime,
    backlog_policy: BacklogPolicy,
):
    as_of = now + timedelta(seconds=1)
    sentinel = _sentinel(as_of)
    if backlog_policy is BacklogPolicy.START_FRESH:
        selected = select_completed_sessions(adapter.read_snapshots(), as_of=as_of)
        if selected.next_watermark is not None:
            watermark = selected.next_watermark
            boundary = SourceCursor(
                session_id=watermark.session_id,
                last_user_message_id=watermark.last_user_message_id,
                session_ended_at=watermark.session_ended_at,
            )
        else:
            watermark = None
            boundary = sentinel
    else:
        watermark = None
        boundary = sentinel

    return parse_capture_state(
        {
            "schema_version": "1.1",
            "discovery_as_of": as_of.isoformat(),
            "discovery_boundary": boundary.model_dump(mode="json"),
            "discovered_sources": [],
            "source_cursor": boundary.model_dump(mode="json"),
            "completed_session_watermark": (
                watermark.model_dump(mode="json") if watermark is not None else None
            ),
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
            "canonical_media_changed": False,
            "last_run": None,
        }
    )


def run_capture(args: argparse.Namespace) -> dict[str, Any]:
    hermes_home = _absolute(args.hermes_home, "--hermes-home")
    data_directory = _absolute(args.data_dir, "--data-dir")
    schedule = getattr(args, "schedule", "0 4 * * 0")
    _load_hermes_source()

    from app.automation_preferences import AutomationPreferencesStore
    from app.bootstrap import open_library

    preferences = AutomationPreferencesStore(
        data_directory / "automation-preferences.json"
    ).read()
    if preferences is None:
        raise RuntimeError("automation onboarding has not been completed")
    if args.backlog:
        if not preferences.backlog_cron_enabled:
            raise RuntimeError("backlog capture cron is not enabled")
    elif not preferences.recent_capture_cron_enabled:
        raise RuntimeError("recent capture cron is not enabled")

    state_database = hermes_home / "state.db"
    if not state_database.is_file():
        return {
            "state": "no_session_database",
            "hermes_home": str(hermes_home),
            "data_directory": str(data_directory),
            "canonical_media_changed": False,
            "proposals_submitted": 0,
        }

    now = datetime.now(timezone.utc)
    adapter = HermesCompletedSessionSourceAdapter(
        HermesStateSessionReader(state_database)
    )
    library = open_library(data_directory)
    state_path = data_directory / "capture-state.json"
    state_store = CaptureStateStore(state_path)
    if not state_path.exists():
        state_store.create(
            _initial_state(
                adapter=adapter,
                now=now,
                backlog_policy=preferences.backlog_policy,
            )
        )
    lock_manager = CaptureLockManager(
        data_directory / "capture-lock.json", state_store=state_store
    )
    report_store = CaptureRunReportStore(data_directory / "capture-reports")
    canonical_items = library.list_media_items(include_archived=False)

    def submit(item) -> WorkerSubmission:
        extraction = extract_automatic_capture(
            item.content,
            canonical_items=canonical_items,
        )
        if extraction.match is None:
            failure_kind = (
                FailureKind.AMBIGUOUS
                if extraction.reason == "ambiguous_canonical_match"
                else FailureKind.PERMANENT
            )
            raise WorkerFailure(
                failure_kind,
                f"automatic capture abstained: {extraction.reason}",
            )
        proposal = build_automatic_observation_proposal(
            source_text=item.content,
            source_ref=item.source_ref,
            observed_at=item.cursor.session_ended_at or now,
            match=extraction.match,
        )
        try:
            library.submit_proposal(proposal.model_dump(mode="json"))
        except ValueError:
            try:
                existing = library.get_proposal(proposal.id)
            except KeyError as error:
                raise WorkerFailure(FailureKind.PERMANENT, str(error)) from error
            if existing != proposal:
                raise WorkerFailure(
                    FailureKind.CONTRACT,
                    f"proposal identity collision: {proposal.id}",
                )
        return WorkerSubmission(
            proposal_id=proposal.id,
            result_id=f"{args.run_id}:{item.source_ref}",
        )

    result = run_completed_session_worker(
        adapter,
        state_store=state_store,
        lock_manager=lock_manager,
        report_store=report_store,
        run_id=args.run_id,
        owner_token=f"concierge-automatic-capture-{args.run_id}",
        now=now,
        as_of=now + timedelta(seconds=1),
        proposal_submitter=submit,
        progress_discovery=True,
    )
    backlog_retirement = None
    if (
        args.backlog
        and result.report.terminal_status.value
        in {"complete", "no_visible_evidence"}
        and not result.report.backlog.remaining
        and not result.report.retryable_claim_ids
        and not result.report.errors
    ):
        from app.automation_cron_identity import (
            AutomationJobKind,
            AutomationLifecycleAction,
            HermesAutomationCronStore,
            build_automation_job_specs,
            retire_automation_job,
        )

        backlog_spec = next(
            spec
            for spec in build_automation_job_specs(
                preferences,
                schedule=schedule,
                runtime_root=ROOT,
                data_directory=data_directory,
                hermes_home=hermes_home,
            )
            if spec.kind is AutomationJobKind.BACKLOG
        )
        retirement = retire_automation_job(
            HermesAutomationCronStore(hermes_home),
            backlog_spec,
        )
        backlog_retirement = {
            "action": retirement.action.value,
            "reason": retirement.reason,
            "job": retirement.job,
            "mutated": retirement.mutated,
        }
        if retirement.action in {
            AutomationLifecycleAction.CONFLICT,
            AutomationLifecycleAction.FAILED,
        }:
            raise RuntimeError(f"backlog job retirement failed: {retirement.reason}")
    return {
        "state": "automatic_capture_complete",
        "hermes_home": str(hermes_home),
        "data_directory": str(data_directory),
        "backlog_run": args.backlog,
        "backlog_policy": preferences.backlog_policy.value,
        "run_id": args.run_id,
        "worker": {
            "terminal_status": result.report.terminal_status.value,
            "submitted_count": result.submitted_count,
            "replayed_count": result.replayed_count,
            "proposal_ids": list(result.report.proposal_ids),
            "backlog_remaining": result.report.backlog.remaining,
            "retryable_claim_ids": list(result.report.retryable_claim_ids),
            "report_path": str(report_store.path_for(args.run_id)),
            "state_path": str(state_path),
        },
        "backlog_retirement": backlog_retirement,
        "canonical_media_changed": result.report.canonical_media_changed,
        "canonical_media_count": len(library.list_media_items(include_archived=True)),
        "pending_proposal_count": len(library.list_pending_proposals()),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hermes-home", required=True)
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--backlog", action="store_true")
    parser.add_argument("--schedule", default="0 4 * * 0")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.run_id is None:
        args.run_id = "automatic-capture-" + datetime.now(timezone.utc).strftime(
            "%Y%m%dT%H%M%SZ"
        )
    try:
        payload = run_capture(args)
    except (OSError, RuntimeError, ValueError, KeyError) as error:
        print(json.dumps({"state": "failed", "reason": str(error)}, indent=2))
        return 2
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
