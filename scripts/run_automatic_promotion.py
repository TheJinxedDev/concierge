#!/usr/bin/env python3
"""Run Concierge's separate beta automatic-promotion pass."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.automation_promotion import AUTO_PROMOTION_THRESHOLD, run_auto_promotion  # noqa: E402


def _absolute(value: str, label: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError(f"{label} must be an absolute path")
    return path.resolve(strict=False)


def run_promotion(args: argparse.Namespace) -> dict[str, Any]:
    data_directory = _absolute(args.data_dir, "--data-dir")
    from app.automation_preferences import AutomationPreferencesStore
    from app.bootstrap import open_library

    preferences = AutomationPreferencesStore(
        data_directory / "automation-preferences.json"
    ).read()
    if preferences is None:
        raise RuntimeError("automation onboarding has not been completed")
    if not preferences.promotion_cron_enabled:
        raise RuntimeError("automatic promotion cron is not enabled")

    library = open_library(data_directory)
    canonical_before = [
        item.id for item in library.list_media_items(include_archived=True)
    ]
    pending_before = [proposal.id for proposal in library.list_pending_proposals()]
    results = run_auto_promotion(library, threshold=AUTO_PROMOTION_THRESHOLD)
    promoted = [result.proposal_id for result in results if result.promoted]
    abstained = [
        {
            "proposal_id": result.proposal_id,
            "reason": result.decision.reason.value,
            "threshold": result.decision.threshold,
            "error": result.error,
        }
        for result in results
        if not result.promoted
    ]
    canonical_after = [
        item.id for item in library.list_media_items(include_archived=True)
    ]
    pending_after = [proposal.id for proposal in library.list_pending_proposals()]
    return {
        "state": "automatic_promotion_complete",
        "run_id": args.run_id,
        "data_directory": str(data_directory),
        "threshold": AUTO_PROMOTION_THRESHOLD,
        "promoted_proposal_ids": promoted,
        "abstained": abstained,
        "canonical_media_before": canonical_before,
        "canonical_media_after": canonical_after,
        "canonical_media_changed": canonical_before != canonical_after,
        "pending_before": pending_before,
        "pending_after": pending_after,
        "verified_at": datetime.now(timezone.utc).isoformat(),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--run-id", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.run_id is None:
        args.run_id = "automatic-promotion-" + datetime.now(timezone.utc).strftime(
            "%Y%m%dT%H%M%SZ"
        )
    try:
        payload = run_promotion(args)
    except (OSError, RuntimeError, ValueError, KeyError) as error:
        print(json.dumps({"state": "failed", "reason": str(error)}, indent=2))
        return 2
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
