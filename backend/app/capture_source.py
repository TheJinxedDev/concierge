"""Deterministic source normalization and discovery guards for capture.

This module owns only the source-side boundary for the disposable capture lane:
it accepts caller-owned source records, derives stable references and exact
content hashes, orders them deterministically, and checks that a source has not
changed since discovery. It does not read Hermes, persist capture state, acquire
locks, submit proposals, or advance a cursor.

The two concrete adapters are intentionally injection-only. They have the same
normalized output shape as a future production adapter, but neither adapter
opens a session store or performs filesystem/network I/O.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
from typing import Any, Protocol


from .capture_envelope import (
    CaptureEnvelope,
    SourceClass,
    SourceRefSemantics,
    parse_capture_envelope,
)
from .capture_state import ReasonCode


class SourceAdapterBoundaryError(ValueError):
    """Raised when a source record crosses the wrong adapter boundary."""


class SourceDisposition(str, Enum):
    """Whether the discovered source may reach the proposal submitter."""

    SUBMIT = "submit"
    HOLD = "hold"


class SourceStateBoundary(str, Enum):
    """Whether the cursor/state boundary remains open for the source."""

    OPEN = "open"
    HELD = "held"


@dataclass(frozen=True)
class SourceIdentity:
    """Stable identity for one source message, independent of its content."""

    source_class: SourceClass
    source_ref: str
    session_ref: str
    message_ref: str
    source_ref_semantics: SourceRefSemantics


@dataclass(frozen=True)
class DiscoveredSource:
    """Immutable discovery snapshot used for a later freshness check."""

    ordinal: int
    identity: SourceIdentity
    content_hash: str
    envelope: CaptureEnvelope


@dataclass(frozen=True)
class SourceProcessingDecision:
    """The source-side decision made immediately before proposal submission."""

    identity: SourceIdentity
    current_envelope: CaptureEnvelope
    discovered_hash: str
    current_hash: str
    disposition: SourceDisposition
    state_boundary: SourceStateBoundary
    cursor_advance_allowed: bool
    reason_code: ReasonCode | None
    submitted: bool


class SourceAdapter(Protocol):
    """Injection-only protocol shared by fixture and production-shaped adapters."""

    source_class: SourceClass

    def payloads(self) -> tuple[object, ...]:
        """Return caller-owned raw payload copies without accessing a source."""


def hash_source_content(content: str) -> str:
    """Hash the exact UTF-8 source content; do not trim or normalize it."""

    if not isinstance(content, str):
        raise TypeError("source content must be text")
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def normalize_source_envelope(
    payload: object,
    *,
    expected_source_class: SourceClass | str | None = None,
) -> CaptureEnvelope:
    """Build one validated envelope with derived reference and content fields.

    ``content_hash`` is always recomputed from the exact ``quoted_evidence``
    bytes. A source-provided hash is treated as untrusted metadata and is
    overwritten by the computed value, so a changed source cannot hide behind a
    stale declared hash.
    """

    normalized = _payload_as_mapping(payload)
    source_class = _parse_source_class(normalized.get("source_class"))
    if expected_source_class is not None:
        expected = _parse_source_class(expected_source_class)
        if source_class is not expected:
            raise SourceAdapterBoundaryError(
                f"source adapter {expected.value} cannot accept "
                f"{source_class.value} records"
            )

    session_ref = _require_stable_reference(normalized, "session_ref")
    message_ref = _require_stable_reference(normalized, "message_ref")
    semantics = _parse_reference_semantics(normalized.get("source_ref_semantics"))

    if semantics is SourceRefSemantics.NORMALIZED:
        normalized["source_ref"] = normalized_source_ref(
            source_class,
            session_ref,
            message_ref,
        )

    quoted_evidence = normalized.get("quoted_evidence")
    if not isinstance(quoted_evidence, str) or not quoted_evidence:
        raise ValueError("quoted_evidence must be nonblank source text")
    normalized["content_hash"] = hash_source_content(quoted_evidence)

    return parse_capture_envelope(normalized)


def normalized_source_ref(
    source_class: SourceClass | str,
    session_ref: str,
    message_ref: str,
) -> str:
    """Derive the canonical URI for normalized fixture or Hermes references."""

    parsed_class = _parse_source_class(source_class)
    session = _validate_stable_reference_value(session_ref, "session_ref")
    message = _validate_stable_reference_value(message_ref, "message_ref")
    if parsed_class is SourceClass.SYNTHETIC_FIXTURE:
        return f"synthetic://concierge-e2e/{session}/{message}"
    return f"hermes://session/{session}/message/{message}"


def discover_sources(adapter: SourceAdapter) -> tuple[DiscoveredSource, ...]:
    """Normalize and deterministically order one adapter's source snapshot."""

    envelopes = [
        normalize_source_envelope(
            payload,
            expected_source_class=adapter.source_class,
        )
        for payload in adapter.payloads()
    ]
    envelopes.sort(key=_source_order_key)

    discovered: list[DiscoveredSource] = []
    seen: set[SourceIdentity] = set()
    for ordinal, envelope in enumerate(envelopes):
        identity = _source_identity(envelope)
        if identity in seen:
            raise ValueError(
                f"duplicate stable source identity: {identity.source_ref!r}"
            )
        seen.add(identity)
        discovered.append(
            DiscoveredSource(
                ordinal=ordinal,
                identity=identity,
                content_hash=envelope.content_hash,
                envelope=envelope,
            )
        )
    return tuple(discovered)


def process_discovered_source(
    discovered: DiscoveredSource,
    current_payload: object,
    *,
    submitter: Callable[[CaptureEnvelope], object],
) -> SourceProcessingDecision:
    """Gate proposal submission on an unchanged discovery snapshot.

    A changed source returns a held decision and never invokes ``submitter``.
    The pure result tells the later state owner to retain the cursor; this
    function does not write that state itself.
    """

    current_envelope = normalize_source_envelope(
        current_payload,
        expected_source_class=discovered.envelope.source_class,
    )
    current_identity = _source_identity(current_envelope)
    unchanged = (
        current_identity == discovered.identity
        and current_envelope.content_hash == discovered.content_hash
    )
    if not unchanged:
        return SourceProcessingDecision(
            identity=current_identity,
            current_envelope=current_envelope,
            discovered_hash=discovered.content_hash,
            current_hash=current_envelope.content_hash,
            disposition=SourceDisposition.HOLD,
            state_boundary=SourceStateBoundary.HELD,
            cursor_advance_allowed=False,
            reason_code=ReasonCode.SOURCE_CHANGED_AFTER_DISCOVERY,
            submitted=False,
        )

    submitter(current_envelope)
    return SourceProcessingDecision(
        identity=current_identity,
        current_envelope=current_envelope,
        discovered_hash=discovered.content_hash,
        current_hash=current_envelope.content_hash,
        disposition=SourceDisposition.SUBMIT,
        state_boundary=SourceStateBoundary.OPEN,
        cursor_advance_allowed=True,
        reason_code=None,
        submitted=True,
    )


class _InjectedSourceAdapter:
    """Common no-I/O storage for the two visibly distinct adapters."""

    source_class: SourceClass

    def __init__(self, payloads: Iterable[object]) -> None:
        self._payloads = tuple(deepcopy(payload) for payload in payloads)

    def payloads(self) -> tuple[object, ...]:
        return tuple(deepcopy(payload) for payload in self._payloads)


class SyntheticFixtureSourceAdapter(_InjectedSourceAdapter):
    """Normalize only explicitly synthetic, caller-owned fixture records."""

    source_class = SourceClass.SYNTHETIC_FIXTURE


class HermesSessionSearchSourceAdapter(_InjectedSourceAdapter):
    """Normalize production-shaped records supplied by a future session reader.

    The adapter accepts already-fetched records for structural tests only. It
    deliberately has no session-search client, filesystem access, or Hermes
    profile lookup.
    """

    source_class = SourceClass.HERMES_SESSION_SEARCH


def _payload_as_mapping(payload: object) -> dict[str, Any]:
    if isinstance(payload, Mapping):
        return deepcopy(dict(payload))
    model_dump = getattr(payload, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump(mode="python")
        if isinstance(dumped, Mapping):
            return deepcopy(dict(dumped))
    raise TypeError("source envelope payload must be a mapping or contract model")


def _parse_source_class(value: object) -> SourceClass:
    try:
        return value if isinstance(value, SourceClass) else SourceClass(value)
    except (TypeError, ValueError) as error:
        raise ValueError("source_class must be synthetic_fixture or hermes_session_search") from error


def _parse_reference_semantics(value: object) -> SourceRefSemantics:
    try:
        return value if isinstance(value, SourceRefSemantics) else SourceRefSemantics(value)
    except (TypeError, ValueError) as error:
        raise ValueError("source_ref_semantics must be exact or normalized") from error


def _require_stable_reference(payload: Mapping[str, Any], field: str) -> str:
    return _validate_stable_reference_value(payload.get(field), field)


def _validate_stable_reference_value(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or any(character.isspace() for character in value):
        raise ValueError(f"{field} must be nonblank and contain no whitespace")
    return value


def _source_identity(envelope: CaptureEnvelope) -> SourceIdentity:
    return SourceIdentity(
        source_class=envelope.source_class,
        source_ref=envelope.source_ref,
        session_ref=envelope.session_ref,
        message_ref=envelope.message_ref,
        source_ref_semantics=envelope.source_ref_semantics,
    )


def _source_order_key(envelope: CaptureEnvelope) -> tuple[datetime, str, str, str]:
    return (
        envelope.source_timestamp.astimezone(timezone.utc),
        envelope.session_ref,
        envelope.message_ref,
        envelope.source_ref,
    )
