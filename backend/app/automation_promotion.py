"""Beta confidence gate and runner for automatic capture promotion."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Iterable

from .capture_contract import (
    MediaItemProposal,
    ProgressEventProposal,
    RatingEventProposal,
)
from .domain import (
    ConflictState,
    Proposal,
    ProposalKind,
    Provenance,
    ReviewState,
)

if TYPE_CHECKING:
    from .library_service import LibraryService


AUTO_PROMOTION_THRESHOLD = 0.85


class AutoPromotionReason(str, Enum):
    ELIGIBLE = "eligible"
    LOW_CONFIDENCE = "low_confidence"
    NOT_PENDING = "not_pending"
    INCOMPLETE_SOURCE = "incomplete_source"
    CONFLICT = "conflict"
    UNSUPPORTED_KIND = "unsupported_kind"
    AMBIGUOUS_TARGET = "ambiguous_target"
    DUPLICATE_TARGET = "duplicate_target"
    INFERRED_SCORE = "inferred_score"
    UNSUPPORTED_PROVENANCE = "unsupported_provenance"


@dataclass(frozen=True)
class AutoPromotionDecision:
    proposal_id: str
    eligible: bool
    reason: AutoPromotionReason
    threshold: float


@dataclass(frozen=True)
class AutoPromotionResult:
    proposal_id: str
    decision: AutoPromotionDecision
    promoted: bool
    error: str | None = None


ProposalLike = Proposal | MediaItemProposal | RatingEventProposal | ProgressEventProposal


def _decision(
    proposal: ProposalLike,
    eligible: bool,
    reason: AutoPromotionReason,
    threshold: float,
) -> AutoPromotionDecision:
    return AutoPromotionDecision(
        proposal_id=proposal.id,
        eligible=eligible,
        reason=reason,
        threshold=threshold,
    )


def assess_auto_promotion(
    proposal: ProposalLike,
    *,
    canonical_media_item_ids: Iterable[str] = (),
    threshold: float = AUTO_PROMOTION_THRESHOLD,
) -> AutoPromotionDecision:
    """Apply the deliberately small beta rubric without mutating anything."""
    if not 0 <= threshold <= 1:
        raise ValueError("auto-promotion threshold must be between 0 and 1")
    if proposal.review_state is not ReviewState.NEEDS_REVIEW:
        return _decision(proposal, False, AutoPromotionReason.NOT_PENDING, threshold)
    if proposal.confidence < threshold:
        return _decision(proposal, False, AutoPromotionReason.LOW_CONFIDENCE, threshold)
    if not proposal.source_context.strip():
        return _decision(proposal, False, AutoPromotionReason.INCOMPLETE_SOURCE, threshold)
    if hasattr(proposal, "conflict_state") and proposal.conflict_state is not ConflictState.NONE:
        return _decision(proposal, False, AutoPromotionReason.CONFLICT, threshold)
    canonical_ids = set(canonical_media_item_ids)
    target = getattr(proposal, "target_media_item_id", None)
    if isinstance(proposal, Proposal):
        if proposal.kind is ProposalKind.OBSERVATION:
            if target is None or proposal.proposed_observation is None:
                return _decision(proposal, False, AutoPromotionReason.AMBIGUOUS_TARGET, threshold)
            return _decision(proposal, True, AutoPromotionReason.ELIGIBLE, threshold)
        if proposal.kind is ProposalKind.MEDIA_ITEM:
            candidate = proposal.proposed_media_item
            if candidate is None:
                return _decision(proposal, False, AutoPromotionReason.AMBIGUOUS_TARGET, threshold)
            if candidate.id in canonical_ids:
                return _decision(proposal, False, AutoPromotionReason.DUPLICATE_TARGET, threshold)
            return _decision(proposal, True, AutoPromotionReason.ELIGIBLE, threshold)
        return _decision(proposal, False, AutoPromotionReason.UNSUPPORTED_KIND, threshold)

    if proposal.provenance in {Provenance.IMPORTED_METADATA, Provenance.EXTERNAL_REFERENCE}:
        return _decision(proposal, False, AutoPromotionReason.UNSUPPORTED_PROVENANCE, threshold)
    if target is None or not target.strip():
        return _decision(proposal, False, AutoPromotionReason.AMBIGUOUS_TARGET, threshold)
    if isinstance(proposal, RatingEventProposal) and proposal.provenance is Provenance.ASSISTANT_INFERRED:
        return _decision(proposal, False, AutoPromotionReason.INFERRED_SCORE, threshold)
    if isinstance(proposal, (RatingEventProposal, ProgressEventProposal)):
        return _decision(proposal, True, AutoPromotionReason.ELIGIBLE, threshold)
    return _decision(proposal, False, AutoPromotionReason.UNSUPPORTED_KIND, threshold)


def select_auto_promotions(
    proposals: Iterable[ProposalLike],
    *,
    canonical_media_item_ids: Iterable[str] = (),
    threshold: float = AUTO_PROMOTION_THRESHOLD,
) -> list[AutoPromotionDecision]:
    return [
        assess_auto_promotion(
            proposal,
            canonical_media_item_ids=canonical_media_item_ids,
            threshold=threshold,
        )
        for proposal in sorted(proposals, key=lambda candidate: candidate.id)
    ]


def run_auto_promotion(
    service: "LibraryService",
    *,
    threshold: float = AUTO_PROMOTION_THRESHOLD,
) -> list[AutoPromotionResult]:
    """Evaluate and atomically apply one bounded pending-proposal pass."""
    pending = service.list_pending_proposals(include_archived=False)
    canonical_ids = {item.id for item in service.list_media_items(include_archived=True)}
    results: list[AutoPromotionResult] = []
    for proposal in sorted(pending, key=lambda candidate: candidate.id):
        decision = assess_auto_promotion(
            proposal,
            canonical_media_item_ids=canonical_ids,
            threshold=threshold,
        )
        if not decision.eligible:
            results.append(AutoPromotionResult(proposal.id, decision, False))
            continue
        try:
            service.auto_promote_proposal(proposal.id)
        except Exception as error:
            results.append(AutoPromotionResult(proposal.id, decision, False, str(error)))
            continue
        if isinstance(proposal, MediaItemProposal):
            canonical_ids.add(proposal.proposed_media_item.id)
        results.append(AutoPromotionResult(proposal.id, decision, True))
    return results
