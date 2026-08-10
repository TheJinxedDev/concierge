"""Fixture-only synthetic source tracer for the first disposable beta slice.

The tracer accepts a caller-owned application service and a fixture-owned source
catalog. It normalizes each case through the capture-envelope contract, resolves
canonical identity through LibraryService, and submits only pending proposals
through the existing MCP proposal adapter. It is not imported by application
startup, MCP startup, cron, or live session capture.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .capture_envelope import (
    MediaItemCaptureEnvelope,
    ObservationCaptureEnvelope,
    parse_capture_envelope,
)
from .domain import Observation, Proposal, Provenance, ReviewState
from .library_service import LibraryService
from .mcp_server import submit_pending_proposal_record


_SYNTHETIC_SOURCE_CLASS = "synthetic_fixture"


def trace_synthetic_session(
    library: LibraryService,
    catalog_payload: object,
) -> dict[str, Any]:
    """Trace one synthetic session into pending proposals without canonical writes."""
    if not isinstance(catalog_payload, dict):
        raise ValueError("synthetic source catalog must be an object")
    if catalog_payload.get("source_class") != _SYNTHETIC_SOURCE_CLASS:
        raise ValueError("synthetic source catalog must use source_class=synthetic_fixture")

    cases = catalog_payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("synthetic source catalog must contain cases")
    session_refs = {case.get("session_ref") for case in cases if isinstance(case, dict)}
    if len(session_refs) != 1 or None in session_refs:
        raise ValueError("synthetic source cases must belong to one session")

    receipts: list[dict[str, Any]] = []
    processed_case_ids: list[str] = []
    existing_proposals = {proposal.id: proposal for proposal in library.list_proposals()}
    submitted_count = 0
    replayed_count = 0

    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("synthetic source cases must be objects")
        case_id = _required_text(case, "case_id")
        candidate = case.get("candidate")
        expected = case.get("expected")
        if not isinstance(candidate, dict) or not isinstance(expected, dict):
            raise ValueError(f"synthetic case {case_id!r} must include candidate and expected")
        if case.get("source_class") != _SYNTHETIC_SOURCE_CLASS:
            raise ValueError(f"synthetic case {case_id!r} has an invalid source class")

        resolution = expected.get("resolution")
        matches = library.search_media_titles(_required_text(candidate, "title"))
        if resolution == "canonical":
            canonical_id = _required_text(expected, "canonical_id")
            if len(matches) != 1 or matches[0].id != canonical_id:
                raise ValueError(
                    f"synthetic case {case_id!r} did not resolve to its expected canonical media item"
                )
            envelope = parse_capture_envelope(
                _observation_envelope_payload(case, case_id, canonical_id)
            )
            proposal_payload = _observation_proposal_payload(envelope)
        elif resolution == "new_unambiguous":
            if matches:
                raise ValueError(
                    f"synthetic case {case_id!r} unexpectedly matched canonical media"
                )
            envelope = parse_capture_envelope(
                _media_item_envelope_payload(case, case_id)
            )
            proposal_payload = _media_item_proposal_payload(envelope)
        else:
            raise ValueError(f"synthetic case {case_id!r} has unsupported resolution")

        proposal = Proposal.model_validate(proposal_payload)
        prior = existing_proposals.get(proposal.id)
        if prior is not None:
            if prior != proposal:
                raise ValueError(f"synthetic proposal id conflict: {proposal.id!r}")
            receipt = {
                "proposal": _serialize_proposal_receipt(prior),
                "canonical_media_changed": False,
                "replayed": True,
            }
            replayed_count += 1
        else:
            receipt = submit_pending_proposal_record(library, proposal_payload)
            existing_proposals[proposal.id] = proposal
            receipt["replayed"] = False
            submitted_count += 1

        receipt["provenance"] = envelope.provenance.value
        receipt["review_state"] = proposal.review_state.value

        receipts.append({"case_id": case_id, **receipt})
        processed_case_ids.append(case_id)

    pending_proposal_count = sum(
        proposal.review_state is ReviewState.NEEDS_REVIEW
        for proposal in library.list_proposals()
    )
    return {
        "source_class": _SYNTHETIC_SOURCE_CLASS,
        "session_ref": next(iter(session_refs)),
        "processed_case_ids": processed_case_ids,
        "receipts": receipts,
        "run_report": {
            "schema_version": "1.0",
            "status": "complete",
            "source_count": len(cases),
            "submitted_count": submitted_count,
            "replayed_count": replayed_count,
            "pending_proposal_count": pending_proposal_count,
            "canonical_media_changed": False,
        },
    }


def _observation_envelope_payload(
    case: dict[str, Any], case_id: str, canonical_id: str
) -> dict[str, Any]:
    observed_on = _source_date(case)
    return {
        **_shared_envelope_payload(case, case_id, "observation"),
        "kind": "observation",
        "target_media_item_id": canonical_id,
        "observation_event": {
            "event_id": f"observation-event-{case_id}",
            "scope": "work",
            "polarity": "neutral",
            "dimension": "consumption",
            "text": _required_text(case, "quoted_evidence"),
            "observed_on": observed_on,
        },
        "observed_on": observed_on,
    }


def _media_item_envelope_payload(case: dict[str, Any], case_id: str) -> dict[str, Any]:
    candidate = case["candidate"]
    proposed_media_item = {
        "id": _candidate_id(candidate),
        "title": _required_text(candidate, "title"),
        "category": _required_text(candidate, "category"),
        "status": _required_text(candidate, "status"),
    }
    return {
        **_shared_envelope_payload(case, case_id, "media_item"),
        "kind": "media_item",
        "proposed_media_item": proposed_media_item,
    }


def _shared_envelope_payload(
    case: dict[str, Any], case_id: str, kind: str
) -> dict[str, Any]:
    session_ref = _required_text(case, "session_ref")
    message_ref = _required_text(case, "message_ref")
    proposal_kind = "media" if kind == "media_item" else kind
    return {
        "id": f"proposal-{proposal_kind}-{case_id}",
        "source_context": _source_context(case),
        "provenance": _required_text(case, "provenance"),
        "confidence": case["identity_confidence"],
        "review_state": "needs_review",
        "idempotency_key": f"capture:{session_ref}:{message_ref}:{kind}",
        "proposed_on": _source_date(case),
        "source_class": _required_text(case, "source_class"),
        "source_ref": _required_text(case, "source_ref"),
        "session_ref": session_ref,
        "message_ref": message_ref,
        "source_ref_semantics": _required_text(case, "source_ref_semantics"),
        "content_hash": _required_text(case, "content_hash"),
        "source_timestamp": _required_text(case, "source_timestamp"),
        "capture_timestamp": _required_text(case, "capture_timestamp"),
        "quoted_evidence": _required_text(case, "quoted_evidence"),
        "evidence_form": _required_text(case, "evidence_form"),
        "attribution": _required_text(case, "attribution"),
        "identity_confidence": case["identity_confidence"],
    }


def _observation_proposal_payload(envelope: ObservationCaptureEnvelope) -> dict[str, Any]:
    source_context = _envelope_source_context(envelope)
    event = envelope.observation_event
    observation = Observation(
        id=event.event_id,
        scope=event.scope,
        subject_id=event.subject_id,
        subject_label=event.subject_label,
        polarity=event.polarity,
        dimension=event.dimension,
        text=event.text,
        provenance=Provenance.ASSISTANT_INFERRED,
        privacy=event.privacy,
        source_context=source_context,
        confidence=envelope.identity_confidence,
        review_state=ReviewState.NEEDS_REVIEW,
        observed_on=event.observed_on,
    )
    return {
        "id": envelope.id,
        "target_media_item_id": envelope.target_media_item_id,
        "kind": "observation",
        "proposed_observation": observation.model_dump(
            mode="json", exclude_none=True, exclude_defaults=False
        ),
        "source_context": source_context,
        "confidence": envelope.confidence,
        "review_state": envelope.review_state.value,
        "proposed_on": envelope.proposed_on.isoformat(),
    }


def _media_item_proposal_payload(envelope: MediaItemCaptureEnvelope) -> dict[str, Any]:
    return {
        "id": envelope.id,
        "kind": "media_item",
        "proposed_media_item": envelope.proposed_media_item.model_dump(
            mode="json", exclude_none=True, exclude_defaults=False
        ),
        "source_context": _envelope_source_context(envelope),
        "confidence": envelope.confidence,
        "review_state": envelope.review_state.value,
        "proposed_on": envelope.proposed_on.isoformat(),
    }


def _serialize_proposal_receipt(proposal: Proposal) -> dict[str, Any]:
    """Match the existing proposal adapter's compact receipt serialization."""
    serialized = proposal.model_dump(mode="json", exclude_none=True, exclude_defaults=True)
    serialized["review_state"] = proposal.review_state.value
    return serialized


def _envelope_source_context(envelope: Any) -> str:
    return (
        f"{envelope.source_ref} "
        f"(session_ref={envelope.session_ref}; message_ref={envelope.message_ref}): "
        f"{envelope.quoted_evidence}"
    )


def _source_context(case: dict[str, Any]) -> str:
    return (
        f"{_required_text(case, 'source_ref')} "
        f"(session_ref={_required_text(case, 'session_ref')}; "
        f"message_ref={_required_text(case, 'message_ref')}): "
        f"{_required_text(case, 'quoted_evidence')}"
    )


def _source_date(case: dict[str, Any]) -> str:
    return datetime.fromisoformat(_required_text(case, "source_timestamp")).date().isoformat()


def _candidate_id(candidate: dict[str, Any]) -> str:
    category = _required_text(candidate, "category")
    title = _required_text(candidate, "title")
    slug = "-".join("".join(character for character in word.casefold() if character.isalnum()) for word in title.split())
    return f"{category}-{slug}-fixture"


def _required_text(payload: dict[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"synthetic source field {field!r} must be nonblank text")
    return value
