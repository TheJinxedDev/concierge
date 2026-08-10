from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.automation_cron_identity import build_automation_job_specs
from app.automation_preferences import AutomationPreferences
from app.package_mcp import build_mcp_server_spec


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
