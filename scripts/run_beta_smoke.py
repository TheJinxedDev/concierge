#!/usr/bin/env python3
"""Run the exact disposable smoke receipt for Concierge's rough semantic beta.

This runner uses only synthetic fixtures and temporary target directories. It
covers exact package install/uninstall, ended-session observation capture,
the independent 0.85 promotion pass, pending abstention, semantic readback,
and the no-generated-score boundary. It never targets the user's real profile
or library and never contacts a provider.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.automation_preferences import (  # noqa: E402
    AutomationPreferences,
    AutomationPreferencesStore,
)
from app.bootstrap import open_library  # noqa: E402
from app.capture_contract import parse_typed_event_proposal  # noqa: E402
from app.domain import Proposal  # noqa: E402
from app.package_preflight import load_artifact  # noqa: E402
from app.setup_contract import BacklogPolicy  # noqa: E402


SEED = ROOT / "backend" / "tests" / "fixtures" / "concierge_e2e" / "seed_export.json"
PACKAGE_SCRIPT = ROOT / "scripts" / "concierge_package.py"


class FakeSessionReader:
    """Synthetic ended-session source used instead of a real Hermes database."""

    def __init__(self, _path: Path):
        self.sessions = (
            {
                "id": "ended-automatic-session",
                "source": "synthetic-fixture",
                "started_at": "2026-08-09T08:00:00+00:00",
                "ended_at": "2026-08-09T08:30:00+00:00",
                "end_reason": "synthetic_fixture",
                "parent_session_id": None,
                "archived": False,
            },
        )
        self.messages = (
            {
                "id": 7,
                "role": "user",
                "content": "I finished Echoes of Glass and loved the fractured colors.",
                "timestamp": "2026-08-09T08:20:00+00:00",
                "active": True,
            },
        )

    def list_sessions(self):
        return self.sessions

    def list_messages(self, _session_id):
        return self.messages


def _load_script(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load beta runner script: {path.name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_command(command: list[str], *, env: dict[str, str]) -> dict[str, Any]:
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "command failed with exit code "
            f"{completed.returncode}: {' '.join(command)}\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(
            f"command did not return JSON: {' '.join(command)}\n{completed.stdout}"
        ) from error
    if not isinstance(payload, dict):
        raise RuntimeError(f"command returned non-object JSON: {' '.join(command)}")
    return payload


def _enable_automation(data_directory: Path) -> AutomationPreferences:
    preferences = AutomationPreferences(
        decision_id="synthetic-beta-smoke",
        decided_at="2026-08-09T09:00:00+00:00",
        backlog_cron_enabled=True,
        recent_capture_cron_enabled=True,
        promotion_cron_enabled=True,
        backlog_policy=BacklogPolicy.PROCESS_EXISTING,
    )
    AutomationPreferencesStore(
        data_directory / "automation-preferences.json"
    ).save(preferences)
    stored = AutomationPreferencesStore(
        data_directory / "automation-preferences.json"
    ).read()
    if stored != preferences:
        raise AssertionError("synthetic automation preferences did not round-trip")
    return stored


def _low_confidence_observation() -> dict[str, Any]:
    return {
        "id": "synthetic-low-confidence-observation",
        "target_media_item_id": "movie-echoes-of-glass-fixture",
        "kind": "observation",
        "proposed_observation": {
            "id": "synthetic-low-confidence-observation-record",
            "scope": "work",
            "polarity": "positive",
            "dimension": "visual_style",
            "text": "Synthetic low-confidence evidence stays pending.",
            "provenance": "assistant_inferred",
            "privacy": "assistant_readable",
            "source_context": "synthetic:low-confidence",
            "confidence": 0.84,
            "review_state": "needs_review",
            "observed_on": "2026-08-09",
        },
        "source_context": "synthetic:low-confidence",
        "confidence": 0.84,
        "review_state": "needs_review",
        "proposed_on": "2026-08-09",
    }


def _inferred_rating() -> dict[str, Any]:
    return {
        "id": "synthetic-inferred-rating",
        "kind": "rating_event",
        "target_media_item_id": "movie-echoes-of-glass-fixture",
        "rating_event": {
            "event_id": "synthetic-inferred-rating-event",
            "score": 9.0,
            "rated_on": "2026-08-09",
        },
        "source_context": "synthetic:inferred-score",
        "provenance": "assistant_inferred",
        "confidence": 1.0,
        "idempotency_key": "synthetic:inferred-score",
        "proposed_on": "2026-08-09",
    }


@contextmanager
def _isolated_environment(root: Path) -> Iterator[dict[str, Path]]:
    hermes_home = root / "synthetic-hermes-home"
    local_appdata = root / "synthetic-local-appdata"
    data_directory = root / "synthetic-library"
    hermes_home.mkdir(parents=True)
    (hermes_home / "state.db").touch()
    yield {
        "hermes_home": hermes_home,
        "local_appdata": local_appdata,
        "data_directory": data_directory,
    }


def run_smoke() -> dict[str, Any]:
    artifact = load_artifact(ROOT)
    if artifact.version != "0.1.16-dev":
        raise AssertionError(f"unexpected artifact version: {artifact.version}")

    isolated_env = os.environ.copy()
    isolated_env.pop("HERMES_HOME", None)
    isolated_env.pop("LOCALAPPDATA", None)
    isolated_env.pop("CONCIERGE_DATA_DIR", None)

    with tempfile.TemporaryDirectory(prefix="concierge-beta-smoke-") as temporary_name:
        temporary_root = Path(temporary_name)
        with _isolated_environment(temporary_root) as paths:
            install_hermes = temporary_root / "install-hermes-home"
            install_local_appdata = temporary_root / "install-local-appdata"
            install = _run_command(
                [
                    sys.executable,
                    str(PACKAGE_SCRIPT),
                    "install",
                    "--artifact-root",
                    str(ROOT),
                    "--hermes-home",
                    str(install_hermes),
                    "--local-appdata",
                    str(install_local_appdata),
                ],
                env={
                    **isolated_env,
                    "HERMES_HOME": str(install_hermes),
                    "LOCALAPPDATA": str(install_local_appdata),
                },
            )
            if install.get("action") != "installed":
                raise AssertionError(f"exact package install did not install: {install}")
            if install.get("version") != artifact.version:
                raise AssertionError("installed package version differs from artifact")
            if install.get("artifact_hash") != artifact.artifact_hash:
                raise AssertionError("installed artifact hash differs from exact snapshot")

            installed_skill = install_hermes / "skills" / "concierge"
            installed_runtime = (
                install_local_appdata / "Concierge" / "packages" / artifact.version
            )
            if not installed_skill.is_dir() or not installed_runtime.is_dir():
                raise AssertionError("exact package install omitted an owned target")

            data_directory = paths["data_directory"]
            hermes_home = paths["hermes_home"]
            library = open_library(data_directory)
            if library.import_document(json.loads(SEED.read_text(encoding="utf-8"))) != 1:
                raise AssertionError("synthetic seed import did not import one media item")
            preferences = _enable_automation(data_directory)

            capture = _load_script(
                "run_automatic_capture_beta_smoke",
                ROOT / "scripts" / "run_automatic_capture.py",
            )
            capture.HermesStateSessionReader = FakeSessionReader
            capture._load_hermes_source = lambda: None
            capture_payload = capture.run_capture(
                SimpleNamespace(
                    hermes_home=str(hermes_home),
                    data_dir=str(data_directory),
                    run_id="synthetic-ended-session-capture",
                    backlog=False,
                )
            )
            if capture_payload["worker"]["submitted_count"] != 1:
                raise AssertionError(f"ended-session capture did not submit one proposal: {capture_payload}")
            if capture_payload["canonical_media_changed"] is not False:
                raise AssertionError("capture changed canonical media")

            library = open_library(data_directory)
            low_confidence = library.submit_proposal(_low_confidence_observation())
            inferred_rating = library.submit_capture_proposal(
                parse_typed_event_proposal(_inferred_rating())
            )

            promotion = _load_script(
                "run_automatic_promotion_beta_smoke",
                ROOT / "scripts" / "run_automatic_promotion.py",
            )
            promotion_payload = promotion.run_promotion(
                SimpleNamespace(
                    data_dir=str(data_directory),
                    run_id="synthetic-automatic-promotion",
                )
            )
            if promotion_payload["threshold"] != 0.85:
                raise AssertionError("beta promotion threshold is not 0.85")
            if len(promotion_payload["promoted_proposal_ids"]) != 1:
                raise AssertionError(f"unexpected promotion count: {promotion_payload}")
            if promotion_payload["canonical_media_changed"] is not False:
                raise AssertionError("automatic promotion changed canonical media identity")
            abstained_reasons = {
                result["reason"] for result in promotion_payload["abstained"]
            }
            if "low_confidence" not in abstained_reasons:
                raise AssertionError("low-confidence proposal was not abstained")
            if "inferred_score" not in abstained_reasons:
                raise AssertionError("assistant-inferred score was not abstained")
            pending_after = set(promotion_payload["pending_after"])
            if {low_confidence.id, inferred_rating.id} - pending_after:
                raise AssertionError("abstained proposals did not remain pending")

            canonical_before = promotion_payload["canonical_media_before"]
            canonical_after = promotion_payload["canonical_media_after"]
            item = open_library(data_directory).get_media_item(
                "movie-echoes-of-glass-fixture"
            )
            search_results = open_library(data_directory).search_media_titles("Echoes")
            dimension = open_library(data_directory).dimension_profile("emotional_reaction")
            semantic_entry = next(
                (entry for entry in dimension.entries if entry.media_item_id == item.id),
                None,
            )
            no_generated_numeric_score = item.rating is None and not item.rating_history
            if not search_results or search_results[0].id != item.id:
                raise AssertionError("semantic title query did not read back the canonical item")
            if semantic_entry is None or not semantic_entry.supporting_evidence:
                raise AssertionError("semantic dimension query did not read back cited evidence")
            if not no_generated_numeric_score:
                raise AssertionError("canonical item contains a generated numeric score")

            uninstall = _run_command(
                [
                    sys.executable,
                    str(PACKAGE_SCRIPT),
                    "uninstall",
                    "--hermes-home",
                    str(install_hermes),
                    "--local-appdata",
                    str(install_local_appdata),
                    "--version",
                    artifact.version,
                    "--expected-artifact-hash",
                    artifact.artifact_hash,
                ],
                env={
                    **isolated_env,
                    "HERMES_HOME": str(install_hermes),
                    "LOCALAPPDATA": str(install_local_appdata),
                },
            )
            if uninstall.get("action") != "removed":
                raise AssertionError(f"exact package uninstall did not remove package: {uninstall}")
            if installed_skill.exists() or installed_runtime.exists():
                raise AssertionError("exact package uninstall left owned files behind")

            return {
                "schema_version": "1",
                "status": "complete",
                "package": {
                    "name": artifact.name,
                    "version": artifact.version,
                    "artifact_hash": artifact.artifact_hash,
                    "declared_file_count": len(artifact.files),
                    "install_action": install["action"],
                    "uninstall_action": uninstall["action"],
                },
                "cron_choices": {
                    "backlog_cron_enabled": preferences.backlog_cron_enabled,
                    "recent_capture_cron_enabled": preferences.recent_capture_cron_enabled,
                    "promotion_cron_enabled": preferences.promotion_cron_enabled,
                    "backlog_policy": preferences.backlog_policy.value,
                },
                "capture": {
                    "ended_session_fixture": True,
                    "submitted_count": capture_payload["worker"]["submitted_count"],
                    "pending_proposal_count": capture_payload["pending_proposal_count"],
                    "canonical_media_changed": capture_payload["canonical_media_changed"],
                    "proposal_ids": capture_payload["worker"]["proposal_ids"],
                },
                "promotion": {
                    "threshold": promotion_payload["threshold"],
                    "promoted_proposal_ids": promotion_payload["promoted_proposal_ids"],
                    "abstained": promotion_payload["abstained"],
                    "pending_before": promotion_payload["pending_before"],
                    "pending_after": promotion_payload["pending_after"],
                    "canonical_media_before": canonical_before,
                    "canonical_media_after": canonical_after,
                    "canonical_media_changed": promotion_payload["canonical_media_changed"],
                },
                "semantic_readback": {
                    "title_query_found_canonical_item": True,
                    "dimension_query_found_cited_observation": True,
                    "canonical_item_id": item.id,
                    "canonical_observation_count": len(item.observations),
                    "canonical_rating_is_none": item.rating is None,
                },
                "no_generated_numeric_score": no_generated_numeric_score,
                "cleanup": {
                    "skill_removed": not installed_skill.exists(),
                    "runtime_removed": not installed_runtime.exists(),
                    "temporary_root_removed_by_context": True,
                },
            }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        payload = run_smoke()
    except Exception as error:
        print(json.dumps({"schema_version": "1", "status": "failed", "reason": str(error)}, indent=2))
        return 2
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8", newline="\n")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
