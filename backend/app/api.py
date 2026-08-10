"""Loopback application boundary for the local media library."""

from datetime import date
from pathlib import Path
import re

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.bootstrap import open_default_library
from app.domain import (
    ConsumptionStatus,
    Creator,
    CreatorRole,
    ExportDocument,
    MediaCategory,
    MediaItem,
    Proposal,
    RecommendationOutcomeEvent,
    RecommendationRecord,
    ReviewState,
)
from app.library_service import (
    CreatorAlreadyExistsError,
    ImportReviewStaleError,
    MediaItemAlreadyExistsError,
    RecommendationIdentityConflictError,
    RecommendationOutcomeIdentityConflictError,
    RecommendationReferenceConflictError,
)


def _serialize_import_document(document: ExportDocument) -> dict:
    serialized_document = document.model_dump(
        mode="json", exclude_none=True, exclude_defaults=True
    )
    if document.schema_version == "1.0":
        for item in serialized_document["media_items"]:
            item.pop("rating_history", None)
    if document.schema_version in {"1.3", "1.4", "1.5", "1.6", "1.7", "1.8"}:
        serialized_document["creators"] = [
            creator.model_dump(mode="json", exclude_none=True, exclude_defaults=True)
            for creator in document.creators
        ]
    if document.schema_version in {"1.4", "1.5", "1.6", "1.7", "1.8"}:
        serialized_document["proposals"] = [
            proposal.model_dump(mode="json", exclude_none=True, exclude_defaults=True)
            for proposal in document.proposals
        ]
    if document.schema_version in {"1.6", "1.7", "1.8"}:
        serialized_document["recommendations"] = [
            recommendation.model_dump(
                mode="json", exclude_none=True, exclude_defaults=True
            )
            for recommendation in document.recommendations
        ]
    # ``accepted`` is the Observation default, but assistant-inferred evidence must
    # carry that review decision explicitly when the service reparses this payload.
    for source_item, serialized_item in zip(document.media_items, serialized_document["media_items"]):
        for source_observation, serialized_observation in zip(
            source_item.observations, serialized_item.get("observations", [])
        ):
            if source_observation.provenance.value == "assistant_inferred":
                serialized_observation["review_state"] = source_observation.review_state.value
    for source_proposal, serialized_proposal in zip(
        document.proposals, serialized_document.get("proposals", [])
    ):
        source_observation = source_proposal.proposed_observation
        serialized_observation = serialized_proposal.get("proposed_observation")
        if (
            source_observation is not None
            and serialized_observation is not None
            and source_observation.provenance.value == "assistant_inferred"
        ):
            serialized_observation["review_state"] = source_observation.review_state.value
    if document.schema_version == "1.8":
        serialized_document["capture_proposals"] = []
        for source_proposal in document.capture_proposals or []:
            serialized_proposal = source_proposal.model_dump(
                mode="json", exclude_none=True, exclude_defaults=True
            )
            serialized_proposal["review_state"] = source_proposal.review_state.value
            serialized_document["capture_proposals"].append(serialized_proposal)
    return serialized_document


def create_app(web_root: Path | None = None) -> FastAPI:
    """Open the local library and expose application routes plus an optional built UI."""
    library = open_default_library()
    app = FastAPI(title="Personal Media Concierge")
    app.state.library_service = library

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}


    @app.get("/profile/rating-history")
    def rating_history_profile(include_archived: bool = False):
        return library.rating_history_profile(include_archived=include_archived)

    @app.get("/profile/dimensions/{dimension}")
    def dimension_profile(dimension: str, include_archived: bool = False):
        try:
            return library.dimension_profile(dimension, include_archived=include_archived)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.get("/profile/report")
    def taste_profile_report(include_archived: bool = False):
        return library.taste_profile_report(include_archived=include_archived)

    @app.get("/duplicates/candidates")
    def duplicate_candidate_collection(include_archived: bool = False):
        return library.duplicate_candidates(include_archived=include_archived)

    @app.get("/export")
    def export() -> dict:
        return library.export_document(date.today())

    @app.post("/backup")
    def create_backup() -> dict:
        return library.create_backup(date.today())

    @app.post("/backup/restore")
    def restore_backup() -> dict:
        try:
            return library.restore_backup()
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail="no local backup exists") from error
        except (ValueError, RuntimeError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.post("/import/review")
    def review_import_document(document: ExportDocument) -> dict:
        try:
            return library.review_import_document(_serialize_import_document(document))
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.post("/import")
    def import_document(document: ExportDocument, review_token: str | None = None) -> dict[str, int]:
        if review_token is not None and re.fullmatch(r"[0-9a-f]{64}", review_token) is None:
            raise HTTPException(status_code=422, detail="invalid import review token")
        serialized_document = _serialize_import_document(document)
        try:
            imported = (
                library.import_document(serialized_document)
                if review_token is None
                else library.import_document(
                    serialized_document, review_token=review_token
                )
            )
            return {"imported": imported}
        except ImportReviewStaleError as error:
            raise HTTPException(
                status_code=409,
                detail=str(error),
                headers={"X-Error-Code": "import-review-stale"},
            ) from error
        except (
            RecommendationIdentityConflictError,
            RecommendationReferenceConflictError,
        ) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.get("/recommendations")
    def list_recommendations():
        return [
            recommendation.model_dump(
                mode="json", exclude_none=True, exclude_defaults=True
            )
            for recommendation in library.list_recommendations()
        ]

    @app.post("/recommendations")
    def create_recommendation(recommendation: RecommendationRecord):
        try:
            stored, created = library.create_recommendation(
                recommendation.model_dump(
                    mode="json", exclude_none=True, exclude_defaults=True
                )
            )
        except RecommendationIdentityConflictError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return JSONResponse(
            status_code=201 if created else 200,
            content={
                "created": created,
                "recommendation": stored.model_dump(
                    mode="json", exclude_none=True, exclude_defaults=True
                ),
            },
        )

    @app.post("/recommendations/{recommendation_id}/outcomes")
    def append_recommendation_outcome(
        recommendation_id: str, outcome: RecommendationOutcomeEvent
    ):
        try:
            stored, created = library.append_recommendation_outcome(
                recommendation_id,
                outcome.model_dump(mode="json", exclude_none=True, exclude_defaults=True),
            )
        except RecommendationOutcomeIdentityConflictError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except KeyError as error:
            raise HTTPException(status_code=404, detail="recommendation not found") from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return JSONResponse(
            status_code=201 if created else 200,
            content={
                "created": created,
                "recommendation": stored.model_dump(
                    mode="json", exclude_none=True, exclude_defaults=True
                ),
            },
        )

    @app.post("/media", status_code=201)
    def create_media_item(item: MediaItem):
        try:
            library.create_media_item(
                item.model_dump(mode="json", exclude_none=True, exclude_defaults=True)
            )
        except MediaItemAlreadyExistsError as error:
            raise HTTPException(status_code=409, detail="media item id already exists") from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return item.model_dump(mode="json", exclude_none=True, exclude_defaults=True)

    @app.get("/media")
    def media_collection(
        title: str | None = None,
        category: MediaCategory | None = None,
        status: ConsumptionStatus | None = None,
        include_archived: bool = False,
    ):
        def collection_call(method, *args):
            return method(*args, include_archived=True) if include_archived else method(*args)

        normalized_title = title.strip() if title else ""
        if normalized_title and category is not None and status is not None:
            return collection_call(library.search_media_titles_by_category_and_status, normalized_title, category, status)
        if normalized_title and category is not None:
            return collection_call(library.search_media_titles_by_category, normalized_title, category)
        if normalized_title and status is not None:
            return collection_call(library.search_media_titles_by_status, normalized_title, status)
        if category is not None and status is not None:
            return collection_call(library.filter_media_by_category_and_status, category, status)
        if status is not None:
            return collection_call(library.filter_media_by_status, status)
        if category is not None:
            return collection_call(library.filter_media_by_category, category)
        if normalized_title:
            return collection_call(library.search_media_titles, normalized_title)
        return collection_call(library.list_media_items)

    @app.get("/proposals")
    def proposal_collection():
        return library.list_proposals()

    @app.post("/proposals")
    def submit_proposal(proposal: Proposal):
        try:
            library.submit_proposal(
                proposal.model_dump(mode="json", exclude_none=True, exclude_defaults=True)
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return proposal.model_dump(mode="json", exclude_none=True, exclude_defaults=True)

    @app.post("/proposals/{proposal_id}/accept")
    def accept_proposal(proposal_id: str):
        try:
            return library.review_proposal(proposal_id, ReviewState.ACCEPTED)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="proposal not found") from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.post("/proposals/{proposal_id}/reject")
    def reject_proposal(proposal_id: str):
        try:
            return library.review_proposal(proposal_id, ReviewState.REJECTED)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="proposal not found") from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.post("/proposals/{proposal_id}/promote")
    def promote_observation_proposal(proposal_id: str):
        try:
            return library.promote_observation_proposal(proposal_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="proposal or target media item not found") from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.post("/proposals/{proposal_id}/promote-media")
    def promote_media_proposal(proposal_id: str):
        try:
            return library.promote_media_proposal(proposal_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="proposal or promoted media item not found") from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.post("/creators", status_code=201)
    def create_creator(creator: Creator):
        try:
            library.create_creator(
                creator.model_dump(mode="json", exclude_none=True, exclude_defaults=True)
            )
        except CreatorAlreadyExistsError as error:
            raise HTTPException(status_code=409, detail="creator id already exists") from error
        return creator.model_dump(mode="json", exclude_none=True, exclude_defaults=True)

    @app.get("/creators")
    def creator_collection():
        return library.list_creators()

    @app.put("/creators/{creator_id}")
    def upsert_creator(creator_id: str, creator: Creator):
        if creator.id != creator_id:
            raise HTTPException(status_code=422, detail="path and body creator ids must match")
        library.upsert_creator(creator.model_dump(mode="json", exclude_none=True, exclude_defaults=True))
        return creator.model_dump(mode="json", exclude_none=True, exclude_defaults=True)

    @app.get("/creators/{creator_id}/media")
    def creator_media_collection(
        creator_id: str,
        role: CreatorRole | None = None,
        include_archived: bool = False,
    ):
        try:
            return library.list_media_for_creator(
                creator_id, role=role, include_archived=include_archived
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail="creator not found") from error

    @app.get("/creators/{creator_id}")
    def creator(creator_id: str):
        try:
            return library.get_creator(creator_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="creator not found") from error

    @app.put("/media/{item_id}")
    def upsert_media_item(item_id: str, item: MediaItem):
        if item.id != item_id:
            raise HTTPException(status_code=422, detail="path and body media item ids must match")
        try:
            library.upsert_media_item(
                item.model_dump(mode="json", exclude_none=True, exclude_defaults=True)
            )
        except RecommendationReferenceConflictError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return item.model_dump(mode="json", exclude_none=True, exclude_defaults=True)

    @app.post("/media/{item_id}/archive")
    def archive_media_item(item_id: str):
        try:
            return library.archive_media_item(item_id, date.today())
        except KeyError as error:
            raise HTTPException(status_code=404, detail="media item not found") from error

    @app.post("/media/{item_id}/restore")
    def restore_media_item(item_id: str):
        try:
            return library.restore_media_item(item_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="media item not found") from error

    @app.delete("/media/{item_id}", status_code=204)
    def delete_media_item(item_id: str) -> None:
        try:
            library.delete_media_item(item_id)
        except RecommendationReferenceConflictError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except KeyError as error:
            raise HTTPException(status_code=404, detail="media item not found") from error

    @app.get("/media/{item_id}")
    def media_item(item_id: str):
        try:
            return library.get_media_item(item_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="media item not found") from error

    if web_root is not None:
        resolved_web_root = Path(web_root).resolve()
        index = resolved_web_root / "index.html"
        assets = resolved_web_root / "assets"
        if not index.is_file() or not assets.is_dir():
            raise ValueError("built frontend must contain index.html and assets")

        @app.middleware("http")
        async def development_api_compatibility_prefix(request, call_next):
            path = request.scope["path"]
            if path == "/api" or path.startswith("/api/"):
                request.scope["path"] = path[4:] or "/"
            return await call_next(request)

        app.mount("/assets", StaticFiles(directory=assets), name="frontend-assets")

        @app.get("/{browser_path:path}", include_in_schema=False)
        def browser_application(browser_path: str):
            return FileResponse(index)

    return app
