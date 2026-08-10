"""Persisted onboarding choices for Concierge's independent automation lanes."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
from pathlib import Path
import tempfile

from .setup_contract import BacklogPolicy


class AutomationLane(str, Enum):
    FULLY_MANUAL = "fully_manual"
    SEMI_AUTO = "semi_auto"
    FULLY_AUTO = "fully_auto"
    PROMOTION_ONLY = "promotion_only"


@dataclass(frozen=True)
class AutomationPreferences:
    """One explicit onboarding decision, including explicit negative answers."""

    decision_id: str
    decided_at: str
    backlog_cron_enabled: bool
    recent_capture_cron_enabled: bool
    promotion_cron_enabled: bool
    backlog_policy: BacklogPolicy = BacklogPolicy.START_FRESH
    favorite_media_interview: bool = False

    def __post_init__(self) -> None:
        if not self.decision_id.strip():
            raise ValueError("decision id must not be blank")
        if not self.decided_at.strip():
            raise ValueError("decided_at must not be blank")
        if self.favorite_media_interview and not self.backlog_cron_enabled:
            raise ValueError("favorite media interview requires backlog cron")

    @property
    def lane(self) -> AutomationLane:
        if self.recent_capture_cron_enabled and self.promotion_cron_enabled:
            return AutomationLane.FULLY_AUTO
        if self.promotion_cron_enabled:
            return AutomationLane.PROMOTION_ONLY
        if self.recent_capture_cron_enabled:
            return AutomationLane.SEMI_AUTO
        return AutomationLane.FULLY_MANUAL

    def as_payload(self) -> dict[str, object]:
        return {
            "decision_id": self.decision_id,
            "decided_at": self.decided_at,
            "backlog_cron_enabled": self.backlog_cron_enabled,
            "recent_capture_cron_enabled": self.recent_capture_cron_enabled,
            "promotion_cron_enabled": self.promotion_cron_enabled,
            "backlog_policy": self.backlog_policy.value,
            "favorite_media_interview": self.favorite_media_interview,
            "lane": self.lane.value,
            "schema_version": "1.1",
        }

    @classmethod
    def from_payload(cls, payload: object) -> "AutomationPreferences":
        if not isinstance(payload, dict):
            raise ValueError("automation preferences must be an object")
        if payload.get("schema_version") not in {"1.0", "1.1"}:
            raise ValueError("unsupported automation preferences schema")
        fields = {
            "decision_id",
            "decided_at",
            "backlog_cron_enabled",
            "recent_capture_cron_enabled",
            "promotion_cron_enabled",
            "backlog_policy",
            "favorite_media_interview",
        }
        if set(payload) - fields - {"lane", "schema_version"}:
            raise ValueError("automation preferences contain unknown fields")
        values = {field: payload.get(field) for field in fields}
        values["backlog_policy"] = payload.get(
            "backlog_policy", BacklogPolicy.START_FRESH.value
        )
        if any(
            not isinstance(values[field], bool)
            for field in fields - {"decision_id", "decided_at", "backlog_policy"}
        ):
            raise ValueError("automation cron choices must be explicit booleans")
        try:
            values["backlog_policy"] = BacklogPolicy(values["backlog_policy"])
        except (TypeError, ValueError) as error:
            raise ValueError("automation backlog policy must be explicit") from error
        return cls(**values)


class AutomationPreferencesStore:
    """Small atomic JSON store; absence means onboarding has not completed."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    def read(self) -> AutomationPreferences | None:
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("automation preferences are unreadable") from error
        return AutomationPreferences.from_payload(payload)

    def save(self, preferences: AutomationPreferences) -> AutomationPreferences:
        existing = self.read()
        if existing is not None and existing.decision_id == preferences.decision_id:
            if existing != preferences:
                raise ValueError("decision id already records different preferences")
            return existing

        self._path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=self._path.parent, delete=False
        ) as handle:
            json.dump(preferences.as_payload(), handle, indent=2, sort_keys=True)
            handle.write("\n")
            temporary = Path(handle.name)
        temporary.replace(self._path)
        return preferences
