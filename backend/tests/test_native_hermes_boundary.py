from pathlib import Path
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
from scripts.concierge_package import (
    _assert_uninstall_launcher_is_outside_runtime,
    _installation_payload,
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
    assert "Do not search for a Hermes source checkout" in skill
    assert "HERMES_HOME=<HERMES_HOME> hermes mcp add" in skill
    assert "HERMES_HOME=<HERMES_HOME> hermes cron" in skill
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
