"""Pure normalized source/provenance envelope for semantic capture.

The envelope is deliberately separate from persistence and runtime session access.
It subclasses the four typed P1.2 proposal variants so their payload rules remain
owned by ``capture_contract`` while source identity and evidence metadata stay in
one flat, adapter-neutral shape.
"""

from datetime import date, datetime
from enum import Enum
from typing import Annotated, TypeAlias

from pydantic import (
    AliasChoices,
    Field,
    StringConstraints,
    TypeAdapter,
    field_validator,
    model_validator,
)

from .capture_contract import (
    MediaItemProposal,
    ObservationProposal,
    ProgressEventProposal,
    RatingEventProposal,
)
from .domain import ContractModel, Provenance


class SourceClass(str, Enum):
    """Closed source families supported by the beta contract."""

    SYNTHETIC_FIXTURE = "synthetic_fixture"
    HERMES_SESSION_SEARCH = "hermes_session_search"


class SourceRefSemantics(str, Enum):
    """Whether an adapter preserved its source reference or normalized it."""

    EXACT = "exact"
    NORMALIZED = "normalized"


class EvidenceForm(str, Enum):
    QUOTED = "quoted"
    NEAR_VERBATIM = "near_verbatim"


class EvidenceAttribution(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"


Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
StableReference = Annotated[str, StringConstraints(min_length=1)]
EvidenceText = Annotated[str, StringConstraints(min_length=1)]


class CaptureEnvelopeFields(ContractModel):
    """Source and provenance fields shared by every capture variant."""

    source_class: SourceClass
    source_ref: StableReference
    session_ref: StableReference = Field(
        validation_alias=AliasChoices("session_ref", "session_id")
    )
    message_ref: StableReference = Field(
        validation_alias=AliasChoices("message_ref", "message_id")
    )
    source_ref_semantics: SourceRefSemantics
    content_hash: Sha256Hex
    source_timestamp: datetime
    capture_timestamp: datetime
    quoted_evidence: EvidenceText
    evidence_form: EvidenceForm
    attribution: EvidenceAttribution
    identity_confidence: float = Field(ge=0, le=1)
    observed_on: date | None = None
    event_on: date | None = None
    ambiguity_notes: EvidenceText | None = None
    assistant_inference: EvidenceText | None = None

    @field_validator("source_ref", "session_ref", "message_ref")
    @classmethod
    def require_unmodified_stable_references(cls, value: str) -> str:
        if not value.strip() or any(character.isspace() for character in value):
            raise ValueError("source references must be nonblank and contain no whitespace")
        return value

    @field_validator("quoted_evidence", "ambiguity_notes", "assistant_inference")
    @classmethod
    def preserve_nonblank_evidence_text(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("evidence text must be nonblank")
        return value

    @field_validator("source_timestamp", "capture_timestamp")
    @classmethod
    def require_timezone_aware_timestamps(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("source and capture timestamps must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_source_reference(self) -> "CaptureEnvelopeFields":
        if self.source_class is SourceClass.SYNTHETIC_FIXTURE:
            expected = f"synthetic://concierge-e2e/{self.session_ref}/{self.message_ref}"
            if not self.source_ref.startswith("synthetic://concierge-e2e/"):
                raise ValueError(
                    "synthetic_fixture envelopes require a synthetic://concierge-e2e/ source_ref"
                )
            if (
                self.source_ref_semantics is SourceRefSemantics.NORMALIZED
                and self.source_ref != expected
            ):
                raise ValueError(
                    "normalized synthetic_fixture source_ref must encode "
                    "session_ref and message_ref"
                )
        elif self.source_class is SourceClass.HERMES_SESSION_SEARCH:
            expected = f"hermes://session/{self.session_ref}/message/{self.message_ref}"
            if not self.source_ref.startswith("hermes://session/"):
                raise ValueError(
                    "hermes_session_search envelopes require a hermes://session/ source_ref"
                )
            if (
                self.source_ref_semantics is SourceRefSemantics.NORMALIZED
                and self.source_ref != expected
            ):
                raise ValueError(
                    "normalized hermes_session_search source_ref must encode "
                    "session_ref and message_ref"
                )
        return self

    @model_validator(mode="after")
    def keep_inference_separate_from_explicit_source_facts(self) -> "CaptureEnvelopeFields":
        if self.provenance is Provenance.USER_EXPLICIT:
            if self.attribution is not EvidenceAttribution.USER:
                raise ValueError(
                    "user_explicit provenance requires user attribution"
                )
            if self.assistant_inference is not None:
                raise ValueError(
                    "user_explicit provenance cannot carry assistant_inference"
                )
        elif self.provenance is Provenance.ASSISTANT_INFERRED:
            if self.assistant_inference is None:
                raise ValueError(
                    "assistant_inferred provenance requires assistant_inference"
                )
        else:
            raise ValueError(
                "capture envelopes support only user_explicit or assistant_inferred provenance"
            )
        return self


class ObservationCaptureEnvelope(ObservationProposal, CaptureEnvelopeFields):
    """Flat envelope for one typed observation proposal."""

    @model_validator(mode="after")
    def require_observation_date(self) -> "ObservationCaptureEnvelope":
        if self.observed_on is None:
            raise ValueError("observed_on is required for observation envelopes")
        if self.event_on is not None:
            raise ValueError("event_on is not valid for observation envelopes")
        if self.observed_on != self.observation_event.observed_on:
            raise ValueError("observed_on must match observation_event.observed_on")
        return self


class MediaItemCaptureEnvelope(MediaItemProposal, CaptureEnvelopeFields):
    """Flat envelope for one targetless new-media proposal."""

    @model_validator(mode="after")
    def reject_media_event_dates(self) -> "MediaItemCaptureEnvelope":
        if self.observed_on is not None or self.event_on is not None:
            raise ValueError("media_item envelopes cannot carry observation or event dates")
        return self


class RatingEventCaptureEnvelope(RatingEventProposal, CaptureEnvelopeFields):
    """Flat envelope for one typed rating-event proposal."""

    @model_validator(mode="after")
    def require_rating_event_date(self) -> "RatingEventCaptureEnvelope":
        if self.event_on is None:
            raise ValueError("event_on is required for rating-event envelopes")
        if self.observed_on is not None:
            raise ValueError("observed_on is not valid for rating-event envelopes")
        if self.event_on != self.rating_event.rated_on:
            raise ValueError("event_on must match rating_event.rated_on")
        return self


class ProgressEventCaptureEnvelope(ProgressEventProposal, CaptureEnvelopeFields):
    """Flat envelope for one typed progress-event proposal."""

    @model_validator(mode="after")
    def require_progress_event_date(self) -> "ProgressEventCaptureEnvelope":
        if self.event_on is None:
            raise ValueError("event_on is required for progress-event envelopes")
        if self.observed_on is not None:
            raise ValueError("observed_on is not valid for progress-event envelopes")
        if self.event_on != self.progress_event.recorded_on:
            raise ValueError("event_on must match progress_event.recorded_on")
        return self


CaptureEnvelope: TypeAlias = Annotated[
    ObservationCaptureEnvelope
    | MediaItemCaptureEnvelope
    | RatingEventCaptureEnvelope
    | ProgressEventCaptureEnvelope,
    Field(discriminator="kind"),
]

_CAPTURE_ENVELOPE_ADAPTER = TypeAdapter(CaptureEnvelope)


def parse_capture_envelope(payload: object) -> CaptureEnvelope:
    """Validate one flat capture envelope without accessing or mutating a source."""

    return _CAPTURE_ENVELOPE_ADAPTER.validate_python(payload)
