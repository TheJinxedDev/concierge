"""Small, deterministic capture bridge for the fully automatic beta lane.

The beta intentionally does not attempt general-purpose sentiment analysis. It
only turns an ended user message into a reviewable observation when the message
contains both an exact canonical title/alias and an explicit consumption or
reaction cue. The later promotion job applies the independent confidence gate.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import re
from typing import Iterable

from .domain import (
    MediaItem,
    Observation,
    ObservationPolarity,
    ObservationScope,
    PrivacyLevel,
    Proposal,
    ProposalKind,
    Provenance,
    ReviewState,
)


_CONSUMPTION_RE = re.compile(
    r"\b(?:watched|watching|finished|completed|saw|played|read|started|"
    r"dropped|rewatched|rewatch|caught\s+up|beat|cleared)\b",
    re.IGNORECASE,
)
_POSITIVE_RE = re.compile(
    r"\b(?:liked|like|loved|love|enjoyed|enjoy|favorite|favourite|"
    r"great|good|amazing|excellent|brilliant)\b",
    re.IGNORECASE,
)
_NEGATIVE_RE = re.compile(
    r"\b(?:hated|hate|disliked|dislike|awful|terrible|bad|worst|"
    r"boring|weak)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class AutomaticCaptureMatch:
    """One exact canonical match and the evidence that made it actionable."""

    item: MediaItem
    matched_label: str
    confidence: float
    polarity: ObservationPolarity
    dimension: str


@dataclass(frozen=True)
class AutomaticCaptureExtraction:
    """Pure result for one ended user message."""

    match: AutomaticCaptureMatch | None
    reason: str


def _identity(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


def _labels(item: MediaItem) -> tuple[str, ...]:
    return (item.title, *(alias.value for alias in item.aliases))


def _find_matches(text: str, items: Iterable[MediaItem]) -> list[tuple[MediaItem, str]]:
    matches: list[tuple[MediaItem, str]] = []
    for item in sorted(items, key=lambda candidate: candidate.id):
        for label in _labels(item):
            if len(_identity(label)) < 4:
                continue
            pattern = rf"(?<!\w){re.escape(label.strip())}(?!\w)"
            if re.search(pattern, text, re.IGNORECASE):
                matches.append((item, label.strip()))
    return matches


def extract_automatic_capture(
    text: str,
    *,
    canonical_items: Iterable[MediaItem],
) -> AutomaticCaptureExtraction:
    """Extract one conservative observation candidate without side effects."""
    source = text.strip()
    if not source:
        return AutomaticCaptureExtraction(None, "empty_source")
    if not (_CONSUMPTION_RE.search(source) or _POSITIVE_RE.search(source) or _NEGATIVE_RE.search(source)):
        return AutomaticCaptureExtraction(None, "no_explicit_media_cue")

    matches = _find_matches(source, canonical_items)
    if not matches:
        return AutomaticCaptureExtraction(None, "no_exact_canonical_match")

    by_item: dict[str, tuple[MediaItem, str]] = {}
    for item, label in matches:
        current = by_item.get(item.id)
        if current is None or len(_identity(label)) > len(_identity(current[1])):
            by_item[item.id] = (item, label)
    if len(by_item) != 1:
        return AutomaticCaptureExtraction(None, "ambiguous_canonical_match")

    item, label = next(iter(by_item.values()))
    has_consumption = _CONSUMPTION_RE.search(source) is not None
    has_positive = _POSITIVE_RE.search(source) is not None
    has_negative = _NEGATIVE_RE.search(source) is not None
    if has_positive and has_negative:
        polarity = ObservationPolarity.MIXED
    elif has_positive:
        polarity = ObservationPolarity.POSITIVE
    elif has_negative:
        polarity = ObservationPolarity.NEGATIVE
    else:
        polarity = ObservationPolarity.NEUTRAL

    exact_title = label.casefold() == item.title.casefold()
    confidence = 0.95 if has_consumption and (has_positive or has_negative) else 0.92
    if not exact_title:
        confidence -= 0.04
    return AutomaticCaptureExtraction(
        AutomaticCaptureMatch(
            item=item,
            matched_label=label,
            confidence=confidence,
            polarity=polarity,
            dimension="emotional_reaction" if has_positive or has_negative else "consumption",
        ),
        "eligible_exact_match",
    )


def build_automatic_observation_proposal(
    *,
    source_text: str,
    source_ref: str,
    observed_at: datetime,
    match: AutomaticCaptureMatch,
) -> Proposal:
    """Build a stable pending legacy observation from one extraction result."""
    digest = hashlib.sha256(
        f"{source_ref}|{match.item.id}|{match.dimension}|{match.polarity.value}".encode(
            "utf-8"
        )
    ).hexdigest()[:24]
    observation_id = f"concierge-auto-observation-{digest}"
    proposal_id = f"concierge-auto-proposal-{digest}"
    observation = Observation(
        id=observation_id,
        scope=ObservationScope.WORK,
        polarity=match.polarity,
        dimension=match.dimension,
        text=source_text.strip(),
        provenance=Provenance.ASSISTANT_INFERRED,
        privacy=PrivacyLevel.ASSISTANT_READABLE,
        source_context=source_ref,
        confidence=match.confidence,
        review_state=ReviewState.NEEDS_REVIEW,
        observed_on=observed_at.date(),
    )
    return Proposal(
        id=proposal_id,
        target_media_item_id=match.item.id,
        kind=ProposalKind.OBSERVATION,
        proposed_observation=observation,
        source_context=source_ref,
        confidence=match.confidence,
        review_state=ReviewState.NEEDS_REVIEW,
        proposed_on=observed_at.date(),
    )
