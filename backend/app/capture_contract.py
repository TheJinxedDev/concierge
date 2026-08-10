"""Pure typed proposal/event contract for Concierge semantic capture.

The persisted contract lives in :mod:`app.domain` so the export boundary and
injection-only capture adapters validate the same discriminated union. This
module remains the stable capture-facing import surface; it performs no
persistence, promotion, or canonical mutation.
"""

from .domain import (
    CaptureProposal,
    CaptureProposalBase,
    ConflictState,
    ContractModel,
    MediaItem,
    MediaItemProposal,
    NonBlankText,
    ObservationEvent,
    ObservationPolarity,
    ObservationProposal,
    ObservationScope,
    PrivacyLevel,
    ProgressEvent,
    ProgressEventProposal,
    ProgressRecord,
    Provenance,
    Rating,
    RatingEvent,
    RatingEventProposal,
    ReviewState,
    TypedEventProposal,
    _CAPTURE_PROPOSAL_ADAPTER,
    parse_capture_proposal,
    parse_typed_event_proposal,
)

__all__ = [
    "CaptureProposal",
    "CaptureProposalBase",
    "ConflictState",
    "ContractModel",
    "MediaItem",
    "MediaItemProposal",
    "NonBlankText",
    "ObservationEvent",
    "ObservationPolarity",
    "ObservationProposal",
    "ObservationScope",
    "PrivacyLevel",
    "ProgressEvent",
    "ProgressEventProposal",
    "ProgressRecord",
    "Provenance",
    "Rating",
    "RatingEvent",
    "RatingEventProposal",
    "ReviewState",
    "TypedEventProposal",
    "parse_capture_proposal",
    "parse_typed_event_proposal",
]
