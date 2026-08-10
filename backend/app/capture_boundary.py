"""Pure contract for the semantic-beta automatic capture boundary.

This module only decides where an already extracted candidate may go. It does
not persist records, mutate canonical media, schedule work, or consult the
quarantined delayed auto-promotion experiment.
"""

from dataclasses import dataclass
from enum import Enum

from .domain import Provenance


class CaptureMode(str, Enum):
    FULL_AUTO = "full_auto"
    PENDING_ONLY = "pending_only"
    OFF = "off"


class CaptureStatus(str, Enum):
    CANONICAL = "canonical"
    PENDING = "pending"
    CLARIFICATION_REQUIRED = "clarification_required"
    BLOCKED = "blocked"
    OFF = "off"


class CaptureReason(str, Enum):
    CLEAR_EXPLICIT = "clear_explicit"
    PENDING_ONLY_MODE = "pending_only_mode"
    CAPTURE_OFF = "capture_off"
    INFERRED_REQUIRES_REVIEW = "inferred_requires_review"
    CONFLICT_REQUIRES_REVIEW = "conflict_requires_review"
    RATING_DATE_REQUIRED = "rating_date_required"
    AMBIGUOUS_TARGET = "ambiguous_target"
    INCOMPLETE_CONTRACT = "incomplete_contract"
    INVALID_CONTRACT = "invalid_contract"
    UNSUPPORTED_CONTENT = "unsupported_content"
    SECRET_CONTENT = "secret_content"
    ESTIMATED_SCORE_FORBIDDEN = "estimated_score_forbidden"


class NumericScoreKind(str, Enum):
    NONE = "none"
    LITERAL_USER = "literal_user"
    ESTIMATED = "estimated"


@dataclass(frozen=True)
class CaptureCandidate:
    """The truth properties needed to disposition one extracted candidate.

    Typed event payloads are intentionally not defined here; that is the P1.2
    slice. This small boundary object only carries the hard gates that must be
    independent of the selected capture mode.
    """

    provenance: Provenance
    target_resolved: bool
    complete: bool
    contract_valid: bool
    ambiguous: bool = False
    conflicting: bool = False
    unsupported: bool = False
    contains_secret: bool = False
    score_kind: NumericScoreKind = NumericScoreKind.NONE
    rating_date_exact: bool = False


@dataclass(frozen=True)
class CaptureDecision:
    """A visible disposition; callers must not infer status from booleans."""

    mode: CaptureMode
    status: CaptureStatus
    reason: CaptureReason
    canonical_write: bool
    pending_capture: bool
    allows_literal_rating: bool


def _decision(
    mode: CaptureMode,
    status: CaptureStatus,
    reason: CaptureReason,
    *,
    canonical_write: bool = False,
    pending_capture: bool = False,
    allows_literal_rating: bool = False,
) -> CaptureDecision:
    return CaptureDecision(
        mode=mode,
        status=status,
        reason=reason,
        canonical_write=canonical_write,
        pending_capture=pending_capture,
        allows_literal_rating=allows_literal_rating,
    )


def _review_or_off(
    mode: CaptureMode,
    reason: CaptureReason,
    *,
    allows_literal_rating: bool = False,
) -> CaptureDecision:
    if mode is CaptureMode.OFF:
        return _decision(mode, CaptureStatus.OFF, reason)
    return _decision(
        mode,
        CaptureStatus.PENDING,
        reason,
        pending_capture=True,
        allows_literal_rating=allows_literal_rating,
    )


def decide_capture(
    mode: CaptureMode | str,
    candidate: CaptureCandidate,
) -> CaptureDecision:
    """Apply the beta mode boundary to one already extracted candidate.

    Safety and truth gates run before mode disposition. A mode can choose
    canonical append, pending capture, or no automatic entry; it cannot make
    malformed, secret, unsupported, ambiguous, conflicting, inferred, or
    estimated material canonical.
    """

    selected_mode = CaptureMode(mode)

    if not candidate.contract_valid:
        return _decision(selected_mode, CaptureStatus.BLOCKED, CaptureReason.INVALID_CONTRACT)
    if candidate.unsupported:
        return _decision(selected_mode, CaptureStatus.BLOCKED, CaptureReason.UNSUPPORTED_CONTENT)
    if candidate.contains_secret:
        return _decision(selected_mode, CaptureStatus.BLOCKED, CaptureReason.SECRET_CONTENT)
    if candidate.score_kind is NumericScoreKind.ESTIMATED:
        return _decision(
            selected_mode,
            CaptureStatus.BLOCKED,
            CaptureReason.ESTIMATED_SCORE_FORBIDDEN,
        )
    if candidate.provenance not in {
        Provenance.USER_EXPLICIT,
        Provenance.ASSISTANT_INFERRED,
    }:
        return _decision(selected_mode, CaptureStatus.BLOCKED, CaptureReason.UNSUPPORTED_CONTENT)
    if candidate.score_kind is NumericScoreKind.LITERAL_USER and (
        candidate.provenance is not Provenance.USER_EXPLICIT
    ):
        return _decision(
            selected_mode,
            CaptureStatus.BLOCKED,
            CaptureReason.ESTIMATED_SCORE_FORBIDDEN,
        )

    if not candidate.target_resolved or candidate.ambiguous:
        return _decision(
            selected_mode,
            CaptureStatus.CLARIFICATION_REQUIRED,
            CaptureReason.AMBIGUOUS_TARGET,
        )
    if not candidate.complete:
        return _decision(
            selected_mode,
            CaptureStatus.CLARIFICATION_REQUIRED,
            CaptureReason.INCOMPLETE_CONTRACT,
        )
    if candidate.score_kind is NumericScoreKind.LITERAL_USER and not candidate.rating_date_exact:
        return _review_or_off(selected_mode, CaptureReason.RATING_DATE_REQUIRED)
    if candidate.provenance is Provenance.ASSISTANT_INFERRED:
        return _review_or_off(selected_mode, CaptureReason.INFERRED_REQUIRES_REVIEW)
    if candidate.conflicting:
        return _review_or_off(selected_mode, CaptureReason.CONFLICT_REQUIRES_REVIEW)
    if selected_mode is CaptureMode.OFF:
        return _decision(selected_mode, CaptureStatus.OFF, CaptureReason.CAPTURE_OFF)
    if selected_mode is CaptureMode.PENDING_ONLY:
        return _decision(
            selected_mode,
            CaptureStatus.PENDING,
            CaptureReason.PENDING_ONLY_MODE,
            pending_capture=True,
            allows_literal_rating=candidate.score_kind is NumericScoreKind.LITERAL_USER,
        )
    return _decision(
        selected_mode,
        CaptureStatus.CANONICAL,
        CaptureReason.CLEAR_EXPLICIT,
        canonical_write=True,
        allows_literal_rating=candidate.score_kind is NumericScoreKind.LITERAL_USER,
    )
