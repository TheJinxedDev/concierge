#!/usr/bin/env python3
"""Run two synthetic completed sessions through the real Concierge worker/cron seam."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.capture_boundary import CaptureMode

SYNTHETIC_SESSION_SOURCE = "synthetic_fixture"
AS_OF = datetime(2026, 8, 8, 12, tzinfo=timezone.utc)
NOW = datetime(2026, 8, 8, 12, 1, tzinfo=timezone.utc)
SESSION_1_END = datetime(2026, 8, 8, 10, tzinfo=timezone.utc)
SESSION_2_END = datetime(2026, 8, 8, 11, tzinfo=timezone.utc)


class SyntheticSessionReader:
    """Caller-owned ended session records; no Hermes database access."""

    def __init__(self):
        self.sessions = [
            {
                "id": "synthetic-session-1",
                "source": SYNTHETIC_SESSION_SOURCE,
                "started_at": "2026-08-08T09:00:00+00:00",
                "ended_at": SESSION_1_END.isoformat(),
                "end_reason": "agent_close",
                "parent_session_id": None,
                "archived": False,
            },
            {
                "id": "synthetic-session-2",
                "source": SYNTHETIC_SESSION_SOURCE,
                "started_at": "2026-08-08T10:00:00+00:00",
                "ended_at": SESSION_2_END.isoformat(),
                "end_reason": "agent_close",
                "parent_session_id": None,
                "archived": False,
            },
        ]
        self.messages = {
            "synthetic-session-1": [
                {
                    "id": 1,
                    "role": "user",
                    "content": "I finished Echoes of Glass and liked its fractured colors.",
                    "timestamp": "2026-08-08T09:30:00+00:00",
                    "active": True,
                }
            ],
            "synthetic-session-2": [
                {
                    "id": 10,
                    "role": "user",
                    "content": "I finished The Glass Cartographer.",
                    "timestamp": "2026-08-08T10:30:00+00:00",
                    "active": True,
                }
            ],
        }

    def list_sessions(self):
        return tuple(dict(row) for row in self.sessions)

    def list_messages(self, session_id):
        return tuple(dict(row) for row in self.messages[session_id])


def _absolute(value: str, label: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError(f"{label} must be an absolute path")
    return path.resolve(strict=False)


def _manifest_version() -> str:
    manifest = ROOT / "manifest.yaml"
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if line.startswith("version:"):
            version = line.partition(":")[2].strip()
            if version:
                return version
    raise ValueError(f"package manifest has no version: {manifest}")


DEFAULT_PACKAGE_MARKER = f"concierge@{_manifest_version()}"


def _load_hermes_source() -> None:
    configured = os.environ.get("HERMES_AGENT_SOURCE")
    source = Path(configured) if configured else (
        Path.home() / "AppData" / "Local" / "hermes" / "hermes-agent"
    )
    if not source.is_dir():
        raise RuntimeError(f"Hermes source checkout is unavailable: {source}")
    for import_root in (source, source / "venv" / "Lib" / "site-packages"):
        if not import_root.is_dir() or str(import_root) in sys.path:
            continue
        if import_root == source:
            sys.path.insert(0, str(import_root))
        else:
            sys.path.append(str(import_root))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            descriptor = -1
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if descriptor != -1:
            os.close(descriptor)
        if temporary.exists():
            temporary.unlink()


def _cursor(session_id: str, message_id: int, ended_at: datetime) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "session_ended_at": ended_at.isoformat(),
        "last_user_message_id": message_id,
    }


def _initial_state():
    from app.capture_state import parse_capture_state

    return parse_capture_state(
        {
            "schema_version": "1.1",
            "discovery_as_of": AS_OF.isoformat(),
            "discovery_boundary": _cursor("synthetic-session-2", 10, SESSION_2_END),
            "discovered_sources": [
                _cursor("synthetic-session-1", 1, SESSION_1_END),
                _cursor("synthetic-session-2", 10, SESSION_2_END),
            ],
            "source_cursor": _cursor("synthetic-session-1", 0, SESSION_1_END),
            "completed_session_watermark": None,
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


def _initial_state_for_policy(backlog_policy):
    """Seed the synthetic first-run frontier from the explicit backlog choice."""

    from app.capture_state import parse_capture_state
    from app.setup_contract import BacklogPolicy

    state = _initial_state()
    if backlog_policy is not BacklogPolicy.START_FRESH:
        return state
    boundary = _cursor("synthetic-session-2", 10, SESSION_2_END)
    payload = state.model_dump(mode="json")
    payload["source_cursor"] = boundary
    payload["completed_session_watermark"] = boundary
    return parse_capture_state(payload)


def _proposal_payload(item) -> dict[str, Any]:
    if item.message_id == 1:
        return {
            "id": "proposal-worker-echoes-of-glass",
            "target_media_item_id": "movie-echoes-of-glass-fixture",
            "kind": "observation",
            "proposed_observation": {
                "id": "observation-worker-echoes-of-glass",
                "scope": "work",
                "polarity": "neutral",
                "dimension": "consumption",
                "text": item.content,
                "provenance": "assistant_inferred",
                "privacy": "assistant_readable",
                "source_context": item.source_ref,
                "confidence": 0.8,
                "review_state": "needs_review",
                "observed_on": "2026-08-08",
            },
            "source_context": item.source_ref,
            "confidence": 0.8,
            "review_state": "needs_review",
            "proposed_on": "2026-08-08",
        }
    return {
        "id": "proposal-worker-glass-cartographer",
        "kind": "media_item",
        "proposed_media_item": {
            "id": "movie-the-glass-cartographer-worker-fixture",
            "title": "The Glass Cartographer",
            "category": "movie",
            "status": "finished",
        },
        "source_context": item.source_ref,
        "confidence": 0.8,
        "review_state": "needs_review",
        "proposed_on": "2026-08-08",
    }


def _snapshot(library):
    return [item.model_dump(mode="json") for item in library.list_media_items(include_archived=True)]


def _report_path(data_directory: Path, run_id: str) -> Path:
    return Path(data_directory) / "capture-reports" / f"capture-run-{run_id}.json"


def _persisted_proposal_payload(proposal) -> dict[str, Any]:
    return proposal.model_dump(
        mode="json",
        exclude_none=True,
        exclude_defaults=False,
    )


def _restore_missing_pending_proposals(library, proposals, submitter) -> int:
    expected = {proposal.id: _persisted_proposal_payload(proposal) for proposal in proposals}
    for payload in expected.values():
        if payload.get("review_state") != "needs_review":
            raise RuntimeError("synthetic seed cannot restore a non-needs_review proposal")

    current = {proposal.id: _persisted_proposal_payload(proposal) for proposal in library.list_proposals()}
    for proposal_id, payload in expected.items():
        if proposal_id in current and current[proposal_id] != payload:
            raise RuntimeError(f"pre-existing proposal payload changed during synthetic seed: {proposal_id}")

    restored = 0
    for proposal_id, payload in expected.items():
        if proposal_id not in current:
            submitter(library, payload)
            restored += 1

    final = {proposal.id: _persisted_proposal_payload(proposal) for proposal in library.list_proposals()}
    for proposal_id, payload in expected.items():
        if final.get(proposal_id) != payload:
            raise RuntimeError(f"pre-existing proposal was not restored exactly: {proposal_id}")
    return restored


def _exact_job(store, spec):
    from app.cron_identity import JobOwnership, classify_job_record

    matches = [job for job in store.list_jobs() if job.get("name") == spec.name]
    if len(matches) != 1:
        raise RuntimeError(f"expected one exact {spec.name!r} job, found {len(matches)}")
    if classify_job_record(matches[0], spec) is not JobOwnership.EXACT:
        raise RuntimeError("synthetic worker found a drifted or unowned cron job")
    return matches[0]


def run_synthetic_completed_sessions(args: argparse.Namespace) -> dict[str, Any]:
    hermes_home = _absolute(args.hermes_home, "--hermes-home")
    runtime_root = _absolute(args.runtime_root, "--runtime-root")
    data_directory = _absolute(args.data_dir, "--data-dir")
    if not (runtime_root / "backend" / "app" / "completed_session_worker.py").is_file():
        raise RuntimeError("runtime root does not contain the completed-session worker")
    os.environ["HERMES_HOME"] = str(hermes_home)
    os.environ["CONCIERGE_DATA_DIR"] = str(data_directory)
    _load_hermes_source()

    from app.bootstrap import open_library
    from app.capture_claims import CaptureLockManager
    from app.capture_enablement import CaptureEnablementStore
    from app.capture_report import CaptureRunReportStore
    from app.capture_state_store import CaptureStateStore
    from app.completed_session_source import HermesCompletedSessionSourceAdapter
    from app.completed_session_worker import (
        WorkerSubmission,
        run_completed_session_worker,
    )
    from app.cron_identity import DEFAULT_CAPTURE_SCHEDULE, build_package_owned_job_spec
    from app.cron_lifecycle import (
        HermesCronStore,
        LifecycleAction,
        retire_completed_manual_backlog_job,
    )
    from app.mcp_server import submit_pending_proposal_record
    from app.setup_contract import BacklogPolicy, DeliveryTarget

    enablement = CaptureEnablementStore(data_directory / "capture-enablement.json").read()
    if not enablement.is_enabled or enablement.current_decision is None:
        raise RuntimeError("synthetic worker requires explicit capture or backlog consent")
    decision = enablement.current_decision
    if decision.mode not in {CaptureMode.PENDING_ONLY, CaptureMode.OFF}:
        raise RuntimeError("synthetic worker supports pending_only or manual backlog-only mode")
    if (
        decision.mode is CaptureMode.OFF
        and decision.backlog_policy is not BacklogPolicy.PROCESS_EXISTING
    ):
        raise RuntimeError("manual synthetic worker requires process_existing backlog policy")
    if decision.delivery_target is not DeliveryTarget.LOCAL:
        raise RuntimeError("synthetic worker requires local delivery")

    state_path = data_directory / "capture-state.json"
    if state_path.exists():
        raise RuntimeError("refusing to overwrite an existing worker capture state")
    library = open_library(data_directory)
    seed_path = runtime_root / "backend" / "tests" / "fixtures" / "concierge_e2e" / "seed_export.json"
    seed = json.loads(seed_path.read_text(encoding="utf-8"))
    existing = _snapshot(library)
    existing_proposals = list(library.list_proposals())
    if not existing:
        if library.import_document(seed) != 1:
            raise RuntimeError("worker synthetic seed did not import exactly one item")
    elif [item["id"] for item in existing] != ["movie-echoes-of-glass-fixture"]:
        raise RuntimeError("unexpected canonical data in worker synthetic library")
    _restore_missing_pending_proposals(
        library,
        existing_proposals,
        submit_pending_proposal_record,
    )
    canonical_before = _snapshot(library)
    pending_before = len(library.list_proposals())

    spec = build_package_owned_job_spec(
        schedule=DEFAULT_CAPTURE_SCHEDULE,
        delivery_target=DeliveryTarget.LOCAL,
        capture_mode=decision.mode,
        backlog_policy=decision.backlog_policy,
        package_marker=args.package_marker,
    )
    store = HermesCronStore(hermes_home)
    job = _exact_job(store, spec)

    state_store = CaptureStateStore(state_path)
    state_store.create(_initial_state_for_policy(decision.backlog_policy))
    lock_manager = CaptureLockManager(data_directory / "capture-lock.json", state_store=state_store)
    report_store = CaptureRunReportStore(data_directory / "capture-reports")
    adapter = HermesCompletedSessionSourceAdapter(SyntheticSessionReader())

    def submit(item):
        receipt = submit_pending_proposal_record(library, _proposal_payload(item))
        proposal = receipt["proposal"]
        return WorkerSubmission(
            proposal_id=proposal["id"],
            result_id=f"synthetic-worker-result-{item.message_id}",
        )

    from cron import scheduler
    from cron.executions import finish_execution
    from cron.jobs import list_jobs, mark_job_run, trigger_job, use_cron_store

    evidence_box: dict[str, Any] = {}

    def synthetic_run(current_job, **_kwargs):
        result = run_completed_session_worker(
            adapter,
            state_store=state_store,
            lock_manager=lock_manager,
            report_store=report_store,
            run_id=args.run_id,
            owner_token=f"synthetic-owner-{args.run_id}",
            now=NOW,
            proposal_submitter=submit,
        )
        evidence_box["result"] = result
        mark_job_run(current_job["id"], True)
        if finish_execution(
            current_job["execution_id"],
            success=True,
            delivery_outcome="suppressed",
        ) is None:
            raise RuntimeError("cron execution completion failed")
        return True

    original = scheduler.run_one_job
    scheduler.run_one_job = synthetic_run
    try:
        with use_cron_store(hermes_home):
            if trigger_job(job["id"]) is None:
                raise RuntimeError("could not trigger package-owned synthetic job")
            tick_count = scheduler.tick(verbose=False, sync=True)
            execution_job = next(
                (item for item in list_jobs(include_disabled=True) if item["id"] == job["id"]),
                None,
            )
    finally:
        scheduler.run_one_job = original

    result = evidence_box.get("result")
    if result is None:
        raise RuntimeError("synthetic cron callback produced no worker result")
    canonical_after = _snapshot(library)
    pending_after = len(library.list_proposals())
    if canonical_after != canonical_before:
        raise RuntimeError("completed-session worker changed canonical media")
    if pending_after != pending_before + result.submitted_count:
        raise RuntimeError("worker proposal readback count disagreed")

    retirement = None
    if decision.mode is CaptureMode.OFF:
        retirement = retire_completed_manual_backlog_job(
            store,
            spec,
            terminal_status=result.report.terminal_status,
            backlog_remaining=result.report.backlog.remaining,
            retryable_claim_ids=tuple(result.report.retryable_claim_ids),
            canonical_media_changed=result.report.canonical_media_changed,
            verified_readback=True,
        )
        if retirement.action is not LifecycleAction.REMOVED:
            raise RuntimeError(
                "verified manual backlog catch-up did not retire its exact job: "
                f"{retirement.reason}"
            )
    remaining_job = next(
        (item for item in store.list_jobs() if item.get("id") == job["id"]),
        None,
    )

    state = state_store.read()
    return {
        "state": "synthetic_completed_session_worker_cron",
        "profile_home": str(hermes_home),
        "runtime_root": str(runtime_root),
        "data_directory": str(data_directory),
        "job": {
            "id": job["id"],
            "name": execution_job.get("name") if execution_job is not None else None,
            "last_status": execution_job.get("last_status") if execution_job is not None else None,
            "execution_status": (
                execution_job.get("latest_execution", {}).get("status")
                if execution_job is not None
                else None
            ),
            "tick_count": tick_count,
            "remaining_exact_jobs": len(store.list_jobs()),
            "present_after_run": remaining_job is not None,
            "retirement_action": (
                retirement.action.value
                if retirement is not None
                else "not_applicable"
            ),
        },
        "worker": {
            "capture_mode": decision.mode.value,
            "backlog_policy": decision.backlog_policy.value,
            "terminal_status": result.report.terminal_status.value,
            "submitted_count": result.submitted_count,
            "replayed_count": result.replayed_count,
            "proposal_ids": list(result.report.proposal_ids),
            "source_cursor": state.source_cursor.model_dump(mode="json"),
            "completed_session_watermark": (
                state.completed_session_watermark.model_dump(mode="json")
                if state.completed_session_watermark is not None
                else None
            ),
            "report_path": str(_report_path(data_directory, args.run_id)),
            "state_path": str(state_path),
            "canonical_media_changed": result.report.canonical_media_changed,
            "backlog_remaining": result.report.backlog.remaining,
        },
        "database": {
            "canonical_before": len(canonical_before),
            "canonical_after": len(canonical_after),
            "canonical_ids_unchanged": canonical_before == canonical_after,
            "pending_before": pending_before,
            "pending_after": pending_after,
        },
        "non_actions": [
            "synthetic source only; no real Hermes session database was read",
            "active session was not observed",
            "default Hermes profile was not accessed",
            "canonical promotion and fully_auto were not attempted",
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hermes-home", required=True)
    parser.add_argument("--runtime-root", required=True)
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--package-marker", default=DEFAULT_PACKAGE_MARKER)
    parser.add_argument("--run-id", default="synthetic-completed-worker-run-001")
    parser.add_argument("--evidence", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        payload = run_synthetic_completed_sessions(args)
    except (OSError, RuntimeError, ValueError, KeyError) as error:
        print(json.dumps({"state": "failed", "reason": str(error)}, indent=2, sort_keys=True))
        return 2
    if args.evidence is not None:
        _write_json(args.evidence, payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
