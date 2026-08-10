"""Pure completed-session source selection for the future semi-auto lane.

This module deliberately accepts caller-owned, already-normalized session and
message records. It does not open Hermes state.db, inspect the active session,
read credentials, persist a watermark, schedule cron, or create proposals.
Those runtime boundaries belong to a later adapter/worker slice.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
import json
from typing import Any, Iterable, Mapping, Protocol

from pydantic import Field, TypeAdapter, model_validator

from .domain import ContractModel, NonBlankText


class SessionKind(str, Enum):
    """Lineage classifications supplied by the owning session reader."""

    ROOT = "root"
    BRANCH = "branch"
    COMPRESSION_CONTINUATION = "compression_continuation"
    DELEGATE = "delegate"
    TOOL = "tool"


@dataclass(frozen=True)
class CompletedSessionMessage:
    """One immutable, caller-owned message snapshot."""

    message_id: int
    role: str
    content: str | None
    timestamp: datetime
    active: bool = True

    def __post_init__(self) -> None:
        if isinstance(self.message_id, bool) or self.message_id < 0:
            raise ValueError("message_id must be non-negative")
        if not self.role.strip():
            raise ValueError("message role must be nonblank")
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise ValueError("message timestamp must include a timezone")
        if self.content is not None and not isinstance(self.content, str):
            raise TypeError("message content must be text or None")


@dataclass(frozen=True)
class CompletedSessionSnapshot:
    """One completed-session candidate supplied by a future source adapter."""

    session_id: str
    source: str
    started_at: datetime
    ended_at: datetime | None
    end_reason: str | None
    kind: SessionKind
    archived: bool = False
    messages: tuple[CompletedSessionMessage, ...] = ()

    def __post_init__(self) -> None:
        if not self.session_id.strip() or any(char.isspace() for char in self.session_id):
            raise ValueError("session_id must be nonblank and contain no whitespace")
        if not self.source.strip():
            raise ValueError("session source must be nonblank")
        if self.started_at.tzinfo is None or self.started_at.utcoffset() is None:
            raise ValueError("session started_at must include a timezone")
        if self.ended_at is not None:
            if self.ended_at.tzinfo is None or self.ended_at.utcoffset() is None:
                raise ValueError("session ended_at must include a timezone")
            if self.ended_at < self.started_at:
                raise ValueError("session ended_at cannot precede started_at")
        if not isinstance(self.kind, SessionKind):
            raise TypeError("session kind must be a SessionKind")
        messages = tuple(self.messages)
        if len({message.message_id for message in messages}) != len(messages):
            raise ValueError("session message IDs must be unique")
        object.__setattr__(self, "messages", messages)


class CompletedSessionWatermark(ContractModel):
    """Durable-order marker for one completed session's last user message."""

    session_id: NonBlankText
    session_ended_at: datetime
    last_user_message_id: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_watermark(self) -> "CompletedSessionWatermark":
        if not self.session_id.strip() or any(char.isspace() for char in self.session_id):
            raise ValueError("watermark session_id must be nonblank and contain no whitespace")
        if self.session_ended_at.tzinfo is None or self.session_ended_at.utcoffset() is None:
            raise ValueError("watermark session_ended_at must include a timezone")
        return self


@dataclass(frozen=True)
class CompletedSessionBatch:
    """Ordered source records plus the next durable-order marker."""

    sessions: tuple[CompletedSessionSnapshot, ...]
    next_watermark: CompletedSessionWatermark | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "sessions", tuple(self.sessions))


class HermesSessionSearchReader(Protocol):
    """Narrow injected shape for a future Hermes-owned session reader."""

    def list_sessions(self) -> Iterable[Mapping[str, object]]:
        """Return session rows without opening a store in this package."""

    def list_messages(self, session_id: str) -> Iterable[Mapping[str, object]]:
        """Return message rows for one caller-selected session."""


class HermesCompletedSessionSourceAdapter:
    """Normalize caller-supplied Hermes session/message rows without I/O."""

    def __init__(self, reader: HermesSessionSearchReader) -> None:
        self._reader = reader

    def read_snapshots(self) -> tuple[CompletedSessionSnapshot, ...]:
        """Read one injected snapshot from the reader's two narrow methods."""

        rows = tuple(self._reader.list_sessions())
        end_reasons = {
            _required_text(row, "id"): row.get("end_reason")
            for row in rows
        }
        snapshots: list[CompletedSessionSnapshot] = []
        for row in rows:
            session_id = _required_text(row, "id")
            parent_session_id = _optional_text(row.get("parent_session_id"))
            messages = tuple(
                _message_from_hermes_row(message_row)
                for message_row in self._reader.list_messages(session_id)
            )
            snapshots.append(
                CompletedSessionSnapshot(
                    session_id=session_id,
                    source=_required_text(row, "source"),
                    started_at=_timestamp_from_hermes(row.get("started_at"), "started_at"),
                    ended_at=_timestamp_from_hermes(
                        row.get("ended_at"), "ended_at", allow_none=True
                    ),
                    end_reason=_optional_text(row.get("end_reason")),
                    kind=_classify_session_kind(
                        source=_required_text(row, "source"),
                        parent_session_id=parent_session_id,
                        parent_end_reason=(
                            _optional_text(row.get("parent_end_reason"))
                            or (
                                end_reasons.get(parent_session_id)
                                if parent_session_id is not None
                                else None
                            )
                        ),
                        model_config=row.get("model_config"),
                    ),
                    archived=_bool_from_hermes(row.get("archived", False), "archived"),
                    messages=messages,
                )
            )
        return tuple(snapshots)


_ELIGIBLE_SESSION_KINDS = frozenset({SessionKind.ROOT, SessionKind.BRANCH})


def select_completed_sessions(
    sessions: Iterable[CompletedSessionSnapshot],
    *,
    as_of: datetime,
    watermark: CompletedSessionWatermark | None = None,
    include_archived: bool = False,
) -> CompletedSessionBatch:
    """Select a stable, bounded-independent prefix of completed source records.

    A session is eligible only when it ended strictly before ``as_of``, is a
    root/branch conversation, is not archived by default, and has at least one
    active user message. Records are ordered by ``ended_at`` then ``session_id``;
    messages are ordered by timestamp then row ID. A same-session watermark
    removes all messages at or before its last processed user-message ID.

    The function is intentionally pure. The caller must persist the returned
    ``next_watermark`` only after downstream proposal/claim/report work has
    reached its own terminal safety point.
    """

    _require_aware(as_of, "as_of")
    records = tuple(sessions)
    by_id: dict[str, CompletedSessionSnapshot] = {}
    for record in records:
        if record.session_id in by_id:
            raise ValueError(f"duplicate session_id: {record.session_id}")
        by_id[record.session_id] = record

    watermark_key: tuple[datetime, str] | None = None
    if watermark is not None:
        bound = by_id.get(watermark.session_id)
        if bound is None:
            raise ValueError(f"watermark session is not present: {watermark.session_id}")
        if bound.ended_at is None:
            raise ValueError("watermark session must have ended_at")
        if bound.ended_at != watermark.session_ended_at:
            raise ValueError("watermark ended_at does not match the source session")
        if not any(
            message.active
            and message.role == "user"
            and message.message_id == watermark.last_user_message_id
            for message in bound.messages
        ):
            raise ValueError(
                "watermark last_user_message_id must identify an active user message"
            )
        watermark_key = (bound.ended_at, bound.session_id)

    selected: list[CompletedSessionSnapshot] = []
    for record in sorted(
        records,
        key=lambda item: (item.ended_at or as_of, item.session_id),
    ):
        if record.ended_at is None or record.ended_at >= as_of:
            continue
        if record.kind not in _ELIGIBLE_SESSION_KINDS:
            continue
        if record.archived and not include_archived:
            continue
        record_key = (record.ended_at, record.session_id)
        if watermark_key is not None and record_key < watermark_key:
            continue

        messages = tuple(
            sorted(
                (message for message in record.messages if message.active),
                key=lambda message: (message.timestamp, message.message_id),
            )
        )
        if watermark is not None and record.session_id == watermark.session_id:
            messages = tuple(
                message
                for message in messages
                if message.message_id > watermark.last_user_message_id
            )
        if not any(message.role == "user" for message in messages):
            continue
        selected.append(replace(record, messages=messages))

    next_watermark = None
    if selected:
        last = selected[-1]
        user_message_ids = [
            message.message_id for message in last.messages if message.role == "user"
        ]
        assert last.ended_at is not None
        assert user_message_ids
        next_watermark = CompletedSessionWatermark(
            session_id=last.session_id,
            session_ended_at=last.ended_at,
            last_user_message_id=max(user_message_ids),
        )

    return CompletedSessionBatch(
        sessions=tuple(selected),
        next_watermark=next_watermark,
    )


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must include a timezone")


_COMPLETED_SESSION_WATERMARK_ADAPTER = TypeAdapter(CompletedSessionWatermark)


def parse_completed_session_watermark(payload: object) -> CompletedSessionWatermark:
    """Validate one portable completed-session watermark payload."""

    return _COMPLETED_SESSION_WATERMARK_ADAPTER.validate_python(payload)


def completed_session_order_key(
    watermark: CompletedSessionWatermark,
) -> tuple[datetime, str, int]:
    """Return the total order used for cross-session watermark comparisons."""

    if not isinstance(watermark, CompletedSessionWatermark):
        raise TypeError("watermark must be a CompletedSessionWatermark")
    return (
        watermark.session_ended_at,
        watermark.session_id,
        watermark.last_user_message_id,
    )


def _required_text(row: Mapping[str, object], field: str) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Hermes session row requires nonblank {field}")
    return value


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("Hermes text fields must be text or None")
    return value


def _timestamp_from_hermes(
    value: object,
    field: str,
    *,
    allow_none: bool = False,
) -> datetime | None:
    if value is None and allow_none:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        parsed = datetime.fromtimestamp(float(value), tz=timezone.utc)
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise TypeError(f"Hermes {field} must be an aware datetime, ISO string, or epoch")
    _require_aware(parsed, field)
    return parsed


def _bool_from_hermes(value: object, field: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in {0, 1}:
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"0", "false", "no", "off"}:
            return False
        if normalized in {"1", "true", "yes", "on"}:
            return True
    raise ValueError(f"Hermes {field} must be boolean-shaped")


def _model_config_mapping(value: object) -> Mapping[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as error:
            raise ValueError("Hermes model_config must be valid JSON") from error
        if isinstance(parsed, Mapping):
            return parsed
    raise TypeError("Hermes model_config must be an object, JSON object, or None")


def _classify_session_kind(
    *,
    source: str,
    parent_session_id: str | None,
    parent_end_reason: str | None,
    model_config: object,
) -> SessionKind:
    if source == "tool":
        return SessionKind.TOOL
    if parent_session_id is None:
        return SessionKind.ROOT
    config = _model_config_mapping(model_config)
    if config.get("_delegate_from") is not None:
        return SessionKind.DELEGATE
    if parent_end_reason == "compression":
        return SessionKind.COMPRESSION_CONTINUATION
    if config.get("_branched_from") is not None or parent_end_reason == "branched":
        return SessionKind.BRANCH
    # Unknown child lineage is excluded rather than promoted to a conversation.
    return SessionKind.DELEGATE


def _message_from_hermes_row(row: Mapping[str, object]) -> CompletedSessionMessage:
    message_id = row.get("id", row.get("message_id"))
    if not isinstance(message_id, int) or isinstance(message_id, bool):
        raise TypeError("Hermes message row requires an integer id")
    role = row.get("role")
    if not isinstance(role, str):
        raise TypeError("Hermes message row requires a text role")
    content = row.get("content")
    if content is not None and not isinstance(content, str):
        raise TypeError("Hermes message content must be text or None")
    return CompletedSessionMessage(
        message_id=message_id,
        role=role,
        content=content,
        timestamp=_timestamp_from_hermes(row.get("timestamp"), "timestamp"),
        active=_bool_from_hermes(row.get("active", True), "active"),
    )
