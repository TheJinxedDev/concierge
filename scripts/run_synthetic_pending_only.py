#!/usr/bin/env python3
"""Run one synthetic pending-only pass through an existing Hermes cron job.

This is a bounded validation harness, not the live source adapter. It uses the
real Hermes cron store and scheduler in one explicit profile, seeds only the
packaged synthetic fixture library, and replaces the scheduler's model callback
with the package's deterministic synthetic execution bridge. It never reads
Hermes session history and never promotes canonical records.
"""

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

SYNTHETIC_SOURCE_CLASS = "synthetic_fixture"
CAPTURE_MODE = CaptureMode.PENDING_ONLY


def _absolute(value: str, label: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError(f"{label} must be an absolute path")
    return path.resolve(strict=False)


def _load_hermes_source() -> Path:
    configured = os.environ.get("HERMES_AGENT_SOURCE")
    candidate = Path(configured) if configured else (
        Path.home() / "AppData" / "Local" / "hermes" / "hermes-agent"
    )
    if not candidate.is_dir():
        raise RuntimeError(f"Hermes source checkout is unavailable: {candidate}")
    for import_root in (
        candidate,
        candidate / "venv" / "Lib" / "site-packages",
    ):
        if import_root.is_dir() and str(import_root) not in sys.path:
            sys.path.insert(0, str(import_root))
    return candidate


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"fixture must be an object: {path}")
    return payload


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


def _snapshot(library) -> list[dict[str, Any]]:
    return [item.model_dump(mode="json") for item in library.list_media_items(include_archived=True)]


def _find_exact_job(store, spec):
    from app.cron_identity import JobOwnership, classify_job_record

    jobs = store.list_jobs()
    matches = [job for job in jobs if job.get("name") == spec.name]
    if len(matches) != 1:
        raise RuntimeError(
            f"expected exactly one {spec.name!r} job, found {len(matches)}"
        )
    job = matches[0]
    if classify_job_record(job, spec) is not JobOwnership.EXACT:
        raise RuntimeError("existing capture job is not an exact package-owned record")
    return job


def run_synthetic_pending_only(args: argparse.Namespace) -> dict[str, Any]:
    global CAPTURE_MODE

    hermes_home = _absolute(args.hermes_home, "--hermes-home")
    runtime_root = _absolute(args.runtime_root, "--runtime-root")
    data_directory = _absolute(args.data_dir, "--data-dir")
    if not (runtime_root / "backend" / "app" / "mcp_entry.py").is_file():
        raise RuntimeError("runtime root does not contain the installed Concierge backend")
    if not hermes_home.name:
        raise RuntimeError("Hermes home must identify one explicit profile")

    os.environ["HERMES_HOME"] = str(hermes_home)
    os.environ["LOCALAPPDATA"] = str(hermes_home.parent.parent / "localappdata")
    os.environ["CONCIERGE_DATA_DIR"] = str(data_directory)
    _load_hermes_source()

    from app.bootstrap import open_library
    from app.capture_boundary import CaptureMode
    from app.capture_enablement import CaptureEnablementStore
    from app.capture_report import CaptureRunReportStore
    from app.capture_state_store import CaptureStateStore
    from app.cron_identity import (
        DEFAULT_CAPTURE_SCHEDULE,
        PackageOwnedJobSpec,
        build_package_owned_job_spec,
    )
    from app.cron_lifecycle import HermesCronStore
    from app.setup_contract import DeliveryTarget
    from app.synthetic_capture_execution import execute_synthetic_capture_job

    CAPTURE_MODE = CaptureMode.PENDING_ONLY
    enablement_path = data_directory / "capture-enablement.json"
    enablement_store = CaptureEnablementStore(enablement_path)
    enablement = enablement_store.read()
    if not enablement.is_enabled:
        raise RuntimeError("synthetic run requires explicit enabled capture consent")
    decision = enablement.current_decision
    assert decision is not None
    if decision.mode is not CaptureMode.PENDING_ONLY:
        raise RuntimeError(f"synthetic run requires pending_only mode, got {decision.mode.value}")
    if decision.schedule != DEFAULT_CAPTURE_SCHEDULE:
        raise RuntimeError("synthetic run requires the package-owned default schedule")
    if decision.delivery_target is not DeliveryTarget.LOCAL:
        raise RuntimeError("synthetic run requires local delivery")

    state_path = data_directory / "capture-state.json"
    if state_path.exists():
        raise RuntimeError("refusing to overwrite an existing synthetic capture state")
    report_directory = data_directory / "capture-reports"
    if report_directory.exists() and any(report_directory.iterdir()):
        raise RuntimeError("refusing to overwrite existing synthetic capture reports")

    seed_path = runtime_root / "backend" / "tests" / "fixtures" / "concierge_e2e" / "seed_export.json"
    catalog_path = runtime_root / "backend" / "tests" / "fixtures" / "concierge_e2e" / "source_catalog.json"
    seed = _read_json(seed_path)
    catalog = _read_json(catalog_path)
    if catalog.get("source_class") != SYNTHETIC_SOURCE_CLASS:
        raise RuntimeError("synthetic catalog has the wrong source class")

    library = open_library(data_directory)
    existing_canonical = _snapshot(library)
    if not existing_canonical:
        if library.import_document(seed) != 1:
            raise RuntimeError("synthetic seed did not import exactly one canonical item")
    elif [item.get("id") for item in existing_canonical] != ["movie-echoes-of-glass-fixture"]:
        raise RuntimeError("unexpected canonical data in the synthetic profile library")
    canonical_before = _snapshot(library)
    pending_before = len(library.list_proposals())

    marker = args.package_marker
    spec: PackageOwnedJobSpec = build_package_owned_job_spec(
        schedule=DEFAULT_CAPTURE_SCHEDULE,
        delivery_target=DeliveryTarget.LOCAL,
        package_marker=marker,
    )
    store = HermesCronStore(hermes_home)
    job = _find_exact_job(store, spec)
    job_id = job["id"]

    from cron import scheduler
    from cron.executions import finish_execution
    from cron.jobs import list_jobs, mark_job_run, trigger_job, use_cron_store

    run_id = args.run_id
    now = datetime.fromisoformat(args.now.replace("Z", "+00:00"))
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("--now must be timezone-aware")
    evidence_box: dict[str, Any] = {}

    def synthetic_run(current_job, **_kwargs):
        evidence = execute_synthetic_capture_job(
            job=current_job,
            spec=spec,
            enablement=enablement,
            library=library,
            catalog_payload=catalog,
            state_store=CaptureStateStore(state_path),
            report_store=CaptureRunReportStore(report_directory),
            run_id=run_id,
            now=now,
        )
        evidence_box["execution"] = evidence
        mark_job_run(current_job["id"], True)
        if finish_execution(
            current_job["execution_id"],
            success=True,
            delivery_outcome="suppressed",
        ) is None:
            raise RuntimeError("scheduler execution completion readback failed")
        return True

    original = scheduler.run_one_job
    scheduler.run_one_job = synthetic_run
    try:
        with use_cron_store(hermes_home):
            if trigger_job(job_id) is None:
                raise RuntimeError("failed to trigger the exact package-owned cron job")
            tick_count = scheduler.tick(verbose=False, sync=True)
            final_job = next(
                item for item in list_jobs(include_disabled=True) if item["id"] == job_id
            )
    finally:
        scheduler.run_one_job = original

    execution = evidence_box.get("execution")
    if execution is None:
        raise RuntimeError("synthetic scheduler callback produced no capture evidence")
    canonical_after = _snapshot(library)
    pending_after = len(library.list_proposals())
    report = execution.report
    state = execution.state
    if canonical_after != canonical_before:
        raise RuntimeError("synthetic capture changed canonical media")
    if pending_after != pending_before + len(report.proposal_ids):
        raise RuntimeError("synthetic proposal count failed exact readback")

    return {
        "state": "synthetic_pending_only_profile_run",
        "profile_home": str(hermes_home),
        "runtime_root": str(runtime_root),
        "data_directory": str(data_directory),
        "capture_mode": CAPTURE_MODE.value,
        "job": {
            "id": job_id,
            "name": final_job.get("name"),
            "last_status": final_job.get("last_status"),
            "execution_status": final_job.get("latest_execution", {}).get("status"),
            "remaining_exact_jobs": len(store.list_jobs()),
            "tick_count": tick_count,
        },
        "capture": {
            "run_id": report.run_id,
            "terminal_status": report.terminal_status.value,
            "proposal_ids": list(report.proposal_ids),
            "proposal_count": len(report.proposal_ids),
            "state_path": str(state_path),
            "report_path": str(execution.report_path),
            "cursor_session_id": state.source_cursor.session_id,
            "cursor_last_user_message_id": state.source_cursor.last_user_message_id,
            "watermark": (
                state.completed_session_watermark.model_dump(mode="json")
                if state.completed_session_watermark is not None
                else None
            ),
            "canonical_media_changed": report.canonical_media_changed,
            "backlog_remaining": report.backlog.remaining,
        },
        "database": {
            "canonical_before": len(canonical_before),
            "canonical_after": len(canonical_after),
            "canonical_ids_unchanged": canonical_before == canonical_after,
            "pending_before": pending_before,
            "pending_after": pending_after,
        },
        "non_actions": [
            "no real Hermes session history was read",
            "no active session was observed",
            "no default Hermes profile was accessed",
            "no canonical proposal promotion was attempted",
            "no fully_auto or scoring path was executed",
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hermes-home", required=True)
    parser.add_argument("--runtime-root", required=True)
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--package-marker", default="concierge@0.1.2-dev")
    parser.add_argument("--run-id", default="synthetic-pending-only-run-001")
    parser.add_argument("--now", default="2026-08-08T04:05:00+00:00")
    parser.add_argument("--evidence", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        payload = run_synthetic_pending_only(args)
    except (OSError, RuntimeError, ValueError, KeyError) as error:
        print(json.dumps({"state": "failed", "reason": str(error)}, indent=2, sort_keys=True))
        return 2
    if args.evidence is not None:
        _write_json(args.evidence, payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
