from typing import Annotated, Any

from fastmcp import FastMCP
from pydantic import Field, TypeAdapter

from .bootstrap import open_default_library
from .domain import (
    ConsumptionStatus,
    MediaCategory,
    MediaItem,
    PrivacyLevel,
    Proposal,
    ReviewState,
)
from .library_service import LibraryService

mcp = FastMCP(
    "Concierge",
    instructions=(
        "Read factual canonical records from Concierge. "
        "These tools do not infer preferences or modify the library."
    ),
)


def _search_items(
    library: LibraryService,
    *,
    title: str,
    category: MediaCategory | None,
    status: ConsumptionStatus | None,
    include_archived: bool,
) -> list[MediaItem]:
    if title and category is not None and status is not None:
        return library.search_media_titles_by_category_and_status(
            title, category, status, include_archived
        )
    if title and category is not None:
        return library.search_media_titles_by_category(title, category, include_archived)
    if title and status is not None:
        return library.search_media_titles_by_status(title, status, include_archived)
    if title:
        return library.search_media_titles(title, include_archived)
    if category is not None and status is not None:
        return library.filter_media_by_category_and_status(
            category, status, include_archived
        )
    if category is not None:
        return library.filter_media_by_category(category, include_archived)
    if status is not None:
        return library.filter_media_by_status(status, include_archived)
    return library.list_media_items(include_archived)


def search_media_records(
    library: LibraryService,
    *,
    title: str = "",
    category: str | None = None,
    status: str | None = None,
    include_archived: bool = False,
    limit: int = 50,
) -> dict[str, Any]:
    """Search canonical media titles/aliases and return compact factual records."""
    normalized_title = title.strip()
    normalized_category = (
        TypeAdapter(MediaCategory).validate_python(category)
        if category is not None
        else None
    )
    normalized_status = (
        TypeAdapter(ConsumptionStatus).validate_python(status)
        if status is not None
        else None
    )
    normalized_limit = TypeAdapter(Annotated[int, Field(ge=1, le=100)]).validate_python(limit)
    matches = _search_items(
        library,
        title=normalized_title,
        category=normalized_category,
        status=normalized_status,
        include_archived=include_archived,
    )
    return {
        "items": [
            {
                "id": item.id,
                "title": item.title,
                "category": item.category,
                "status": item.status,
                "current_rating": (
                    item.rating.model_dump(mode="json") if item.rating else None
                ),
                "archived_on": (
                    item.archived_on.isoformat() if item.archived_on else None
                ),
            }
            for item in matches[:normalized_limit]
        ],
        "count": min(len(matches), normalized_limit),
        "has_more": len(matches) > normalized_limit,
        "include_archived": include_archived,
    }


def _assistant_accepted_observation_view(observation: Any) -> dict[str, Any] | None:
    if (
        observation.privacy is not PrivacyLevel.ASSISTANT_READABLE
        or observation.review_state is not ReviewState.ACCEPTED
    ):
        return None
    projected = observation.model_dump(mode="json")
    projected.pop("source_context", None)
    return projected


def _assistant_media_view(item: MediaItem) -> dict[str, Any]:
    """Project canonical media without private or non-reviewable evidence."""
    serialized = item.model_dump(mode="json")
    serialized["observations"] = [
        projected
        for observation in item.observations
        if (projected := _assistant_accepted_observation_view(observation)) is not None
    ]
    return serialized


def _assistant_profile_view(profile: Any) -> dict[str, Any]:
    """Project profile evidence without private source context."""
    serialized = profile.model_dump(mode="json")
    profile_nodes = [serialized]
    if "rating_history" in serialized:
        profile_nodes.append(serialized["rating_history"])
    profile_nodes.extend(serialized.get("dimensions", []))
    model_nodes = [profile]
    if hasattr(profile, "rating_history"):
        model_nodes.append(profile.rating_history)
    model_nodes.extend(getattr(profile, "dimensions", []))
    for model_node, serialized_node in zip(model_nodes, profile_nodes):
        for entry_model, entry_serialized in zip(
            getattr(model_node, "entries", []), serialized_node.get("entries", [])
        ):
            for field_name in (
                "supporting_evidence",
                "contradictory_evidence",
                "context_evidence",
            ):
                entry_serialized[field_name] = [
                    projected
                    for observation in getattr(entry_model, field_name, [])
                    if (projected := _assistant_accepted_observation_view(observation))
                    is not None
                ]
    return serialized


def _assistant_proposed_observation_view(observation: Any) -> dict[str, Any]:
    if observation.privacy is not PrivacyLevel.ASSISTANT_READABLE:
        return {
            "redacted": True,
            "privacy": observation.privacy.value,
            "review_state": observation.review_state.value,
        }
    projected = observation.model_dump(mode="json")
    if projected.get("source_context") is not None:
        projected["source_context"] = "[REDACTED]"
    return projected


def assistant_proposal_receipt_view(proposal: Proposal) -> dict[str, Any]:
    """Project a write receipt without returning trusted source evidence."""

    serialized = proposal.model_dump(mode="json", exclude_none=True, exclude_defaults=True)
    serialized["review_state"] = proposal.review_state.value
    if "source_context" in serialized:
        serialized["source_context"] = "[REDACTED]"
    kind = proposal.kind.value if hasattr(proposal.kind, "value") else proposal.kind
    if kind == "observation" and proposal.proposed_observation is not None:
        serialized["proposed_observation"] = _assistant_proposed_observation_view(
            proposal.proposed_observation
        )
    elif kind == "media_item" and proposal.proposed_media_item is not None:
        serialized["proposed_media_item"] = _assistant_proposed_media_item_view(
            proposal.proposed_media_item
        )
    return serialized


def _assistant_proposed_media_item_view(item: MediaItem) -> dict[str, Any]:
    """Keep sparse proposed-media receipts sparse while filtering evidence."""

    serialized = item.model_dump(mode="json", exclude_none=True, exclude_defaults=True)
    if item.observations:
        serialized["observations"] = [
            projected
            for observation in item.observations
            if (projected := _assistant_accepted_observation_view(observation))
            is not None
        ]
    return serialized


def get_media_record(
    library: LibraryService,
    item_id: str,
    *,
    include_archived: bool = False,
) -> dict[str, Any]:
    """Return one exact canonical record while keeping archived records opt-in."""
    normalized_item_id = item_id.strip()
    if not normalized_item_id:
        raise ValueError("media item ID must not be blank")
    try:
        item = library.get_media_item(normalized_item_id)
    except KeyError:
        return {"found": False, "include_archived": include_archived}
    if item.archived_on is not None and not include_archived:
        return {"found": False, "include_archived": include_archived}
    return {
        "found": True,
        "include_archived": include_archived,
        "item": _assistant_media_view(item),
    }


def get_taste_report_record(
    library: LibraryService,
    *,
    include_archived: bool = False,
) -> dict[str, Any]:
    """Return the exact deterministic composed report without generated claims."""
    report = library.taste_profile_report(include_archived=include_archived)
    return {
        "include_archived": include_archived,
        "report": _assistant_profile_view(report),
    }


def get_dimension_profile_record(
    library: LibraryService,
    dimension: str,
    *,
    include_archived: bool = False,
) -> dict[str, Any]:
    """Return one exact cited dimension projection with service-owned normalization."""
    profile = library.dimension_profile(
        dimension, include_archived=include_archived
    )
    return {
        "include_archived": include_archived,
        "profile": _assistant_profile_view(profile),
    }


def get_rating_history_record(
    library: LibraryService,
    *,
    include_archived: bool = False,
) -> dict[str, Any]:
    """Return exact chronological ratings and their cited evidence buckets."""
    profile = library.rating_history_profile(include_archived=include_archived)
    return {
        "include_archived": include_archived,
        "profile": _assistant_profile_view(profile),
    }


def list_evidence_dimension_records(
    library: LibraryService,
    *,
    include_archived: bool = False,
) -> dict[str, Any]:
    """Return compact normalized dimension discovery for the selected scope."""
    dimensions = library.list_evidence_dimensions(include_archived=include_archived)
    return {
        "include_archived": include_archived,
        "dimensions": dimensions,
        "count": len(dimensions),
    }


def submit_pending_proposal_record(
    library: LibraryService,
    proposal_payload: object,
    *,
    assistant_projection: bool = False,
) -> dict[str, Any]:
    """Persist one reviewable proposal, never a canonical media change."""
    proposal = Proposal.model_validate(proposal_payload)
    if proposal.review_state is not ReviewState.NEEDS_REVIEW:
        raise ValueError("new proposals must begin as needs_review")
    payload = proposal.model_dump(mode="json", exclude_none=True, exclude_defaults=True)
    payload["review_state"] = proposal.review_state.value
    persisted = Proposal.model_validate(library.submit_proposal(payload))
    serialized = (
        assistant_proposal_receipt_view(persisted)
        if assistant_projection
        else persisted.model_dump(mode="json", exclude_none=True, exclude_defaults=True)
    )
    serialized["review_state"] = persisted.review_state.value
    return {"proposal": serialized, "canonical_media_changed": False}


def _pending_proposal_view(
    library: LibraryService,
    proposal: Proposal,
    *,
    include_archived: bool,
) -> dict[str, Any] | None:
    target_media_item_id = proposal.target_media_item_id
    if target_media_item_id is None:
        canonical = {"found": False, "item": None}
    else:
        try:
            item = library.get_media_item(target_media_item_id)
        except KeyError:
            return None
        if item.archived_on is not None and not include_archived:
            return None
        canonical = {
            "found": True,
            "item": _assistant_media_view(item),
        }

    kind = proposal.kind.value if hasattr(proposal.kind, "value") else proposal.kind
    if kind == "observation":
        proposed = {
            "observation": _assistant_proposed_observation_view(
                proposal.proposed_observation
            )
        }
        provenance = proposal.proposed_observation.provenance.value
    elif kind == "metadata":
        proposed = {
            "metadata_field": proposal.metadata_field,
            "metadata_value": proposal.metadata_value,
        }
        provenance = None
    elif kind == "media_item":
        proposed = {"media_item": _assistant_media_view(proposal.proposed_media_item)}
        provenance = None
    elif kind == "rating_event":
        proposed = {"rating_event": proposal.rating_event.model_dump(mode="json")}
        provenance = proposal.provenance.value
    else:
        proposed = {"progress_event": proposal.progress_event.model_dump(mode="json")}
        provenance = proposal.provenance.value

    return {
        "id": proposal.id,
        "kind": kind,
        "review_state": proposal.review_state.value,
        "target_media_item_id": target_media_item_id,
        "source_context": "[REDACTED]",
        "provenance": provenance,
        "confidence": proposal.confidence,
        "proposed_on": proposal.proposed_on.isoformat(),
        "contradiction_notes": getattr(proposal, "contradiction_notes", None),
        "canonical": canonical,
        "proposed": proposed,
    }


def list_pending_proposal_records(
    library: LibraryService,
    *,
    target_media_item_id: str | None = None,
    kind: str | None = None,
    review_state: str = "needs_review",
    include_archived: bool = False,
    limit: int = 50,
) -> dict[str, Any]:
    """Return bounded, exact pending-proposal views without a write path."""
    normalized_review_state = TypeAdapter(ReviewState).validate_python(review_state)
    normalized_limit = TypeAdapter(Annotated[int, Field(ge=1, le=100)]).validate_python(limit)
    proposals = library.list_pending_proposals(
        target_media_item_id=target_media_item_id,
        kind=kind,
        review_state=normalized_review_state,
        include_archived=include_archived,
    )
    views = [
        view
        for proposal in proposals
        if (view := _pending_proposal_view(
            library, proposal, include_archived=include_archived
        ))
        is not None
    ]
    views.sort(key=lambda proposal: proposal["id"])
    return {
        "items": views[:normalized_limit],
        "count": min(len(views), normalized_limit),
        "has_more": len(views) > normalized_limit,
        "include_archived": include_archived,
        "review_state": normalized_review_state.value,
    }


def get_proposal_record(
    library: LibraryService,
    proposal_id: str,
    *,
    include_archived: bool = False,
) -> dict[str, Any]:
    """Return one exact proposal view, hiding archived targets by default."""
    normalized_id = proposal_id.strip()
    if not normalized_id:
        raise ValueError("proposal ID must not be blank")
    try:
        proposal = library.get_proposal(normalized_id)
    except KeyError:
        return {"found": False, "include_archived": include_archived}
    view = _pending_proposal_view(
        library, proposal, include_archived=include_archived
    )
    if view is None:
        return {"found": False, "include_archived": include_archived}
    return {
        "found": True,
        "item": view,
        "include_archived": include_archived,
    }


@mcp.tool
def submit_pending_proposal(proposal: Proposal) -> dict[str, Any]:
    """Store one needs-review candidate only; this cannot accept, promote, or alter canonical media."""
    return submit_pending_proposal_record(
        open_default_library(),
        proposal.model_dump(mode="json", exclude_none=True, exclude_defaults=False),
        assistant_projection=True,
    )


@mcp.tool
def list_pending_proposals(
    target_media_item_id: str | None = None,
    kind: str | None = None,
    review_state: str = "needs_review",
    include_archived: bool = False,
    limit: Annotated[int, Field(ge=1, le=100)] = 50,
) -> dict[str, Any]:
    """List bounded pending proposal views; this is read-only and archive-aware."""
    return list_pending_proposal_records(
        open_default_library(),
        target_media_item_id=target_media_item_id,
        kind=kind,
        review_state=review_state,
        include_archived=include_archived,
        limit=limit,
    )


@mcp.tool
def get_proposal(
    proposal_id: Annotated[str, Field(min_length=1)],
    include_archived: bool = False,
) -> dict[str, Any]:
    """Get one exact proposal view; archived targets are opt-in."""
    return get_proposal_record(
        open_default_library(), proposal_id, include_archived=include_archived
    )


@mcp.tool
def list_evidence_dimensions(include_archived: bool = False) -> dict[str, Any]:
    """List dimensions with usable cited evidence; archived records are opt-in."""
    return list_evidence_dimension_records(
        open_default_library(), include_archived=include_archived
    )


@mcp.tool
def get_rating_history(include_archived: bool = False) -> dict[str, Any]:
    """Get chronological rating histories with cited evidence; archives are opt-in."""
    return get_rating_history_record(
        open_default_library(), include_archived=include_archived
    )


@mcp.tool
def get_dimension_profile(
    dimension: Annotated[str, Field(min_length=1)],
    include_archived: bool = False,
) -> dict[str, Any]:
    """Get cited evidence for one taste dimension; archived records are opt-in."""
    return get_dimension_profile_record(
        open_default_library(), dimension, include_archived=include_archived
    )


@mcp.tool
def get_taste_report(include_archived: bool = False) -> dict[str, Any]:
    """Get the complete cited factual taste report; archived records are opt-in."""
    return get_taste_report_record(
        open_default_library(), include_archived=include_archived
    )


@mcp.tool
def get_media(
    item_id: Annotated[str, Field(min_length=1)],
    include_archived: bool = False,
) -> dict[str, Any]:
    """Get one complete canonical media record by stable ID; archived records are opt-in."""
    return get_media_record(
        open_default_library(), item_id, include_archived=include_archived
    )


@mcp.tool
def search_media(
    title: str = "",
    category: str | None = None,
    status: str | None = None,
    include_archived: bool = False,
    limit: Annotated[int, Field(ge=1, le=100)] = 50,
) -> dict[str, Any]:
    """Search title/aliases with optional category/status filters; archived records are opt-in."""
    return search_media_records(
        open_default_library(),
        title=title,
        category=category,
        status=status,
        include_archived=include_archived,
        limit=limit,
    )


if __name__ == "__main__":
    mcp.run()
