"""Application workflow for validated, portable media-library imports and exports."""

from datetime import date
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile

from app.domain import (
    ConsumptionStatus,
    Creator,
    CreatorContext,
    CreatorContextEntry,
    CreatorRole,
    DimensionProfile,
    DimensionProfileEntry,
    DuplicateCandidate,
    MediaCategory,
    MediaItem,
    ObservationPolarity,
    PrivacyLevel,
    ProgressContext,
    ProgressContextEntry,
    RelationshipContext,
    RelationshipContextEntry,
    ResolvedCreatorCredit,
    ResolvedRelationship,
    Proposal,
    RatingHistoryProfile,
    RatingHistoryProfileEntry,
    RecommendationOutcomeEvent,
    RecommendationRecord,
    ReviewState,
    TasteProfileReport,
    TypedEventProposal,
    export_document,
    parse_typed_event_proposal,
    parse_export_document,
)
from app.persistence import (
    MediaRepository,
    RecommendationIdentityConflictError,
    RecommendationOutcomeIdentityConflictError,
)


BACKUP_VERSION = "1.0"
BACKUP_FILENAME = "latest.json"


class ImportReviewStaleError(ValueError):
    """The library changed after a deterministic import review was issued."""


class MediaItemAlreadyExistsError(ValueError):
    """A create-only write collided with an existing durable stable ID."""


class CreatorAlreadyExistsError(ValueError):
    """A create-only creator write collided with an existing durable stable ID."""


class RecommendationReferenceConflictError(ValueError):
    """A media mutation would orphan a durable recommendation reference."""


class LibraryService:
    """Coordinate domain validation with durable library persistence."""

    def __init__(self, repository: MediaRepository, backup_directory: Path | None = None) -> None:
        self._repository = repository
        self._backup_directory = backup_directory or Path("backups")

    @staticmethod
    def _review_snapshot(value) -> dict[str, object]:
        return value.model_dump(mode="json", exclude_none=True)

    @staticmethod
    def _capture_review_snapshot(value) -> dict[str, object]:
        snapshot = value.model_dump(
            mode="json", exclude_none=True, exclude_defaults=True
        )
        snapshot["review_state"] = value.review_state.value
        return snapshot

    @staticmethod
    def _review_token(document, current) -> str:
        payload = {
            "document": document.model_dump(mode="json", exclude_none=True),
            "current": [
                [value.model_dump(mode="json", exclude_none=True) for value in collection]
                for collection in current
            ],
        }
        serialized = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    @classmethod
    def _merge_review_entries(cls, incoming, current, label) -> list[dict[str, object]]:
        current_by_id = {value.id: value for value in current}
        entries = []
        for value in sorted(incoming, key=lambda candidate: candidate.id):
            existing = current_by_id.get(value.id)
            entries.append(
                {
                    "id": value.id,
                    "label": label(value),
                    "action": "create" if existing is None else "unchanged" if existing == value else "update",
                    "before": cls._review_snapshot(existing) if existing is not None else None,
                    "after": cls._review_snapshot(value),
                }
            )
        return entries

    @classmethod
    def _replacement_review_entries(
        cls, incoming, current, label, *, snapshot=None
    ) -> list[dict[str, object]]:
        snapshot = snapshot or cls._review_snapshot
        incoming_by_id = {value.id: value for value in incoming}
        current_by_id = {value.id: value for value in current}
        entries = []
        for stable_id in sorted(set(incoming_by_id) | set(current_by_id)):
            before = current_by_id.get(stable_id)
            after = incoming_by_id.get(stable_id)
            action = (
                "create" if before is None else
                "remove" if after is None else
                "unchanged" if before == after else
                "update"
            )
            entries.append(
                {
                    "id": stable_id,
                    "label": label(after or before),
                    "action": action,
                    "before": snapshot(before) if before is not None else None,
                    "after": snapshot(after) if after is not None else None,
                }
            )
        return entries

    @classmethod
    def _recommendation_review_entries(cls, incoming, current) -> list[dict[str, object]]:
        current_by_id = {value.id: value for value in current}
        entries = []
        for recommendation in sorted(incoming, key=lambda value: value.id):
            before = current_by_id.get(recommendation.id)
            action = "create" if before is None else "replay" if before == recommendation else "conflict"
            entries.append(
                {
                    "id": recommendation.id,
                    "label": recommendation.id,
                    "action": action,
                    "before": cls._review_snapshot(before) if before is not None else None,
                    "after": cls._review_snapshot(recommendation),
                }
            )
        return entries

    @staticmethod
    def _validated_import_document(payload: object):
        document = parse_export_document(payload)
        item_ids = [item.id for item in document.media_items]
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("import document contains duplicate media item ids")
        proposal_ids = [proposal.id for proposal in document.proposals]
        if len(proposal_ids) != len(set(proposal_ids)):
            raise ValueError("import document contains duplicate proposal ids")
        capture_proposals = document.capture_proposals or []
        capture_ids = [proposal.id for proposal in capture_proposals]
        if len(capture_ids) != len(set(capture_ids)):
            raise ValueError("import document contains duplicate capture proposal ids")
        idempotency_keys = [proposal.idempotency_key for proposal in capture_proposals]
        if len(idempotency_keys) != len(set(idempotency_keys)):
            raise ValueError(
                "import document contains duplicate capture proposal idempotency keys"
            )
        event_ids = [
            proposal.rating_event.event_id
            if proposal.kind == "rating_event"
            else proposal.progress_event.event_id
            for proposal in capture_proposals
        ]
        if len(event_ids) != len(set(event_ids)):
            raise ValueError("import document contains duplicate capture proposal event ids")
        return document

    def review_import_document(self, payload: object) -> dict[str, object]:
        """Describe deterministic portable-import effects without mutating the library."""
        document = self._validated_import_document(payload)
        current = self._repository.load_document_with_capture_proposals()
        items, creators, proposals, recommendations, capture_proposals = current
        proposal_replace = document.schema_version in {"1.4", "1.5", "1.6", "1.7", "1.8"}
        recommendation_merge = document.schema_version in {"1.6", "1.7", "1.8"}
        capture_proposal_replace = document.schema_version == "1.8"
        proposal_entries = (
            self._replacement_review_entries(
                document.proposals, proposals, lambda proposal: proposal.id
            )
            if proposal_replace else []
        )
        recommendation_entries = (
            self._recommendation_review_entries(
                document.recommendations, recommendations
            )
            if recommendation_merge else []
        )
        capture_proposal_entries = (
            self._replacement_review_entries(
                document.capture_proposals or [],
                capture_proposals,
                lambda proposal: proposal.id,
                snapshot=self._capture_review_snapshot,
            )
            if capture_proposal_replace
            else []
        )
        blocking_reasons = [
            f"recommendation id conflict: {entry['id']!r}"
            for entry in recommendation_entries
            if entry["action"] == "conflict"
        ]
        try:
            self._validate_legacy_import_recommendation_references(
                document.media_items, items, recommendations
            )
        except RecommendationReferenceConflictError as error:
            if str(error) not in blocking_reasons:
                blocking_reasons.append(str(error))
        incoming_item_ids = {item.id for item in document.media_items}
        incoming_creator_ids = {creator.id for creator in document.creators}
        incoming_proposal_ids = {proposal.id for proposal in document.proposals}
        incoming_recommendation_ids = {
            recommendation.id for recommendation in document.recommendations
        }
        review = {
            "review_schema_version": "1.0",
            "schema_version": document.schema_version,
            "review_token": self._review_token(document, current),
            "can_import": not blocking_reasons,
            "blocking_reasons": blocking_reasons,
            "media_items": {
                "mode": "merge",
                "entries": self._merge_review_entries(
                    document.media_items, items, lambda item: item.title
                ),
                "preserved_ids": sorted(
                    item.id for item in items if item.id not in incoming_item_ids
                ),
                "current_ids": sorted(item.id for item in items),
            },
            "creators": {
                "mode": "merge",
                "entries": self._merge_review_entries(
                    document.creators, creators, lambda creator: creator.name
                ),
                "preserved_ids": sorted(
                    creator.id
                    for creator in creators
                    if creator.id not in incoming_creator_ids
                ),
                "current_ids": sorted(creator.id for creator in creators),
            },
            "proposals": {
                "mode": "replace" if proposal_replace else "preserve",
                "entries": proposal_entries,
                "preserved_ids": [] if proposal_replace else sorted(
                    proposal.id
                    for proposal in proposals
                    if proposal.id not in incoming_proposal_ids
                ),
                "current_ids": sorted(proposal.id for proposal in proposals),
            },
            "recommendations": {
                "mode": "merge" if recommendation_merge else "preserve",
                "entries": recommendation_entries,
                "preserved_ids": sorted(
                    recommendation.id
                    for recommendation in recommendations
                    if recommendation.id not in incoming_recommendation_ids
                ),
                "current_ids": sorted(
                    recommendation.id for recommendation in recommendations
                ),
            },
        }
        if capture_proposal_replace:
            incoming_capture_ids = {
                proposal.id for proposal in document.capture_proposals or []
            }
            review["capture_proposals"] = {
                "mode": "replace",
                "entries": capture_proposal_entries,
                "preserved_ids": [],
                "current_ids": sorted(
                    proposal.id for proposal in capture_proposals
                    if proposal.id not in incoming_capture_ids
                ),
            }
        return review

    def import_document(self, payload: object, review_token: str | None = None) -> int:
        document = self._validated_import_document(payload)
        if review_token is not None and not review_token.strip():
            raise ValueError("import review token must not be blank")

        def validate_current_document(current) -> None:
            if (
                review_token is not None
                and self._review_token(document, current) != review_token
            ):
                raise ImportReviewStaleError(
                    "import review is stale; review the document again"
                )

        self._repository.save_document(
            document.media_items,
            document.creators,
            document.proposals,
            document.recommendations,
            replace_proposals=document.schema_version in {"1.4", "1.5", "1.6", "1.7", "1.8"},
            capture_proposals=(document.capture_proposals or [])
            if document.schema_version == "1.8"
            else None,
            replace_capture_proposals=document.schema_version == "1.8",
            validate_preserved_recommendations=self._validate_legacy_import_recommendation_references,
            validate_current_document=validate_current_document if review_token is not None else None,
        )
        return len(document.media_items)

    def export_document(self, exported_on: date) -> dict:
        items, creators, proposals, recommendations, capture_proposals = (
            self._repository.load_document_with_capture_proposals()
        )
        return export_document(
            items,
            exported_on,
            creators=creators,
            proposals=proposals,
            recommendations=recommendations,
            capture_proposals=capture_proposals,
        )

    def submit_capture_proposal(self, payload: object) -> TypedEventProposal:
        """Persist one reviewable typed rating/progress event without canonical mutation."""
        proposal = parse_typed_event_proposal(payload)
        if proposal.review_state is not ReviewState.NEEDS_REVIEW:
            raise ValueError("new typed proposals must begin as needs_review")
        try:
            self._repository.get(proposal.target_media_item_id)
        except KeyError as error:
            raise ValueError(
                f"capture proposal target media item does not exist: {proposal.target_media_item_id!r}"
            ) from error
        try:
            self._repository.insert_capture_proposal(proposal)
        except sqlite3.IntegrityError as error:
            raise ValueError(
                f"capture proposal identity already exists: {proposal.id!r}"
            ) from error
        return proposal

    def list_capture_proposals(self) -> list[TypedEventProposal]:
        return self._repository.list_capture_proposals()

    def get_capture_proposal(self, proposal_id: str) -> TypedEventProposal:
        return self._repository.get_capture_proposal(proposal_id)

    def review_capture_proposal(
        self,
        proposal_id: str,
        review_state: ReviewState,
    ) -> TypedEventProposal:
        return self._repository.review_capture_proposal(proposal_id, review_state)

    def promote_capture_proposal(self, proposal_id: str) -> dict[str, object]:
        proposal, item = self._repository.promote_capture_proposal(proposal_id)
        return {"proposal": proposal, "media_item": item}

    def create_recommendation(
        self, payload: object
    ) -> tuple[RecommendationRecord, bool]:
        """Create one immutable recommendation occurrence or replay it exactly."""
        recommendation = RecommendationRecord.model_validate(payload)
        if recommendation.outcomes:
            raise ValueError("append outcomes separately after creating the recommendation")

        def validate(items, creators, proposals, recommendations) -> None:
            export_document(
                items,
                recommendation.recommended_on,
                creators=creators,
                proposals=proposals,
                recommendations=recommendations,
            )

        return self._repository.insert_recommendation_guarded(recommendation, validate)

    def list_recommendations(self) -> list[RecommendationRecord]:
        """Return immutable recommendation occurrences in chronological stable order."""
        return sorted(
            self._repository.list_recommendations(),
            key=lambda record: (record.recommended_on, record.id),
        )

    def append_recommendation_outcome(
        self, recommendation_id: str, payload: object
    ) -> tuple[RecommendationRecord, bool]:
        """Append one immutable factual outcome event or replay it exactly."""
        outcome = RecommendationOutcomeEvent.model_validate(payload)
        return self._repository.append_recommendation_outcome_guarded(
            recommendation_id, outcome
        )

    def upsert_creator(self, payload: object) -> Creator:
        """Validate and durably save one reusable creator identity."""
        creator = Creator.model_validate(payload)
        self._repository.save_creator(creator)
        return creator

    def create_creator(self, payload: object) -> Creator:
        """Create one reusable identity atomically without replacing an existing stable ID."""
        creator = Creator.model_validate(payload)
        try:
            self._repository.insert_creator(creator)
        except sqlite3.IntegrityError as error:
            raise CreatorAlreadyExistsError(
                f"creator id already exists: {creator.id!r}"
            ) from error
        return creator

    def get_creator(self, creator_id: str) -> Creator:
        return self._repository.get_creator(creator_id)

    def list_creators(self) -> list[Creator]:
        return self._repository.list_creators()

    def list_media_for_creator(
        self,
        creator_id: str,
        role: CreatorRole | None = None,
        include_archived: bool = False,
    ) -> list[MediaItem]:
        """Return visible stable-ID-ordered works credited to one existing creator."""
        self._repository.get_creator(creator_id)
        return sorted(
            [
                item
                for item in self._visible(self._repository.list_all(), include_archived)
                if any(
                    credit.creator_id == creator_id and (role is None or credit.role is role)
                    for credit in item.credits
                )
            ],
            key=lambda item: item.id,
        )

    def rating_history_profile(self, include_archived: bool = False) -> RatingHistoryProfile:
        """Project rated works into traceable, assistant-readable evidence buckets."""
        return self._rating_history_profile_from(
            self._visible(self._repository.list_all(), include_archived)
        )

    @staticmethod
    def _rating_history_profile_from(items: list[MediaItem]) -> RatingHistoryProfile:
        entries = []
        for item in items:
            if item.rating is None:
                continue
            evidence = [
                observation
                for observation in item.observations
                if observation.privacy is PrivacyLevel.ASSISTANT_READABLE
                and observation.review_state is ReviewState.ACCEPTED
            ]
            entries.append(
                RatingHistoryProfileEntry(
                    media_item_id=item.id,
                    title=item.title,
                    category=item.category,
                    current_rating=item.rating,
                    rating_history=item.rating_history,
                    supporting_evidence=[
                        observation
                        for observation in evidence
                        if observation.polarity is ObservationPolarity.POSITIVE
                    ],
                    contradictory_evidence=[
                        observation
                        for observation in evidence
                        if observation.polarity is ObservationPolarity.NEGATIVE
                    ],
                    context_evidence=[
                        observation
                        for observation in evidence
                        if observation.polarity
                        in {ObservationPolarity.MIXED, ObservationPolarity.NEUTRAL}
                    ],
                )
            )
        return RatingHistoryProfile(entries=sorted(entries, key=lambda entry: entry.media_item_id))

    def dimension_profile(
        self, dimension: str, include_archived: bool = False
    ) -> DimensionProfile:
        """Return cited evidence for one case-insensitive observation dimension."""
        normalized_dimension = dimension.strip().casefold()
        if not normalized_dimension:
            raise ValueError("dimension must not be blank")
        return self._dimension_profile_from(
            self._visible(self._repository.list_all(), include_archived), normalized_dimension
        )

    def list_evidence_dimensions(self, include_archived: bool = False) -> list[str]:
        """List normalized dimensions represented by visible accepted readable evidence."""
        return self._evidence_dimensions_from(
            self._visible(self._repository.list_all(), include_archived)
        )

    @staticmethod
    def _evidence_dimensions_from(items: list[MediaItem]) -> list[str]:
        return sorted(
            {
                observation.dimension.strip().casefold()
                for item in items
                for observation in item.observations
                if observation.privacy is PrivacyLevel.ASSISTANT_READABLE
                and observation.review_state is ReviewState.ACCEPTED
            }
        )

    @staticmethod
    def _dimension_profile_from(
        items: list[MediaItem], normalized_dimension: str
    ) -> DimensionProfile:
        entries = []
        for item in items:
            evidence = [
                observation
                for observation in item.observations
                if observation.dimension.casefold() == normalized_dimension
                and observation.privacy is PrivacyLevel.ASSISTANT_READABLE
                and observation.review_state is ReviewState.ACCEPTED
            ]
            if not evidence:
                continue
            entries.append(
                DimensionProfileEntry(
                    media_item_id=item.id,
                    title=item.title,
                    category=item.category,
                    current_rating=item.rating,
                    supporting_evidence=[
                        observation
                        for observation in evidence
                        if observation.polarity is ObservationPolarity.POSITIVE
                    ],
                    contradictory_evidence=[
                        observation
                        for observation in evidence
                        if observation.polarity is ObservationPolarity.NEGATIVE
                    ],
                    context_evidence=[
                        observation
                        for observation in evidence
                        if observation.polarity
                        in {ObservationPolarity.MIXED, ObservationPolarity.NEUTRAL}
                    ],
                )
            )
        return DimensionProfile(
            dimension=normalized_dimension,
            entries=sorted(entries, key=lambda entry: entry.media_item_id),
        )

    def taste_profile_report(self, include_archived: bool = False) -> TasteProfileReport:
        """Compose all visible cited dimensions and rating histories without generating claims."""
        items, creators = self._repository.taste_profile_snapshot()
        visible = self._visible(items, include_archived)
        visible_by_id = {item.id: item for item in visible}
        all_item_ids = {item.id for item in items}
        if any(relationship.target_media_item_id not in all_item_ids for item in visible for relationship in item.relationships):
            raise ValueError("relationship references an unknown target")
        creator_by_id = {creator.id: creator for creator in creators}
        missing_creator_ids = sorted({credit.creator_id for item in visible for credit in item.credits if credit.creator_id not in creator_by_id})
        if missing_creator_ids:
            raise ValueError("creator credit references an unknown identity")
        dimensions = self._evidence_dimensions_from(visible)
        return TasteProfileReport(
            rating_history=self._rating_history_profile_from(visible),
            progress_context=ProgressContext(
                entries=[
                    ProgressContextEntry(
                        media_item_id=item.id,
                        title=item.title,
                        category=item.category,
                        current_status=item.status,
                        progress_history=item.progress_records,
                    )
                    for item in sorted(visible, key=lambda item: item.id)
                    if item.progress_records
                ]
            ),
            creator_context=CreatorContext(
                entries=[
                    CreatorContextEntry(
                        media_item_id=item.id,
                        title=item.title,
                        category=item.category,
                        credits=[
                            ResolvedCreatorCredit(
                                creator_id=credit.creator_id,
                                creator_name=creator_by_id[credit.creator_id].name,
                                role=credit.role,
                            )
                            for credit in item.credits
                        ],
                    )
                    for item in sorted(visible, key=lambda item: item.id)
                    if item.credits
                ]
            ),
            relationship_context=RelationshipContext(
                entries=[
                    RelationshipContextEntry(
                        media_item_id=item.id,
                        title=item.title,
                        category=item.category,
                        relationships=[
                            ResolvedRelationship(
                                relationship_type=relationship.relationship_type,
                                target_media_item_id=relationship.target_media_item_id,
                                target_title=visible_by_id[relationship.target_media_item_id].title,
                                target_category=visible_by_id[relationship.target_media_item_id].category,
                            )
                            for relationship in item.relationships
                            if relationship.target_media_item_id in visible_by_id
                        ],
                    )
                    for item in sorted(visible, key=lambda item: item.id)
                    if any(relationship.target_media_item_id in visible_by_id for relationship in item.relationships)
                ]
            ),
            dimensions=[
                self._dimension_profile_from(visible, dimension)
                for dimension in dimensions
            ],
        )

    def duplicate_candidates(self, include_archived: bool = False) -> list[DuplicateCandidate]:
        """Flag same-category title/alias identity collisions without altering records."""
        items = self._visible(self._repository.list_all(), include_archived)
        candidates = []
        for index, item in enumerate(items):
            identities = self._title_identities(item)
            for other in items[index + 1 :]:
                if item.category is not other.category:
                    continue
                shared = sorted(identities & self._title_identities(other))
                if not shared:
                    continue
                candidates.append(
                    DuplicateCandidate(
                        media_item_id=item.id,
                        candidate_media_item_id=other.id,
                        matched_titles=shared,
                        certainty="possible",
                        rationale="same-category records share normalized title or alias identity",
                    )
                )
        return sorted(candidates, key=lambda candidate: (candidate.media_item_id, candidate.candidate_media_item_id))

    @staticmethod
    def _title_identities(item: MediaItem) -> set[str]:
        values = [item.title, *(alias.value for alias in item.aliases)]
        return {
            "".join(character for character in value.casefold() if character.isalnum())
            for value in values
            if "".join(character for character in value.casefold() if character.isalnum())
        }

    def submit_proposal(self, payload: object) -> Proposal:
        """Validate and persist an assistant proposal without changing media evidence."""
        proposal = Proposal.model_validate(payload)
        if proposal.review_state is not ReviewState.NEEDS_REVIEW:
            raise ValueError("new proposals must begin as needs_review")
        try:
            self._repository.insert_proposal(proposal)
        except sqlite3.IntegrityError as error:
            raise ValueError(f"proposal id already exists: {proposal.id!r}") from error
        return proposal


    def list_proposals(self) -> list[Proposal]:
        return self._repository.list_proposals()

    def list_pending_proposals(
        self,
        *,
        target_media_item_id: str | None = None,
        kind: str | None = None,
        review_state: ReviewState | None = ReviewState.NEEDS_REVIEW,
        include_archived: bool = False,
    ) -> list[Proposal | TypedEventProposal]:
        """Return one stable, archive-aware view across both proposal queues."""
        if target_media_item_id is not None:
            target_media_item_id = target_media_item_id.strip()
            if not target_media_item_id:
                raise ValueError("target media item ID must not be blank")
        if kind is not None:
            kind = kind.strip()
            if kind not in {
                "observation",
                "metadata",
                "media_item",
                "rating_event",
                "progress_event",
            }:
                raise ValueError(f"unsupported proposal kind: {kind!r}")
        if isinstance(review_state, str):
            review_state = ReviewState(review_state)
        items, _creators, proposals, _recommendations, capture_proposals = (
            self._repository.load_document_with_capture_proposals()
        )
        visible_item_ids = {
            item.id
            for item in items
            if include_archived or item.archived_on is None
        }
        candidates: list[Proposal | TypedEventProposal] = [
            *proposals,
            *capture_proposals,
        ]
        filtered = []
        for proposal in candidates:
            proposal_kind = proposal.kind
            if kind is not None and proposal_kind != kind:
                continue
            if review_state is not None and proposal.review_state is not review_state:
                continue
            target = proposal.target_media_item_id
            if target_media_item_id is not None and target != target_media_item_id:
                continue
            if target is not None:
                if target not in visible_item_ids:
                    continue
            elif isinstance(proposal, Proposal) and proposal.proposed_media_item is not None:
                if (
                    not include_archived
                    and proposal.proposed_media_item.archived_on is not None
                ):
                    continue
            filtered.append(proposal)
        return sorted(filtered, key=lambda proposal: proposal.id)

    def get_proposal(self, proposal_id: str) -> Proposal | TypedEventProposal:
        try:
            return self._repository.get_proposal(proposal_id)
        except KeyError as legacy_error:
            try:
                return self._repository.get_capture_proposal(proposal_id)
            except KeyError:
                raise legacy_error

    def review_proposal(self, proposal_id: str, review_state: ReviewState) -> Proposal:
        """Record a user review outcome without applying proposed media changes."""
        return self._repository.review_proposal(proposal_id, review_state)

    def promote_observation_proposal(self, proposal_id: str) -> dict[str, object]:
        proposal, item = self._repository.promote_observation_proposal(proposal_id)
        return {"proposal": proposal, "media_item": item}

    def promote_media_proposal(self, proposal_id: str) -> dict[str, object]:
        proposal, item = self._repository.promote_media_proposal(proposal_id)
        return {"proposal": proposal, "media_item": item}

    def auto_promote_proposal(self, proposal_id: str) -> dict[str, object]:
        """Apply one rubric-approved proposal with review and append in one transaction."""
        proposal = self.get_proposal(proposal_id)
        if isinstance(proposal, Proposal):
            if proposal.kind.value == "observation":
                promoted, item = self._repository.auto_promote_observation_proposal(proposal_id)
            elif proposal.kind.value == "media_item":
                promoted, item = self._repository.auto_promote_media_proposal(proposal_id)
            else:
                raise ValueError("metadata proposals cannot be auto-promoted")
        else:
            promoted, item = self._repository.auto_promote_capture_proposal(proposal_id)
        return {"proposal": promoted, "media_item": item}

    def create_backup(self, exported_on: date) -> dict[str, object]:
        """Write and validate the latest versioned JSON backup."""
        document = self.export_document(exported_on)
        backup = {"backup_version": BACKUP_VERSION, "export": document}
        self._backup_directory.mkdir(parents=True, exist_ok=True)
        destination = self._backup_directory / BACKUP_FILENAME
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self._backup_directory, delete=False) as handle:
            json.dump(backup, handle, indent=2, sort_keys=True)
            handle.write("\n")
            temporary = Path(handle.name)
        temporary.replace(destination)
        self._validate_backup(backup)
        return {"backup_version": BACKUP_VERSION, "items": len(document["media_items"]), "verified": True}

    def restore_backup(self) -> dict[str, object]:
        """Restore the latest backup through the export validator and verify equality."""
        destination = self._backup_directory / BACKUP_FILENAME
        try:
            backup = json.loads(destination.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise FileNotFoundError("no local backup exists") from error
        except UnicodeDecodeError as error:
            raise ValueError("backup must be UTF-8 encoded") from error
        except json.JSONDecodeError as error:
            raise ValueError("backup contains invalid JSON") from error
        document = self._validate_backup(backup)
        expected = export_document(
            document.media_items,
            document.exported_on,
            creators=document.creators,
            proposals=document.proposals,
            recommendations=document.recommendations,
            capture_proposals=document.capture_proposals or [],
        )

        def verify(
            items: list[MediaItem],
            creators: list[Creator],
            proposals: list[Proposal],
            recommendations: list[RecommendationRecord],
            capture_proposals: list[TypedEventProposal],
        ) -> None:
            restored = export_document(
                items,
                document.exported_on,
                creators=creators,
                proposals=proposals,
                recommendations=recommendations,
                capture_proposals=capture_proposals,
            )
            if restored != expected:
                raise RuntimeError("backup restore verification failed: restored export differs")

        self._repository.replace_document(
            document.media_items,
            document.creators,
            document.proposals,
            document.recommendations,
            document.capture_proposals or [],
            verify=verify,
        )
        return {"backup_version": BACKUP_VERSION, "items": len(document.media_items), "verified": True}

    @staticmethod
    def _validate_backup(payload: object):
        if not isinstance(payload, dict) or payload.get("backup_version") != BACKUP_VERSION:
            raise ValueError("unsupported backup version")
        if "export" not in payload:
            raise ValueError("backup is missing export")
        return parse_export_document(payload["export"])

    def get_media_item(self, item_id: str) -> MediaItem:
        return self._repository.get(item_id)

    def list_media_items(self, include_archived: bool = False) -> list[MediaItem]:
        return self._visible(self._repository.list_all(), include_archived)

    @staticmethod
    def _visible(items: list[MediaItem], include_archived: bool) -> list[MediaItem]:
        return items if include_archived else [item for item in items if item.archived_on is None]

    def create_media_item(self, payload: object) -> MediaItem:
        """Create one item atomically without replacing any active or archived stable ID."""
        item = MediaItem.model_validate(payload)
        self._validate_media_credits(item)
        try:
            self._repository.insert(item)
        except sqlite3.IntegrityError as error:
            raise MediaItemAlreadyExistsError(
                f"media item id already exists: {item.id!r}"
            ) from error
        return item

    def upsert_media_item(self, payload: object) -> MediaItem:
        """Validate and durably save one media item."""
        item = MediaItem.model_validate(payload)
        self._validate_media_credits(item)
        self._repository.save_media_guarded(
            item,
            lambda current, recommendations: self._validate_recommendation_evidence_after_update(
                current, item, recommendations
            ),
        )
        return item

    @staticmethod
    def _validate_recommendation_evidence_after_update(
        current: MediaItem | None,
        updated: MediaItem,
        recommendations: list[RecommendationRecord],
    ) -> None:
        current_observations = {
            observation.id: observation for observation in (current.observations if current else [])
        }
        updated_observations = {observation.id: observation for observation in updated.observations}
        for recommendation in recommendations:
            for evidence in recommendation.evidence:
                if evidence.media_item_id != updated.id:
                    continue
                before = current_observations.get(evidence.observation_id)
                after = updated_observations.get(evidence.observation_id)
                if before is None or after is None:
                    raise RecommendationReferenceConflictError(
                        "media update would remove a cited observation from a recommendation"
                    )
                if after != before:
                    raise RecommendationReferenceConflictError(
                        "media update would rewrite a cited recommendation observation"
                    )

    @classmethod
    def _validate_legacy_import_recommendation_references(
        cls,
        incoming_items: list[MediaItem],
        current_items: list[MediaItem],
        recommendations: list[RecommendationRecord],
    ) -> None:
        current_by_id = {item.id: item for item in current_items}
        for item in incoming_items:
            cls._validate_recommendation_evidence_after_update(
                current_by_id.get(item.id), item, recommendations
            )

    def _validate_media_credits(self, item: MediaItem) -> None:
        known_creator_ids = {creator.id for creator in self._repository.list_creators()}
        for credit in item.credits:
            if credit.creator_id not in known_creator_ids:
                raise ValueError(f"credit references unknown creator {credit.creator_id!r}")

    def delete_media_item(self, item_id: str) -> None:
        """Delete one media item by its stable ID."""
        def validate(_current: MediaItem, recommendations: list[RecommendationRecord]) -> None:
            for recommendation in recommendations:
                if recommendation.media_item_id == item_id:
                    raise RecommendationReferenceConflictError(
                        "media item is a recommendation target and cannot be deleted"
                    )
                if any(evidence.media_item_id == item_id for evidence in recommendation.evidence):
                    raise RecommendationReferenceConflictError(
                        "media item is a recommendation evidence source and cannot be deleted"
                    )

        self._repository.delete_media_guarded(item_id, validate)

    def archive_media_item(self, item_id: str, archived_on: date) -> MediaItem:
        """Retain one media item while marking it archived on a known date."""
        return self._repository.set_archived_on_guarded(
            item_id,
            archived_on,
            self._validate_recommendation_evidence_after_update,
        )

    def restore_media_item(self, item_id: str) -> MediaItem:
        """Return one archived media item to the active library."""
        return self._repository.set_archived_on_guarded(
            item_id,
            None,
            self._validate_recommendation_evidence_after_update,
        )

    def search_media_titles(self, query: str, include_archived: bool = False) -> list[MediaItem]:
        """Return stable-ID-ordered items whose titles contain the query."""
        normalized_query = query.casefold()
        return [
            item
            for item in self._visible(self._repository.list_all(), include_archived)
            if normalized_query in item.title.casefold()
            or any(normalized_query in alias.value.casefold() for alias in item.aliases)
        ]

    def filter_media_by_category(self, category: MediaCategory, include_archived: bool = False) -> list[MediaItem]:
        """Return stable-ID-ordered items in one media category."""
        return [
            item for item in self._visible(self._repository.list_all(), include_archived) if item.category is category
        ]

    def filter_media_by_status(self, status: ConsumptionStatus, include_archived: bool = False) -> list[MediaItem]:
        """Return stable-ID-ordered items in one consumption status."""
        return [item for item in self._visible(self._repository.list_all(), include_archived) if item.status is status]

    def filter_media_by_category_and_status(
        self, category: MediaCategory, status: ConsumptionStatus, include_archived: bool = False
    ) -> list[MediaItem]:
        """Return stable-ID-ordered items matching both filters."""
        return [
            item
            for item in self._visible(self._repository.list_all(), include_archived)
            if item.category is category and item.status is status
        ]

    def search_media_titles_by_category_and_status(
        self, query: str, category: MediaCategory, status: ConsumptionStatus, include_archived: bool = False
    ) -> list[MediaItem]:
        """Return title matches satisfying both filters in stable-ID order."""
        normalized_query = query.casefold()
        return [
            item
            for item in self.filter_media_by_category_and_status(category, status, include_archived)
            if normalized_query in item.title.casefold()
            or any(normalized_query in alias.value.casefold() for alias in item.aliases)
        ]

    def search_media_titles_by_category(
        self, query: str, category: MediaCategory, include_archived: bool = False
    ) -> list[MediaItem]:
        """Return title matches in one category in stable-ID order."""
        normalized_query = query.casefold()
        return [
            item
            for item in self.filter_media_by_category(category, include_archived)
            if normalized_query in item.title.casefold()
            or any(normalized_query in alias.value.casefold() for alias in item.aliases)
        ]

    def search_media_titles_by_status(
        self, query: str, status: ConsumptionStatus, include_archived: bool = False
    ) -> list[MediaItem]:
        """Return title matches in one status in stable-ID order."""
        normalized_query = query.casefold()
        return [
            item
            for item in self.filter_media_by_status(status, include_archived)
            if normalized_query in item.title.casefold()
            or any(normalized_query in alias.value.casefold() for alias in item.aliases)
        ]
