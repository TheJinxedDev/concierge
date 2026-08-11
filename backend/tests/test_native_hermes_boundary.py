from pathlib import Path
import json
import os
import sys
from argparse import Namespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from app.automation_cron_identity import build_automation_job_specs
from app.automation_preferences import AutomationPreferences, AutomationPreferencesStore
from app.package_lifecycle import LifecycleAction, LifecycleResult, PackageInstallation
from app.package_mcp import McpOwnership, build_mcp_server_spec, classify_mcp_record
from app.domain import Proposal
from app.mcp_server import assistant_proposal_receipt_view
from scripts.concierge_package import (
    _environment,
    _assert_uninstall_launcher_is_outside_runtime,
    _installation_payload,
)
from scripts import concierge_quickstart as quickstart_module
from scripts.concierge_quickstart import (
    build_ui_handoff,
    build_child_environment,
    condense_quickstart_receipt,
    derive_backlog_policy,
    load_setup_context,
    result_mutated,
    validate_automation_choices,
    verify_quickstart_receipt,
)
from scripts.concierge_setup import EXPLICIT_AUTOMATION_CONFIRMATION, save_automation_preferences


def _preferences(**overrides):
    values = {
        "decision_id": "native-hermes-boundary",
        "decided_at": "2026-08-10T12:00:00+00:00",
        "backlog_cron_enabled": False,
        "recent_capture_cron_enabled": True,
        "promotion_cron_enabled": False,
    }
    values.update(overrides)
    return AutomationPreferences(**values)


def test_promotion_cron_requires_a_capture_source():
    with pytest.raises(ValueError, match="capture source"):
        _preferences(
            recent_capture_cron_enabled=False,
            promotion_cron_enabled=True,
        )


def test_automation_plans_tell_hermes_to_use_native_session_search():
    plans = build_automation_job_specs(
        _preferences(
            backlog_cron_enabled=True,
            promotion_cron_enabled=True,
        ),
        runtime_root=ROOT,
        data_directory=ROOT / ".test-data",
    )

    capture_prompt = next(plan.prompt for plan in plans if plan.name == "concierge-session-capture")
    assert "session_search" in capture_prompt
    assert "run_automatic_capture.py" not in capture_prompt
    assert "hermes_state" not in capture_prompt
    assert "import croniter" not in capture_prompt


def test_public_artifact_does_not_import_or_seek_private_hermes_runtime():
    package_source = [
        path
        for path in (ROOT / "backend" / "app").glob("*.py")
        if path.name != "__init__.py"
    ] + list((ROOT / "scripts").glob("*.py"))
    joined = "\n".join(path.read_text(encoding="utf-8") for path in package_source)

    assert "from cron." not in joined
    assert "import cron." not in joined
    assert "from hermes_state import" not in joined
    assert "HERMES_AGENT_SOURCE" not in joined
    assert '"croniter>=' not in (ROOT / "pyproject.toml").read_text(encoding="utf-8")


def test_onboarding_names_native_hermes_tools_and_not_a_private_backend():
    skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")

    assert "native Hermes tools" in skill
    assert "session_search" in skill
    assert "cronjob" in skill
    assert "Do not search for or install a second Hermes backend" in skill
    assert "native `hermes mcp add`" in skill
    assert "public profile-scoped `hermes cron`" in skill
    assert "run_synthetic_completed_sessions.py" not in skill


def test_mcp_receipt_preserves_the_explicit_environment_directory():
    environment_directory = ROOT.parent / "concierge-native-hermes-test-env"

    spec = build_mcp_server_spec(
        ROOT,
        data_directory=ROOT / "concierge-native-hermes-test-data",
        environment_directory=environment_directory,
    )

    assert f"UV_PROJECT_ENVIRONMENT={environment_directory}" in spec.environment


def test_same_automation_decision_retries_without_a_timestamp_collision(tmp_path: Path):
    store = AutomationPreferencesStore(tmp_path / "automation-preferences.json")
    original = _preferences(decided_at="2026-08-10T12:00:00+00:00")
    retry = _preferences(decided_at="2026-08-10T12:05:00+00:00")

    store.save(original)

    assert store.save(retry) == original


def test_profile_mcp_environment_scrubs_inherited_python_runtime_paths():
    spec = build_mcp_server_spec(
        ROOT,
        data_directory=ROOT / "concierge-native-hermes-test-data",
        environment_directory=ROOT.parent / "concierge-native-hermes-test-env",
    )

    assert "PYTHONPATH=" in spec.environment
    assert "VIRTUAL_ENV=" in spec.environment


def test_profile_mcp_readback_recognizes_its_own_environment_mapping():
    spec = build_mcp_server_spec(
        ROOT,
        data_directory=ROOT / "concierge-native-hermes-test-data",
        environment_directory=ROOT.parent / "concierge-native-hermes-test-env",
    )
    record = {"name": spec.name, **spec.as_config()}

    assert classify_mcp_record(record, spec) is McpOwnership.EXACT


def test_same_automation_cli_retry_reports_an_exact_noop(tmp_path: Path):
    args = Namespace(
        runtime_root=str(ROOT),
        data_dir=str(tmp_path),
        decision_id="retry-safe-decision",
        backlog_cron="no",
        recent_capture_cron="yes",
        promotion_cron="no",
        backlog_policy="start_fresh",
        favorite_media_interview="no",
        schedule="0 4 * * 0",
        confirmation=EXPLICIT_AUTOMATION_CONFIRMATION,
    )

    first = save_automation_preferences(args)
    second = save_automation_preferences(args)

    assert first["mutated"] is True
    assert second["mutated"] is False
    assert second["preferences"] == first["preferences"]


def test_install_receipt_names_the_runnable_installed_artifact_root(tmp_path: Path):
    runtime_path = tmp_path / "Concierge" / "packages" / "0.1.16-dev.1"
    installation = PackageInstallation(
        package_name="concierge",
        version="0.1.16-dev.1",
        artifact_hash="sha256:exact",
        artifact_files=("SKILL.md",),
        skill_files=("SKILL.md",),
        skill_tree_hash="sha256:skill",
        runtime_path=runtime_path,
        skill_path=tmp_path / "hermes" / "skills" / "concierge",
    )

    receipt = _installation_payload(
        LifecycleResult(
            LifecycleAction.INSTALLED,
            "installed exact package artifact",
            True,
            installation,
        )
    )

    assert receipt["runtime_path"] == str(runtime_path)
    assert receipt["artifact_directory"] == "artifact"
    assert receipt["runtime_project_path"] == str(runtime_path / "artifact")


def test_uninstall_refuses_a_launcher_inside_the_owned_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    local_appdata = tmp_path / "local-appdata"
    runtime_project = (
        local_appdata / "Concierge" / "packages" / "0.1.16-dev.1" / "artifact"
    )
    runtime_project.mkdir(parents=True)
    monkeypatch.chdir(runtime_project)

    with pytest.raises(ValueError, match="outside the installed runtime"):
        _assert_uninstall_launcher_is_outside_runtime(
            local_appdata,
            "0.1.16-dev.1",
        )


def test_package_preflight_projects_only_needed_environment(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HERMES_HOME", "C:/profiles/fresh")
    monkeypatch.setenv("LOCALAPPDATA", "C:/local")
    monkeypatch.setenv("OPENROUTER_API_KEY", "must-not-enter-preflight")

    values = _environment(Namespace(hermes_home=None, local_appdata=None, data_dir=None))

    assert values["HERMES_HOME"] == "C:/profiles/fresh"
    assert values["LOCALAPPDATA"] == "C:/local"
    assert "OPENROUTER_API_KEY" not in values


def test_quickstart_child_environment_scrubs_hermes_python_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setenv("PATH", os.environ.get("PATH", ""))
    monkeypatch.setenv("PYTHONPATH", "C:/hermes/source;C:/hermes/venv/site-packages")
    monkeypatch.setenv("VIRTUAL_ENV", "C:/hermes/venv")
    monkeypatch.setenv("OPENROUTER_API_KEY", "must-not-enter-concierge")

    values = build_child_environment(tmp_path / "concierge-env", tmp_path / "concierge-data")

    assert values["PYTHONPATH"] == ""
    assert values["VIRTUAL_ENV"] == ""
    assert values["UV_PROJECT_ENVIRONMENT"] == str(tmp_path / "concierge-env")
    assert values["CONCIERGE_DATA_DIR"] == str(tmp_path / "concierge-data")
    assert "OPENROUTER_API_KEY" not in values


def test_public_onboarding_is_a_short_quickstart_not_a_release_rehearsal():
    skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")

    assert "Quick setup" in skill
    assert "scripts/concierge_quickstart.py" not in skill
    assert "scripts/concierge_package.py" not in skill
    assert "uv run" not in skill
    assert "--force" not in skill
    assert len(skill.splitlines()) <= 150


def test_manifest_treats_hermes_version_as_observed_not_pinned():
    manifest = (ROOT / "manifest.yaml").read_text(encoding="utf-8")

    assert 'hermes_cli: "public CLI/tools; no upper version pin"' in manifest
    assert "0.20.x observed" not in manifest


def test_quickstart_reports_automation_preference_mutation():
    assert result_mutated(
        {"mutated": False},
        {"mutated": False},
        {"mutated": True},
    ) is True


def test_quickstart_rejects_partial_automation_choices_before_setup():
    with pytest.raises(ValueError, match="all three automation choices"):
        validate_automation_choices("no", "yes", None)


def test_quickstart_rejects_promotion_only_before_setup():
    with pytest.raises(ValueError, match="capture source"):
        validate_automation_choices("no", "no", "yes")


def test_quickstart_console_receipt_omits_verbose_inventory_and_prompts():
    condensed = condense_quickstart_receipt(
        {
            "action": "concierge_ready_for_hermes_registration",
            "mutated": True,
            "installation": {
                "action": "installed",
                "version": "0.1.16-dev.2",
                "artifact_hash": "sha256:exact",
                "runtime_project_path": "C:/runtime/artifact",
                "skill_path": "C:/hermes/skills/concierge",
                "artifact_files": ["private-noise"],
            },
            "initialization": {
                "action": "database_initialized",
                "data_directory": "C:/data",
                "database_path": "C:/data/db.sqlite3",
                "mcp": {"name": "taste_database"},
            },
            "automation": {
                "action": "automation_preferences_saved",
                "mutated": True,
                "preferences": {"lane": "fully_auto"},
                "native_hermes_jobs": {
                    "plans": [{"name": "capture", "prompt": "very long"}]
                },
            },
        },
        receipt_path=Path("C:/data/quickstart-receipt.json"),
    )

    assert condensed["installation"].get("artifact_files") is None
    assert condensed["automation"]["native_hermes_plan_count"] == 1
    assert "prompt" not in str(condensed)
    assert condensed["receipt_path"] == str(Path("C:/data/quickstart-receipt.json"))


def test_quickstart_reuses_profile_paths_from_its_full_receipt(tmp_path: Path):
    receipt = tmp_path / "quickstart-receipt.json"
    receipt.write_text(
        json.dumps(
            {
                "setup_context": {
                    "hermes_home": str(tmp_path / "hermes"),
                    "local_appdata": str(tmp_path / "local"),
                    "environment_directory": str(tmp_path / "env"),
                    "data_directory": str(tmp_path / "data"),
                }
            }
        ),
        encoding="utf-8",
    )

    context = load_setup_context(receipt)

    assert context.hermes_home == (tmp_path / "hermes").resolve()
    assert context.data_directory == (tmp_path / "data").resolve()


def test_backlog_policy_is_only_requested_when_backlog_capture_is_enabled():
    assert derive_backlog_policy("no", None) == "start_fresh"
    with pytest.raises(ValueError, match="backlog policy"):
        derive_backlog_policy("yes", None)


def test_receipt_reuse_verifies_the_owned_install_before_any_setup_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    receipt = tmp_path / "quickstart-receipt.json"
    receipt.write_text("{}", encoding="utf-8")
    commands_started = False

    def reject_unverified(_):
        raise ValueError("receipt verification failed")

    def unexpected_command(*_args, **_kwargs):
        nonlocal commands_started
        commands_started = True

    monkeypatch.setattr(quickstart_module, "verify_quickstart_receipt", reject_unverified)
    monkeypatch.setattr(quickstart_module, "_run_json", unexpected_command)
    args = Namespace(
        receipt=str(receipt),
        hermes_home=None,
        local_appdata=None,
        environment_dir=None,
        data_dir=None,
        backlog_cron="no",
        recent_capture_cron="yes",
        promotion_cron="no",
        backlog_policy=None,
        favorite_media_interview="no",
        decision_id=None,
    )

    with pytest.raises(ValueError, match="receipt verification failed"):
        quickstart_module.quickstart(args)

    assert commands_started is False


def test_receipt_verification_is_read_only_and_checks_exact_installation(tmp_path: Path):
    runtime = tmp_path / "runtime" / "artifact"
    skill = tmp_path / "hermes" / "skills" / "concierge"
    data = tmp_path / "data"
    runtime.mkdir(parents=True)
    skill.mkdir(parents=True)
    data.mkdir(parents=True)
    (runtime / "marker.txt").write_text("runtime", encoding="utf-8")
    ui_assets = runtime / "frontend" / "dist" / "assets"
    ui_assets.mkdir(parents=True)
    (ui_assets.parent / "index.html").write_text("Concierge", encoding="utf-8")
    (ui_assets / "app.js").write_text("built", encoding="utf-8")
    (skill / "SKILL.md").write_text("skill", encoding="utf-8")
    database = data / "taste-database.sqlite3"
    import sqlite3
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE media_items (id TEXT PRIMARY KEY)")
        connection.execute("CREATE TABLE proposals (id TEXT PRIMARY KEY)")
    receipt = tmp_path / "receipt.json"
    receipt.write_text(
        json.dumps(
            {
                "setup_context": {"data_directory": str(data)},
                "installation": {
                    "artifact_hash": "sha256:expected",
                    "runtime_project_path": str(runtime),
                    "skill_path": str(skill),
                },
                "initialization": {"database_path": str(database)},
            }
        ),
        encoding="utf-8",
    )
    before = {path: path.stat().st_mtime_ns for path in (runtime / "marker.txt", skill / "SKILL.md", database)}

    result = verify_quickstart_receipt(receipt, artifact_hash_reader=lambda _: "sha256:expected")

    assert result["action"] == "concierge_installation_verified"
    assert result["mutated"] is False
    assert result["snapshot"] == {"canonical_media": 0, "pending_proposals": 0}
    assert result["native_hermes_checks"] == [
        "hermes mcp list",
        "hermes mcp test taste_database",
    ]
    assert before == {path: path.stat().st_mtime_ns for path in before}


def test_onboarding_forces_sequential_automation_questions_and_mcp_readback():
    skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")

    assert "Do not use one combined or multi-select picker" in skill
    assert "Ask about automatic promotion only after" in skill
    assert "promotion is unavailable" in skill
    assert "PTY-capable terminal" in skill
    assert "never trust the add command's exit code" in skill
    assert "hermes mcp list" in skill
    assert "exactly nine tools" in skill
    assert "start a new Hermes session" in skill


def test_quickstart_preflight_uses_the_selected_profile_data_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "legacy-local"))

    values = build_child_environment(tmp_path / "env", tmp_path / "profile-data")

    assert values["CONCIERGE_DATA_DIR"] == str(tmp_path / "profile-data")
    source = (ROOT / "scripts" / "concierge_quickstart.py").read_text(encoding="utf-8")
    assert '"--data-dir",\n            str(data_directory)' in source


def test_manifest_references_only_packaged_source_and_release_evidence():
    manifest = (ROOT / "manifest.yaml").read_text(encoding="utf-8")

    assert "PROJECT_STATUS.md" not in manifest
    assert "DEFERRED_WORK.md" not in manifest
    assert "docs/data-contract/compatibility-matrix.md" not in manifest
    assert "install_report: install-report.json" not in manifest


def test_api_proposal_write_receipt_redacts_private_source_context():
    proposal = Proposal.model_validate(
        {
            "id": "proposal-private-context",
            "target_media_item_id": "media-1",
            "kind": "observation",
            "proposed_observation": {
                "id": "observation-private-context",
                "scope": "work",
                "polarity": "positive",
                "dimension": "tone",
                "text": "Warm and strange",
                "provenance": "assistant_inferred",
                "privacy": "assistant_readable",
                "source_context": "private nested transcript excerpt",
                "confidence": 0.91,
                "review_state": "needs_review",
                "observed_on": "2026-08-10",
            },
            "source_context": "private proposal transcript excerpt",
            "confidence": 0.91,
            "review_state": "needs_review",
            "proposed_on": "2026-08-10",
        }
    )

    receipt = assistant_proposal_receipt_view(proposal)

    assert receipt["source_context"] == "[REDACTED]"
    assert receipt["proposed_observation"]["source_context"] == "[REDACTED]"
    api_source = (ROOT / "backend" / "app" / "api.py").read_text(encoding="utf-8")
    assert "assistant_proposal_receipt_view(proposal)" in api_source


def test_quickstart_hands_the_agent_an_exact_installed_ui_command(tmp_path: Path):
    runtime = tmp_path / "Concierge" / "packages" / "0.1.16-dev.4" / "artifact"
    data = tmp_path / "profile" / "concierge-data"
    environment = tmp_path / "Concierge" / "envs" / "0.1.16-dev.4"

    handoff = build_ui_handoff(runtime, data, environment, port=4173)

    assert handoff["url"] == "http://127.0.0.1:4173/"
    assert handoff["readiness_url"] == "http://127.0.0.1:4173/health"
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    assert handoff["launch_command"] == [
        str(python),
        "-I",
        str(runtime / "scripts" / "launch.py"),
        "--data-dir",
        str(data),
        "--port",
        "4173",
    ]
    assert "environment" not in handoff
