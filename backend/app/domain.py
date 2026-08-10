"""Portable, provenance-preserving media record primitives.

This module intentionally has no database dependency. It defines the first
export contract so future persistence layers must preserve these semantics.
"""

from datetime import date
from enum import Enum
from typing import Annotated, Literal, TypeAlias

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    TypeAdapter,
    model_validator,
)

from .categories_generated import CATEGORY_DEFINITIONS, MediaCategory


NonBlankText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class ContractModel(BaseModel):
    """Base for export records: typos are data-loss risks, not harmless extras."""

    model_config = ConfigDict(extra="forbid")


class ConsumptionStatus(str, Enum):
    PLANNED = "planned"
    CURRENTLY_CONSUMING = "currently_consuming"
    PAUSED = "paused"
    FINISHED = "finished"
    DROPPED = "dropped"
    SAMPLED = "sampled"
    REWATCHING = "rewatching"
    REWATCHED = "rewatched"
    NOT_INTERESTED = "not_interested"
    AVOIDING = "avoiding"


class ProgressUnit(str, Enum):
    PERCENT = "percent"
    EPISODE = "episode"
    CHAPTER = "chapter"
    HOUR = "hour"
    INSTALLMENT = "installment"


class PrivacyLevel(str, Enum):
    ASSISTANT_READABLE = "assistant_readable"
    PRIVATE = "private"
    EXCLUDE_FROM_RECOMMENDATIONS = "exclude_from_recommendations"


class ObservationScope(str, Enum):
    WORK = "work"
    SEASON = "season"
    EDITION = "edition"
    PLATFORM = "platform"
    ADAPTATION = "adaptation"
    ARC = "arc"
    EPISODE_CHAPTER = "episode_chapter"
    CHARACTER = "character"
    SCENE = "scene"
    MECHANIC = "mechanic"
    CREATOR = "creator"
    CHANNEL = "channel"
    VIDEO = "video"


class ObservationPolarity(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    MIXED = "mixed"
    NEUTRAL = "neutral"


class Provenance(str, Enum):
    MANUAL = "manual"
    USER_EXPLICIT = "user_explicit"
    ASSISTANT_INFERRED = "assistant_inferred"
    IMPORTED_METADATA = "imported_metadata"
    EXTERNAL_REFERENCE = "external_reference"


class ReviewState(str, Enum):
    ACCEPTED = "accepted"
    NEEDS_REVIEW = "needs_review"
    REJECTED = "rejected"


class TaxonomyType(str, Enum):
    GENRE = "genre"
    THEME = "theme"
    TONE = "tone"
    DEMOGRAPHIC = "demographic"
    PLATFORM = "platform"
    LABEL = "label"
    ATTRIBUTE = "attribute"


class ProposalKind(str, Enum):
    OBSERVATION = "observation"
    METADATA = "metadata"
    MEDIA_ITEM = "media_item"


class RecommendationSource(str, Enum):
    ASSISTANT = "assistant"
    USER = "user"
    EXTERNAL = "external"


class RecommendationOutcomeKind(str, Enum):
    INITIAL_RESPONSE = "initial_response"
    TRIED = "tried"
    OPINION = "opinion"
    SUCCESS_ASSESSMENT = "success_assessment"


class CreatorRole(str, Enum):
    CREATOR = "creator"
    DIRECTOR = "director"
    WRITER = "writer"
    ARTIST = "artist"
    DEVELOPER = "developer"
    COMPOSER = "composer"
    PERFORMER = "performer"
    PRODUCER = "producer"
    VOICE_ACTOR = "voice_actor"
    OTHER = "other"


class RelationshipType(str, Enum):
    SEQUEL = "sequel"
    PREQUEL = "prequel"
    ADAPTATION = "adaptation"
    REMAKE = "remake"
    REBOOT = "reboot"
    SPIN_OFF = "spin_off"
    SAME_FRANCHISE = "same_franchise"
    SAME_CREATOR = "same_creator"
    SAME_UNIVERSE = "same_universe"
    DIFFERENT_SEASON = "different_season"
    DIFFERENT_EDITION = "different_edition"
    CHANNEL_VIDEO = "channel_video"
    GAME_EXPANSION = "game_expansion"
    MAIN_SIDE_STORY = "main_side_story"


class Rating(ContractModel):
    score: float = Field(ge=1, le=10)
    rated_on: date
    provisional: bool = False


class ProgressRecord(ContractModel):
    status: ConsumptionStatus
    amount_completed: float | None = Field(default=None, ge=0)
    unit: ProgressUnit | None = None
    recorded_on: date
    started_on: date | None = None
    ended_on: date | None = None
    return_intent: bool | None = None
    reason: NonBlankText | None = None

    @model_validator(mode="after")
    def validate_lifecycle_dates(self) -> "ProgressRecord":
        if self.started_on is not None and self.ended_on is not None:
            if self.ended_on < self.started_on:
                raise ValueError("ended_on cannot be before started_on")
        return self


class Alias(ContractModel):
    value: NonBlankText


class Creator(ContractModel):
    id: NonBlankText
    name: NonBlankText
    aliases: list[Alias] = Field(default_factory=list)


class CreatorCredit(ContractModel):
    creator_id: NonBlankText
    role: CreatorRole


class TaxonomyTerm(ContractModel):
    kind: TaxonomyType
    value: NonBlankText


class Relationship(ContractModel):
    relationship_type: RelationshipType
    target_media_item_id: NonBlankText


class RecommendationEvidenceRef(ContractModel):
    media_item_id: NonBlankText
    observation_id: NonBlankText


class RecommendationOutcomeEvent(ContractModel):
    id: NonBlankText
    kind: RecommendationOutcomeKind
    recorded_on: date
    text: NonBlankText | None = None
    successful: bool | None = None

    @model_validator(mode="after")
    def require_fields_for_outcome_kind(self) -> "RecommendationOutcomeEvent":
        if self.kind in {
            RecommendationOutcomeKind.INITIAL_RESPONSE,
            RecommendationOutcomeKind.OPINION,
        }:
            if self.text is None:
                raise ValueError("text is required for response and opinion outcomes")
            if self.successful is not None:
                raise ValueError("successful is only valid for success assessments")
        elif self.kind is RecommendationOutcomeKind.TRIED:
            if self.text is not None or self.successful is not None:
                raise ValueError("tried outcomes cannot contain text or successful")
        elif self.kind is RecommendationOutcomeKind.SUCCESS_ASSESSMENT:
            if self.successful is None:
                raise ValueError("successful is required for success assessments")
        return self


class RecommendationRecord(ContractModel):
    id: NonBlankText
    media_item_id: NonBlankText
    recommended_on: date
    source: RecommendationSource
    source_context: NonBlankText | None = None
    rationale: NonBlankText
    evidence: list[RecommendationEvidenceRef] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0, le=1)
    outcomes: list[RecommendationOutcomeEvent] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_traceable_assistant_source(self) -> "RecommendationRecord":
        evidence_keys = {(evidence.media_item_id, evidence.observation_id) for evidence in self.evidence}
        if len(evidence_keys) != len(self.evidence):
            raise ValueError("duplicate recommendation evidence reference")
        if (
            self.source in {RecommendationSource.ASSISTANT, RecommendationSource.EXTERNAL}
            and self.source_context is None
        ):
            raise ValueError(
                "source_context is required for assistant and external recommendations"
            )
        if self.source is RecommendationSource.ASSISTANT and self.confidence is None:
            raise ValueError("confidence is required for assistant recommendations")
        outcome_ids = {outcome.id for outcome in self.outcomes}
        if len(outcome_ids) != len(self.outcomes):
            raise ValueError("duplicate recommendation outcome ids")
        for singleton_kind in {
            RecommendationOutcomeKind.INITIAL_RESPONSE,
            RecommendationOutcomeKind.TRIED,
        }:
            if sum(outcome.kind is singleton_kind for outcome in self.outcomes) > 1:
                raise ValueError(
                    f"only one {singleton_kind.value} outcome is allowed per recommendation"
                )
        tried_event = next(
            (
                outcome
                for outcome in self.outcomes
                if outcome.kind is RecommendationOutcomeKind.TRIED
            ),
            None,
        )
        for outcome in self.outcomes:
            if outcome.kind in {
                RecommendationOutcomeKind.OPINION,
                RecommendationOutcomeKind.SUCCESS_ASSESSMENT,
            } and (
                tried_event is None or tried_event.recorded_on > outcome.recorded_on
            ):
                raise ValueError(
                    "opinion or success outcome requires a prior tried event"
                )
        if any(
            previous.recorded_on > current.recorded_on
            for previous, current in zip(self.outcomes, self.outcomes[1:])
        ):
            raise ValueError("recommendation outcomes must be in chronological order")
        if any(outcome.recorded_on < self.recommended_on for outcome in self.outcomes):
            raise ValueError("recommendation outcome cannot predate recommendation")
        return self


class Observation(ContractModel):
    id: NonBlankText
    scope: ObservationScope
    subject_id: NonBlankText | None = None
    subject_label: NonBlankText | None = None
    polarity: ObservationPolarity
    dimension: NonBlankText
    text: NonBlankText
    provenance: Provenance
    privacy: PrivacyLevel = PrivacyLevel.ASSISTANT_READABLE
    source_context: NonBlankText | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    review_state: ReviewState = ReviewState.ACCEPTED
    observed_on: date

    @model_validator(mode="before")
    @classmethod
    def validate_provenance_and_scope(cls, values: object) -> object:
        if not isinstance(values, dict):
            return values

        scope = values.get("scope")
        scope_value = scope.value if isinstance(scope, ObservationScope) else scope
        if scope_value != ObservationScope.WORK.value and (
            not values.get("subject_id") or not values.get("subject_label")
        ):
            raise ValueError(
                "subject_id and subject_label are required for non-work observations"
            )

        provenance = values.get("provenance")
        provenance_value = (
            provenance.value if isinstance(provenance, Provenance) else provenance
        )
        if provenance_value == Provenance.ASSISTANT_INFERRED.value:
            if not values.get("source_context"):
                raise ValueError("source_context is required for assistant_inferred observations")
            if values.get("confidence") is None:
                raise ValueError("confidence is required for assistant_inferred observations")
            review_state = values.get("review_state")
            review_value = (
                review_state.value if isinstance(review_state, ReviewState) else review_state
            )
            if review_value is None:
                raise ValueError(
                    "review_state is required for assistant_inferred observations"
                )

        return values


class Proposal(ContractModel):
    id: NonBlankText
    target_media_item_id: NonBlankText | None = None
    kind: ProposalKind
    proposed_observation: Observation | None = None
    proposed_media_item: "MediaItem | None" = None
    metadata_field: NonBlankText | None = None
    metadata_value: JsonValue | None = None
    source_context: NonBlankText
    confidence: float = Field(ge=0, le=1)
    review_state: ReviewState = ReviewState.NEEDS_REVIEW
    proposed_on: date
    promoted_observation_id: NonBlankText | None = None
    promoted_media_item_id: NonBlankText | None = None

    @model_validator(mode="after")
    def require_exact_proposal_payload(self) -> "Proposal":
        if self.kind is ProposalKind.OBSERVATION:
            if self.target_media_item_id is None:
                raise ValueError("observation proposals require target_media_item_id")
            if self.proposed_observation is None:
                raise ValueError("observation proposals require proposed_observation")
            if self.proposed_observation.provenance is not Provenance.ASSISTANT_INFERRED:
                raise ValueError("proposed_observation must be assistant_inferred")
            if (
                self.review_state is ReviewState.NEEDS_REVIEW
                and self.proposed_observation.review_state is not ReviewState.NEEDS_REVIEW
            ):
                raise ValueError(
                    "pending observation proposals require a needs_review proposed_observation"
                )
            if self.proposed_media_item is not None or self.metadata_field is not None or self.metadata_value is not None:
                raise ValueError("observation proposals cannot include media or metadata changes")
        if self.kind is ProposalKind.METADATA:
            if self.target_media_item_id is None:
                raise ValueError("metadata proposals require target_media_item_id")
            if self.metadata_field is None or self.metadata_value is None:
                raise ValueError("metadata proposals require metadata_field and metadata_value")
            if self.proposed_observation is not None or self.proposed_media_item is not None:
                raise ValueError("metadata proposals cannot include observations or media candidates")
        if self.kind is ProposalKind.MEDIA_ITEM:
            if self.target_media_item_id is not None:
                raise ValueError("media proposals cannot target canonical media")
            if self.proposed_media_item is None:
                raise ValueError("media proposals require proposed_media_item")
            if self.proposed_observation is not None or self.metadata_field is not None or self.metadata_value is not None:
                raise ValueError("media proposals cannot include observations or metadata changes")
        if self.promoted_observation_id is not None and (
            self.kind is not ProposalKind.OBSERVATION
            or self.review_state is not ReviewState.ACCEPTED
        ):
            raise ValueError(
                "promoted observations require an accepted observation proposal"
            )
        if self.promoted_media_item_id is not None:
            if self.kind is not ProposalKind.MEDIA_ITEM or self.review_state is not ReviewState.ACCEPTED:
                raise ValueError("promoted media records require an accepted media proposal")
            if self.proposed_media_item is None or self.promoted_media_item_id != self.proposed_media_item.id:
                raise ValueError("promoted media id must match the proposed media item id")
        return self


class MediaItem(ContractModel):
    id: NonBlankText
    title: NonBlankText
    category: MediaCategory
    status: ConsumptionStatus | None = None
    aliases: list[Alias] = Field(default_factory=list)
    terms: list[TaxonomyTerm] = Field(default_factory=list)
    relationships: list[Relationship] = Field(default_factory=list)
    credits: list[CreatorCredit] = Field(default_factory=list)
    rating: Rating | None = None
    rating_history: list[Rating] = Field(default_factory=list)
    archived_on: date | None = None
    progress_records: list[ProgressRecord] = Field(default_factory=list)
    observations: list[Observation] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def initialize_rating_history(cls, values: object) -> object:
        if not isinstance(values, dict):
            return values
        if values.get("rating") is not None:
            if "rating_history" in values and not values["rating_history"]:
                raise ValueError("rating_history cannot be empty when rating is present")
            if "rating_history" not in values:
                return {**values, "rating_history": [values["rating"]]}
        return values

    @model_validator(mode="after")
    def reject_self_relationships_and_inconsistent_current_rating(self) -> "MediaItem":
        supports_consumption = "consumption" in CATEGORY_DEFINITIONS[self.category].capabilities
        if supports_consumption and self.status is None:
            raise ValueError("consumption-capable categories require status")
        if not supports_consumption and self.status is not None:
            raise ValueError("non-consumption categories cannot have status")
        if self.progress_records and not supports_consumption:
            raise ValueError("progress records require a consumption-capable category")
        capabilities = CATEGORY_DEFINITIONS[self.category].capabilities
        if self.credits and "creator_credits" not in capabilities:
            raise ValueError("creator credits require the creator_credits capability")
        if self.relationships and "relationships" not in capabilities:
            raise ValueError("relationships require the relationships capability")
        if any(relationship.target_media_item_id == self.id for relationship in self.relationships):
            raise ValueError("a media item cannot have a relationship to itself")
        credit_keys = {(credit.creator_id, credit.role) for credit in self.credits}
        if len(credit_keys) != len(self.credits):
            raise ValueError("duplicate creator credit")
        observation_ids = {observation.id for observation in self.observations}
        if len(observation_ids) != len(self.observations):
            raise ValueError("duplicate observation ids")
        if self.rating_history:
            if any(
                previous.rated_on > current.rated_on
                for previous, current in zip(self.rating_history, self.rating_history[1:])
            ):
                raise ValueError("rating_history must be in chronological order")
            if self.rating is None:
                self.rating = self.rating_history[-1]
            elif self.rating != self.rating_history[-1]:
                raise ValueError("rating must match the latest rating_history entry")
        return self


class ConflictState(str, Enum):
    NONE = "none"
    REQUIRES_REVIEW = "requires_review"


class RatingEvent(Rating):
    """One append-only rating occurrence with a stable event identity."""

    event_id: NonBlankText


class ProgressEvent(ProgressRecord):
    """One append-only lifecycle/progress occurrence with a stable event identity."""

    event_id: NonBlankText


class ObservationEvent(ContractModel):
    """One typed observation occurrence without proposal review state leakage."""

    event_id: NonBlankText
    scope: ObservationScope
    subject_id: NonBlankText | None = None
    subject_label: NonBlankText | None = None
    polarity: ObservationPolarity
    dimension: NonBlankText
    text: NonBlankText
    privacy: PrivacyLevel = PrivacyLevel.ASSISTANT_READABLE
    observed_on: date

    @model_validator(mode="after")
    def require_subject_for_scoped_observations(self) -> "ObservationEvent":
        if self.scope is not ObservationScope.WORK and (
            self.subject_id is None or self.subject_label is None
        ):
            raise ValueError(
                "subject_id and subject_label are required for non-work observations"
            )
        return self


class CaptureProposalBase(ContractModel):
    """Fields shared by every reviewable semantic capture candidate."""

    id: NonBlankText
    source_context: NonBlankText
    provenance: Provenance
    confidence: float = Field(ge=0, le=1)
    review_state: ReviewState = ReviewState.NEEDS_REVIEW
    conflict_state: ConflictState = ConflictState.NONE
    contradiction_notes: NonBlankText | None = None
    idempotency_key: NonBlankText
    proposed_on: date
    promoted_event_id: NonBlankText | None = None

    @model_validator(mode="after")
    def require_conflict_note(self) -> "CaptureProposalBase":
        if (
            self.conflict_state is ConflictState.REQUIRES_REVIEW
            and self.contradiction_notes is None
        ):
            raise ValueError("contradiction_notes is required for requires_review conflicts")
        return self

    @model_validator(mode="after")
    def require_promotion_state(self) -> "CaptureProposalBase":
        if self.review_state is ReviewState.NEEDS_REVIEW and self.promoted_event_id is not None:
            raise ValueError("pending typed proposals cannot have a promoted event")
        if self.review_state is ReviewState.REJECTED and self.promoted_event_id is not None:
            raise ValueError("rejected typed proposals cannot have a promoted event")
        if self.promoted_event_id is not None:
            if self.kind == "rating_event":
                event_id = self.rating_event.event_id
            elif self.kind == "progress_event":
                event_id = self.progress_event.event_id
            elif self.kind == "observation":
                event_id = self.observation_event.event_id
            else:
                raise ValueError(
                    "media-item capture proposals cannot carry a promoted event ID"
                )
            if self.review_state is not ReviewState.ACCEPTED:
                raise ValueError("promoted typed proposals must be accepted")
            if self.promoted_event_id != event_id:
                raise ValueError("promoted event ID must match the typed event ID")
        return self


class ObservationProposal(CaptureProposalBase):
    kind: Literal["observation"]
    target_media_item_id: NonBlankText
    observation_event: ObservationEvent


class MediaItemProposal(CaptureProposalBase):
    kind: Literal["media_item"]
    target_media_item_id: None = None
    proposed_media_item: MediaItem

    @model_validator(mode="after")
    def reject_pre_reviewed_inferred_observations(self) -> "MediaItemProposal":
        if any(
            observation.provenance is Provenance.ASSISTANT_INFERRED
            and observation.review_state is not ReviewState.NEEDS_REVIEW
            for observation in self.proposed_media_item.observations
        ):
            raise ValueError(
                "media candidates cannot contain pre-reviewed inferred observations"
            )
        return self


class RatingEventProposal(CaptureProposalBase):
    kind: Literal["rating_event"]
    target_media_item_id: NonBlankText
    rating_event: RatingEvent


class ProgressEventProposal(CaptureProposalBase):
    kind: Literal["progress_event"]
    target_media_item_id: NonBlankText
    progress_event: ProgressEvent


CaptureProposal: TypeAlias = Annotated[
    ObservationProposal | MediaItemProposal | RatingEventProposal | ProgressEventProposal,
    Field(discriminator="kind"),
]
TypedEventProposal: TypeAlias = Annotated[
    RatingEventProposal | ProgressEventProposal,
    Field(discriminator="kind"),
]

_CAPTURE_PROPOSAL_ADAPTER = TypeAdapter(CaptureProposal)
_TYPED_EVENT_PROPOSAL_ADAPTER = TypeAdapter(TypedEventProposal)


def parse_capture_proposal(payload: object) -> CaptureProposal:
    """Validate one flat, discriminated capture candidate without mutation."""

    return _CAPTURE_PROPOSAL_ADAPTER.validate_python(payload)


def parse_typed_event_proposal(payload: object) -> TypedEventProposal:
    """Validate one persisted rating or progress proposal without mutation."""

    return _TYPED_EVENT_PROPOSAL_ADAPTER.validate_python(payload)


class DuplicateCandidate(ContractModel):
    """A non-mutating, reviewable possible duplicate based on title identity evidence."""

    media_item_id: NonBlankText
    candidate_media_item_id: NonBlankText
    matched_titles: list[NonBlankText]
    certainty: Literal["possible"]
    rationale: NonBlankText


class DimensionProfileEntry(ContractModel):
    """Cited, per-work evidence for one normalized observation dimension."""

    media_item_id: NonBlankText
    title: NonBlankText
    category: MediaCategory
    current_rating: Rating | None = None
    supporting_evidence: list[Observation] = Field(default_factory=list)
    contradictory_evidence: list[Observation] = Field(default_factory=list)
    context_evidence: list[Observation] = Field(default_factory=list)


class DimensionProfile(ContractModel):
    dimension: NonBlankText
    entries: list[DimensionProfileEntry] = Field(default_factory=list)


class RatingHistoryProfileEntry(ContractModel):
    """Traceable rating-history evidence for one media item, not a generated taste score."""

    media_item_id: NonBlankText
    title: NonBlankText
    category: MediaCategory
    current_rating: Rating
    rating_history: list[Rating]
    supporting_evidence: list[Observation] = Field(default_factory=list)
    contradictory_evidence: list[Observation] = Field(default_factory=list)
    context_evidence: list[Observation] = Field(default_factory=list)


class RatingHistoryProfile(ContractModel):
    """A deterministic, evidence-carrying read projection for later profile work."""

    entries: list[RatingHistoryProfileEntry] = Field(default_factory=list)


class ProgressContextEntry(ContractModel):
    """Recorded consumption context for one work, without inferred meaning."""

    media_item_id: NonBlankText
    title: NonBlankText
    category: MediaCategory
    current_status: ConsumptionStatus
    progress_history: list[ProgressRecord] = Field(min_length=1)


class ProgressContext(ContractModel):
    entries: list[ProgressContextEntry] = Field(default_factory=list)


class ResolvedCreatorCredit(ContractModel):
    creator_id: NonBlankText
    creator_name: NonBlankText
    role: CreatorRole


class CreatorContextEntry(ContractModel):
    """Typed creator attribution for one work, without inferred affinity."""

    media_item_id: NonBlankText
    title: NonBlankText
    category: MediaCategory
    credits: list[ResolvedCreatorCredit] = Field(min_length=1)


class CreatorContext(ContractModel):
    entries: list[CreatorContextEntry] = Field(default_factory=list)


class ResolvedRelationship(ContractModel):
    relationship_type: RelationshipType
    target_media_item_id: NonBlankText
    target_title: NonBlankText
    target_category: MediaCategory


class RelationshipContextEntry(ContractModel):
    """Stored directed relationships between visible records, without inferred meaning."""

    media_item_id: NonBlankText
    title: NonBlankText
    category: MediaCategory
    relationships: list[ResolvedRelationship] = Field(min_length=1)


class RelationshipContext(ContractModel):
    entries: list[RelationshipContextEntry] = Field(default_factory=list)


class TasteProfileReport(ContractModel):
    """A deterministic composition of cited reads, not a generated personality claim."""

    rating_history: RatingHistoryProfile
    progress_context: ProgressContext
    creator_context: CreatorContext
    relationship_context: RelationshipContext
    dimensions: list[DimensionProfile] = Field(default_factory=list)


class ExportDocument(ContractModel):
    schema_version: Literal[
        "1.0", "1.1", "1.2", "1.3", "1.4", "1.5", "1.6", "1.7", "1.8"
    ]
    exported_on: date
    creators: list[Creator] = Field(default_factory=list)
    media_items: list[MediaItem]
    proposals: list[Proposal] = Field(default_factory=list)
    recommendations: list[RecommendationRecord] = Field(default_factory=list)
    capture_proposals: list[TypedEventProposal] | None = None

    @model_validator(mode="before")
    @classmethod
    def enforce_recommendation_collection_version(cls, values: object) -> object:
        if not isinstance(values, dict):
            return values
        version = values.get("schema_version")
        if version in {"1.6", "1.7", "1.8"} and "recommendations" not in values:
            raise ValueError(f"recommendations is required for export schema version {version}")
        if version in {"1.0", "1.1", "1.2", "1.3", "1.4", "1.5"} and "recommendations" in values:
            raise ValueError("recommendations require export schema version 1.6")
        return values

    @model_validator(mode="before")
    @classmethod
    def require_proposal_collection_in_v1_4_payloads(cls, values: object) -> object:
        if (
            isinstance(values, dict)
            and values.get("schema_version") in {"1.4", "1.5", "1.6", "1.7", "1.8"}
            and "proposals" not in values
        ):
            raise ValueError(f"proposals is required for export schema version {values['schema_version']}")
        return values

    @model_validator(mode="before")
    @classmethod
    def reject_proposals_from_pre_v1_4_payloads(cls, values: object) -> object:
        if (
            isinstance(values, dict)
            and values.get("schema_version") in {"1.0", "1.1", "1.2", "1.3"}
            and "proposals" in values
        ):
            raise ValueError("proposals require export schema version 1.4")
        return values

    @model_validator(mode="before")
    @classmethod
    def require_capture_proposal_collection_in_v1_8_payloads(cls, values: object) -> object:
        if not isinstance(values, dict):
            return values
        version = values.get("schema_version")
        if version == "1.8" and "capture_proposals" not in values:
            raise ValueError("capture_proposals is required for export schema version 1.8")
        if version != "1.8" and "capture_proposals" in values:
            raise ValueError("capture_proposals require export schema version 1.8")
        return values

    @model_validator(mode="before")
    @classmethod
    def reject_media_proposals_before_v1_7(cls, values: object) -> object:
        if not isinstance(values, dict) or values.get("schema_version") in {"1.7", "1.8"}:
            return values
        proposals = values.get("proposals")
        if isinstance(proposals, list) and any(
            isinstance(proposal, dict) and proposal.get("kind") == ProposalKind.MEDIA_ITEM.value
            for proposal in proposals
        ):
            raise ValueError("media proposals require export schema version 1.7")
        return values

    @model_validator(mode="before")
    @classmethod
    def require_creator_collection_in_v1_3_payloads(cls, values: object) -> object:
        if (
            isinstance(values, dict)
            and values.get("schema_version") in {"1.3", "1.4", "1.5", "1.6", "1.7", "1.8"}
            and "creators" not in values
        ):
            raise ValueError(
                f"creators is required for export schema version {values['schema_version']}"
            )
        return values

    @model_validator(mode="before")
    @classmethod
    def reject_creator_fields_from_pre_v1_3_payloads(cls, values: object) -> object:
        if not isinstance(values, dict) or values.get("schema_version") not in {"1.0", "1.1", "1.2"}:
            return values
        media_items = values.get("media_items")
        if "creators" in values or (
            isinstance(media_items, list)
            and any(isinstance(item, dict) and "credits" in item for item in media_items)
        ):
            raise ValueError("creator identities and credits require export schema version 1.3")
        return values

    @model_validator(mode="after")
    def reject_duplicate_stable_ids(self) -> "ExportDocument":
        media_ids = [item.id for item in self.media_items]
        if len(set(media_ids)) != len(media_ids):
            raise ValueError("duplicate media item ids")

        proposal_ids = [proposal.id for proposal in self.proposals]
        if len(set(proposal_ids)) != len(proposal_ids):
            raise ValueError("duplicate proposal ids")
        capture_proposals = self.capture_proposals or []
        capture_proposal_ids = [proposal.id for proposal in capture_proposals]
        if len(set(capture_proposal_ids)) != len(capture_proposal_ids):
            raise ValueError("duplicate capture proposal ids")
        if len(set(proposal_ids + capture_proposal_ids)) != len(proposal_ids + capture_proposal_ids):
            raise ValueError("duplicate proposal ids across proposal queues")
        idempotency_keys = [proposal.idempotency_key for proposal in capture_proposals]
        if len(set(idempotency_keys)) != len(idempotency_keys):
            raise ValueError("duplicate capture proposal idempotency keys")
        event_ids = [
            proposal.rating_event.event_id
            if proposal.kind == "rating_event"
            else proposal.progress_event.event_id
            for proposal in capture_proposals
        ]
        if len(set(event_ids)) != len(event_ids):
            raise ValueError("duplicate capture proposal event ids")
        return self

    @model_validator(mode="before")
    @classmethod
    def reject_v1_1_rating_history_from_v1_0_payloads(cls, values: object) -> object:
        if not isinstance(values, dict) or values.get("schema_version") != "1.0":
            return values
        media_items = values.get("media_items")
        if isinstance(media_items, list) and any(
            isinstance(item, dict) and "rating_history" in item for item in media_items
        ):
            raise ValueError("rating_history requires export schema version 1.1")
        return values

    @model_validator(mode="before")
    @classmethod
    def reject_archive_state_from_pre_v1_2_payloads(cls, values: object) -> object:
        if not isinstance(values, dict) or values.get("schema_version") not in {"1.0", "1.1"}:
            return values
        media_items = values.get("media_items")
        if isinstance(media_items, list) and any(
            isinstance(item, dict) and "archived_on" in item for item in media_items
        ):
            raise ValueError("archived_on requires export schema version 1.2")
        return values

    @model_validator(mode="before")
    @classmethod
    def reject_new_categories_from_pre_v1_5_payloads(cls, values: object) -> object:
        if not isinstance(values, dict) or values.get("schema_version") in {"1.5", "1.6", "1.7", "1.8"}:
            return values
        media_items = values.get("media_items")
        legacy_categories = {
            "game", "anime_series", "anime_movie", "manga_manhwa",
            "movie", "television_show", "youtube",
        }
        if isinstance(media_items, list) and any(
            isinstance(item, dict) and item.get("category") not in legacy_categories
            for item in media_items
        ):
            raise ValueError("new categories require export schema version 1.5")
        return values

    @model_validator(mode="after")
    def require_v1_3_credits_to_reference_exported_creators(self) -> "ExportDocument":
        creator_ids = {creator.id for creator in self.creators}
        if len(creator_ids) != len(self.creators):
            raise ValueError("duplicate creator ids")
        for item in self.media_items:
            for credit in item.credits:
                if credit.creator_id not in creator_ids:
                    raise ValueError(f"credit references unknown creator {credit.creator_id!r}")
        media_by_id = {item.id: item for item in self.media_items}
        recommendation_ids = {recommendation.id for recommendation in self.recommendations}
        if len(recommendation_ids) != len(self.recommendations):
            raise ValueError("duplicate recommendation ids")
        for recommendation in self.recommendations:
            if recommendation.media_item_id not in media_by_id:
                raise ValueError(
                    f"recommendation references unknown media item {recommendation.media_item_id!r}"
                )
            for evidence in recommendation.evidence:
                evidence_item = media_by_id.get(evidence.media_item_id)
                if evidence_item is None:
                    raise ValueError(
                        f"recommendation evidence references unknown media item {evidence.media_item_id!r}"
                    )
                if evidence.observation_id is not None:
                    observation = next(
                        (
                            candidate
                            for candidate in evidence_item.observations
                            if candidate.id == evidence.observation_id
                        ),
                        None,
                    )
                    if observation is None:
                        raise ValueError(
                            "recommendation evidence references unknown observation "
                            f"{evidence.observation_id!r} on media item {evidence.media_item_id!r}"
                        )
                    if (
                        observation.privacy is not PrivacyLevel.ASSISTANT_READABLE
                        or observation.review_state is not ReviewState.ACCEPTED
                    ):
                        raise ValueError(
                            "recommendation evidence must be accepted and assistant-readable"
                        )
        for capture_proposal in self.capture_proposals or []:
            if capture_proposal.target_media_item_id not in media_by_id:
                raise ValueError(
                    "capture proposal references unknown media item "
                    f"{capture_proposal.target_media_item_id!r}"
                )
        return self


def parse_export_document(payload: object) -> ExportDocument:
    """Validate an import payload without silently dropping unknown data."""
    return ExportDocument.model_validate(payload)


def export_document(
    media_items: list[MediaItem],
    exported_on: date,
    creators: list[Creator] | None = None,
    proposals: list[Proposal] | None = None,
    recommendations: list[RecommendationRecord] | None = None,
    capture_proposals: list[TypedEventProposal] | None = None,
) -> dict:
    """Return the versioned, portable public record for a set of entries."""
    serialized_items = []
    has_media_proposals = bool(
        proposals and any(proposal.kind is ProposalKind.MEDIA_ITEM for proposal in proposals)
    )
    has_capture_proposals = bool(capture_proposals)
    for item in media_items:
        serialized_item = item.model_dump(mode="json", exclude_none=True, exclude_defaults=True)
        for observation, serialized_observation in zip(
            item.observations, serialized_item.get("observations", []), strict=True
        ):
            if observation.provenance is Provenance.ASSISTANT_INFERRED:
                serialized_observation["review_state"] = observation.review_state.value
        serialized_items.append(serialized_item)

    document = {
        "schema_version": (
            "1.8"
            if has_capture_proposals
            else (
                "1.7"
                if has_media_proposals
                else (
                    "1.6"
                    if recommendations is not None
                    else ("1.5" if proposals is not None else "1.3")
                )
            )
        ),
        "exported_on": exported_on.isoformat(),
        "creators": [
            creator.model_dump(mode="json", exclude_none=True, exclude_defaults=True)
            for creator in (creators or [])
        ],
        "media_items": serialized_items,
    }
    if proposals is not None or has_capture_proposals:
        serialized_proposals = []
        for proposal in proposals or []:
            serialized_proposal = proposal.model_dump(
                mode="json", exclude_none=True, exclude_defaults=True
            )
            proposed_observation = proposal.proposed_observation
            if (
                proposed_observation is not None
                and proposed_observation.provenance is Provenance.ASSISTANT_INFERRED
            ):
                serialized_proposal["proposed_observation"]["review_state"] = (
                    proposed_observation.review_state.value
                )
            serialized_proposals.append(serialized_proposal)
        document["proposals"] = serialized_proposals
    if recommendations is not None or has_media_proposals or has_capture_proposals:
        document["recommendations"] = [
            recommendation.model_dump(mode="json", exclude_none=True, exclude_defaults=True)
            for recommendation in (recommendations or [])
        ]
    if has_capture_proposals:
        serialized_capture_proposals = []
        for proposal in capture_proposals or []:
            serialized_proposal = proposal.model_dump(
                mode="json", exclude_none=True, exclude_defaults=True
            )
            serialized_proposal["review_state"] = proposal.review_state.value
            serialized_capture_proposals.append(serialized_proposal)
        document["capture_proposals"] = serialized_capture_proposals
    parse_export_document(document)
    return document
