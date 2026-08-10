"""SQLite persistence that preserves the versioned media-record contract."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path

from app.domain import (
    Creator,
    MediaItem,
    Proposal,
    ProposalKind,
    ProgressRecord,
    Rating,
    RecommendationOutcomeEvent,
    RecommendationRecord,
    ReviewState,
    TypedEventProposal,
    parse_typed_event_proposal,
)


class RecommendationIdentityConflictError(ValueError):
    """A create-only recommendation import reused an ID with different content."""


class RecommendationOutcomeIdentityConflictError(ValueError):
    """An append-only outcome reused an ID with different content."""


_MIGRATIONS: tuple[tuple[int, str, str], ...] = (
    (
        1,
        "initial_media_items",
        """
        CREATE TABLE media_items (
            id TEXT PRIMARY KEY,
            payload TEXT NOT NULL
        )
        """,
    ),
    (
        2,
        "creator_entities",
        """
        CREATE TABLE creators (
            id TEXT PRIMARY KEY,
            payload TEXT NOT NULL
        )
        """,
    ),
    (
        3,
        "proposal_queue",
        """
        CREATE TABLE proposals (
            id TEXT PRIMARY KEY,
            payload TEXT NOT NULL
        )
        """,
    ),
    (
        4,
        "recommendation_ledger",
        """
        CREATE TABLE recommendations (
            id TEXT PRIMARY KEY,
            payload TEXT NOT NULL
        )
        """,
    ),
    (
        5,
        "typed_capture_proposal_queue",
        """
        CREATE TABLE capture_proposals (
            id TEXT PRIMARY KEY,
            idempotency_key TEXT NOT NULL UNIQUE,
            payload TEXT NOT NULL
        )
        """,
    ),
    (
        6,
        "typed_capture_event_identity",
        "ALTER TABLE capture_proposals ADD COLUMN event_id TEXT",
    ),
)


def migrate(database_path: Path) -> None:
    """Apply every known schema migration to a local SQLite database."""
    database_path.parent.mkdir(parents=True, exist_ok=True)
    with _connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL
            )
            """
        )
        applied_migrations = dict(
            connection.execute("SELECT version, name FROM schema_migrations")
        )
        known_migrations = {version: name for version, name, _ in _MIGRATIONS}
        unknown_versions = applied_migrations.keys() - known_migrations.keys()
        if unknown_versions:
            raise RuntimeError(
                "database has migrations this application does not understand: "
                f"{sorted(unknown_versions)}"
            )
        mismatched_versions = {
            version: applied_name
            for version, applied_name in applied_migrations.items()
            if applied_name != known_migrations[version]
        }
        if mismatched_versions:
            raise RuntimeError(
                "database migration identity does not match this application: "
                f"{mismatched_versions}"
            )

        for version, name, statement in _MIGRATIONS:
            if version not in applied_migrations:
                connection.execute(statement)
                connection.execute(
                    "INSERT INTO schema_migrations (version, name) VALUES (?, ?)",
                    (version, name),
                )
        connection.execute(
            """
            UPDATE capture_proposals
            SET event_id = COALESCE(
                json_extract(payload, '$.rating_event.event_id'),
                json_extract(payload, '$.progress_event.event_id')
            )
            WHERE event_id IS NULL
            """
        )
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS capture_proposals_event_id
            ON capture_proposals (event_id)
            """
        )



class MediaRepository:
    """Persist and retrieve media items without weakening their export contract."""

    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path

    def save(self, item: MediaItem) -> None:
        self.save_all([item])

    def insert(self, item: MediaItem) -> None:
        """Atomically insert one new stable ID without replacing an existing record."""
        with _connect(self._database_path) as connection:
            connection.execute(
                "INSERT INTO media_items (id, payload) VALUES (?, ?)",
                (
                    item.id,
                    json.dumps(
                        item.model_dump(mode="json"), separators=(",", ":"), sort_keys=True
                    ),
                ),
            )

    def save_all(self, items: list[MediaItem]) -> None:
        payloads = [
            (
                item.id,
                json.dumps(
                    item.model_dump(mode="json"), separators=(",", ":"), sort_keys=True
                ),
            )
            for item in items
        ]
        with _connect(self._database_path) as connection:
            connection.executemany(
                """
                INSERT INTO media_items (id, payload) VALUES (?, ?)
                ON CONFLICT(id) DO UPDATE SET payload = excluded.payload
                """,
                payloads,
            )

    def save_document(
        self,
        items: list[MediaItem],
        creators: list[Creator],
        proposals: list[Proposal],
        recommendations: list[RecommendationRecord],
        replace_proposals: bool = False,
        replace_recommendations: bool = False,
        capture_proposals: list[TypedEventProposal] | None = None,
        replace_capture_proposals: bool = False,
        validate_preserved_recommendations: Callable[
            [list[MediaItem], list[MediaItem], list[RecommendationRecord]], None
        ] | None = None,
        validate_current_document: Callable[
            [
                tuple[
                    list[MediaItem],
                    list[Creator],
                    list[Proposal],
                    list[RecommendationRecord],
                    list[TypedEventProposal],
                ]
            ],
            None,
        ] | None = None,
    ) -> None:
        """Atomically save a validated export document's top-level collections."""
        capture_proposal_payloads = [
            (
                proposal.id,
                proposal.idempotency_key,
                proposal.rating_event.event_id
                if proposal.kind == "rating_event"
                else proposal.progress_event.event_id,
                json.dumps(
                    proposal.model_dump(mode="json"),
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            )
            for proposal in capture_proposals or []
        ]
        creator_payloads = [
            (
                creator.id,
                json.dumps(
                    creator.model_dump(mode="json"), separators=(",", ":"), sort_keys=True
                ),
            )
            for creator in creators
        ]
        item_payloads = [
            (
                item.id,
                json.dumps(
                    item.model_dump(mode="json"), separators=(",", ":"), sort_keys=True
                ),
            )
            for item in items
        ]
        proposal_payloads = [
            (
                proposal.id,
                json.dumps(
                    proposal.model_dump(mode="json"), separators=(",", ":"), sort_keys=True
                ),
            )
            for proposal in proposals
        ]
        recommendation_payloads = [
            (
                recommendation.id,
                json.dumps(
                    recommendation.model_dump(mode="json"),
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            )
            for recommendation in recommendations
        ]
        with _connect(self._database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            if validate_current_document is not None:
                validate_current_document(
                    self._load_document_with_capture_proposals_from_connection(connection)
                )
            if validate_preserved_recommendations is not None:
                current_item_rows = connection.execute(
                    "SELECT payload FROM media_items ORDER BY id"
                ).fetchall()
                recommendation_rows = connection.execute(
                    "SELECT payload FROM recommendations ORDER BY id"
                ).fetchall()
                validate_preserved_recommendations(
                    items,
                    [MediaItem.model_validate_json(row["payload"]) for row in current_item_rows],
                    [
                        RecommendationRecord.model_validate_json(row["payload"])
                        for row in recommendation_rows
                    ],
                )
            if not replace_recommendations:
                existing_rows = connection.execute(
                    "SELECT id, payload FROM recommendations"
                ).fetchall()
                existing = {
                    row["id"]: RecommendationRecord.model_validate_json(row["payload"])
                    for row in existing_rows
                }
                for recommendation in recommendations:
                    if (
                        recommendation.id in existing
                        and existing[recommendation.id] != recommendation
                    ):
                        raise RecommendationIdentityConflictError(
                            f"recommendation id conflict: {recommendation.id!r}"
                        )
                recommendation_payloads = [
                    payload
                    for payload in recommendation_payloads
                    if payload[0] not in existing
                ]
            connection.executemany(
                """
                INSERT INTO creators (id, payload) VALUES (?, ?)
                ON CONFLICT(id) DO UPDATE SET payload = excluded.payload
                """,
                creator_payloads,
            )
            connection.executemany(
                """
                INSERT INTO media_items (id, payload) VALUES (?, ?)
                ON CONFLICT(id) DO UPDATE SET payload = excluded.payload
                """,
                item_payloads,
            )
            if replace_proposals:
                connection.execute("DELETE FROM proposals")
            connection.executemany(
                """
                INSERT INTO proposals (id, payload) VALUES (?, ?)
                ON CONFLICT(id) DO UPDATE SET payload = excluded.payload
                """,
                proposal_payloads,
            )
            if replace_recommendations:
                connection.execute("DELETE FROM recommendations")
            connection.executemany(
                """
                INSERT INTO recommendations (id, payload) VALUES (?, ?)
                ON CONFLICT(id) DO UPDATE SET payload = excluded.payload
                """,
                recommendation_payloads,
            )
            if replace_capture_proposals:
                connection.execute("DELETE FROM capture_proposals")
            if capture_proposals is not None:
                connection.executemany(
                    """
                    INSERT INTO capture_proposals (id, idempotency_key, event_id, payload)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        idempotency_key = excluded.idempotency_key,
                        event_id = excluded.event_id,
                        payload = excluded.payload
                    """,
                    capture_proposal_payloads,
                )

    def list_all(self) -> list[MediaItem]:
        with _connect(self._database_path) as connection:
            rows = connection.execute(
                "SELECT payload FROM media_items ORDER BY id"
            ).fetchall()
        return [MediaItem.model_validate_json(row["payload"]) for row in rows]

    def list_creators(self) -> list[Creator]:
        with _connect(self._database_path) as connection:
            rows = connection.execute("SELECT payload FROM creators ORDER BY id").fetchall()
        return [Creator.model_validate_json(row["payload"]) for row in rows]

    def taste_profile_snapshot(self) -> tuple[list[MediaItem], list[Creator]]:
        """Read media and creator identities from one SQLite snapshot."""
        with _connect(self._database_path) as connection:
            connection.execute("BEGIN")
            item_rows = connection.execute(
                "SELECT payload FROM media_items ORDER BY id"
            ).fetchall()
            creator_rows = connection.execute(
                "SELECT payload FROM creators ORDER BY id"
            ).fetchall()
        return (
            [MediaItem.model_validate_json(row["payload"]) for row in item_rows],
            [Creator.model_validate_json(row["payload"]) for row in creator_rows],
        )

    def save_creator(self, creator: Creator) -> None:
        with _connect(self._database_path) as connection:
            connection.execute(
                """
                INSERT INTO creators (id, payload) VALUES (?, ?)
                ON CONFLICT(id) DO UPDATE SET payload = excluded.payload
                """,
                (
                    creator.id,
                    json.dumps(
                        creator.model_dump(mode="json"), separators=(",", ":"), sort_keys=True
                    ),
                ),
            )

    def insert_creator(self, creator: Creator) -> None:
        """Insert a creator identity without allowing stable-ID replacement."""
        with _connect(self._database_path) as connection:
            connection.execute(
                "INSERT INTO creators (id, payload) VALUES (?, ?)",
                (
                    creator.id,
                    json.dumps(
                        creator.model_dump(mode="json"), separators=(",", ":"), sort_keys=True
                    ),
                ),
            )

    def list_proposals(self) -> list[Proposal]:
        with _connect(self._database_path) as connection:
            rows = connection.execute("SELECT payload FROM proposals ORDER BY id").fetchall()
        return [Proposal.model_validate_json(row["payload"]) for row in rows]

    def insert_capture_proposal(self, proposal: TypedEventProposal) -> None:
        """Insert one typed event proposal without replacing an existing identity."""
        event_id = (
            proposal.rating_event.event_id
            if proposal.kind == "rating_event"
            else proposal.progress_event.event_id
        )
        with _connect(self._database_path) as connection:
            connection.execute(
                """
                INSERT INTO capture_proposals (id, idempotency_key, event_id, payload)
                VALUES (?, ?, ?, ?)
                """,
                (
                    proposal.id,
                    proposal.idempotency_key,
                    event_id,
                    json.dumps(
                        proposal.model_dump(mode="json"),
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                ),
            )

    def list_capture_proposals(self) -> list[TypedEventProposal]:
        with _connect(self._database_path) as connection:
            rows = connection.execute(
                "SELECT payload FROM capture_proposals ORDER BY id"
            ).fetchall()
        return [
            parse_typed_event_proposal(json.loads(row["payload"]))
            for row in rows
        ]

    def get_capture_proposal(self, proposal_id: str) -> TypedEventProposal:
        with _connect(self._database_path) as connection:
            row = connection.execute(
                "SELECT payload FROM capture_proposals WHERE id = ?", (proposal_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"no capture proposal exists with id {proposal_id!r}")
        return parse_typed_event_proposal(json.loads(row["payload"]))

    def review_capture_proposal(
        self,
        proposal_id: str,
        review_state: ReviewState,
    ) -> TypedEventProposal:
        """Atomically record one terminal review decision before promotion."""
        if review_state is ReviewState.NEEDS_REVIEW:
            raise ValueError("typed proposal review must be accepted or rejected")
        with _connect(self._database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload FROM capture_proposals WHERE id = ?", (proposal_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"no capture proposal exists with id {proposal_id!r}")
            proposal = parse_typed_event_proposal(json.loads(row["payload"]))
            if proposal.promoted_event_id is not None:
                raise ValueError("promoted typed proposal review outcome is immutable")
            if proposal.review_state is not ReviewState.NEEDS_REVIEW:
                if proposal.review_state is review_state:
                    return proposal
                raise ValueError("typed proposal review outcome is immutable")
            proposal.review_state = review_state
            connection.execute(
                "UPDATE capture_proposals SET payload = ? WHERE id = ?",
                (
                    json.dumps(
                        proposal.model_dump(mode="json"),
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                    proposal.id,
                ),
            )
            return proposal

    def promote_capture_proposal(
        self,
        proposal_id: str,
    ) -> tuple[TypedEventProposal, MediaItem]:
        """Atomically append one accepted typed event and record its event identity."""
        with _connect(self._database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            proposal_row = connection.execute(
                "SELECT payload FROM capture_proposals WHERE id = ?", (proposal_id,)
            ).fetchone()
            if proposal_row is None:
                raise KeyError(f"no capture proposal exists with id {proposal_id!r}")
            proposal = parse_typed_event_proposal(json.loads(proposal_row["payload"]))
            item = self._get_media_from_connection(connection, proposal.target_media_item_id)
            if proposal.promoted_event_id is not None:
                return proposal, item
            if proposal.review_state is not ReviewState.ACCEPTED:
                raise ValueError("only accepted typed proposals can be promoted")

            if proposal.kind == "rating_event":
                event = proposal.rating_event
                if item.rating_history and event.rated_on < item.rating_history[-1].rated_on:
                    raise ValueError("rating event predates existing rating history")
                rating = Rating(
                    score=event.score,
                    rated_on=event.rated_on,
                    provisional=event.provisional,
                )
                item.rating_history.append(rating)
                item.rating = rating
            else:
                event = proposal.progress_event
                if item.progress_records and event.recorded_on < item.progress_records[-1].recorded_on:
                    raise ValueError("progress event predates existing progress history")
                progress = ProgressRecord(
                    status=event.status,
                    amount_completed=event.amount_completed,
                    unit=event.unit,
                    recorded_on=event.recorded_on,
                    started_on=event.started_on,
                    ended_on=event.ended_on,
                    return_intent=event.return_intent,
                    reason=event.reason,
                )
                item.progress_records.append(progress)
                item.status = progress.status

            proposal.promoted_event_id = event.event_id
            connection.execute(
                "UPDATE media_items SET payload = ? WHERE id = ?",
                (
                    json.dumps(
                        item.model_dump(mode="json"),
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                    item.id,
                ),
            )
            connection.execute(
                "UPDATE capture_proposals SET payload = ? WHERE id = ?",
                (
                    json.dumps(
                        proposal.model_dump(mode="json"),
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                    proposal.id,
                ),
            )
            return proposal, item

    def auto_promote_capture_proposal(
        self,
        proposal_id: str,
    ) -> tuple[TypedEventProposal, MediaItem]:
        """Accept and append one eligible typed proposal in one transaction."""
        with _connect(self._database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            proposal_row = connection.execute(
                "SELECT payload FROM capture_proposals WHERE id = ?", (proposal_id,)
            ).fetchone()
            if proposal_row is None:
                raise KeyError(f"no capture proposal exists with id {proposal_id!r}")
            proposal = parse_typed_event_proposal(json.loads(proposal_row["payload"]))
            item = self._get_media_from_connection(connection, proposal.target_media_item_id)
            if proposal.promoted_event_id is not None:
                return proposal, item
            if proposal.review_state is not ReviewState.NEEDS_REVIEW:
                raise ValueError("only pending typed proposals can be auto-promoted")
            proposal.review_state = ReviewState.ACCEPTED

            if proposal.kind == "rating_event":
                event = proposal.rating_event
                if item.rating_history and event.rated_on < item.rating_history[-1].rated_on:
                    raise ValueError("rating event predates existing rating history")
                rating = Rating(
                    score=event.score,
                    rated_on=event.rated_on,
                    provisional=event.provisional,
                )
                item.rating_history.append(rating)
                item.rating = rating
            else:
                event = proposal.progress_event
                if item.progress_records and event.recorded_on < item.progress_records[-1].recorded_on:
                    raise ValueError("progress event predates existing progress history")
                progress = ProgressRecord(
                    status=event.status,
                    amount_completed=event.amount_completed,
                    unit=event.unit,
                    recorded_on=event.recorded_on,
                    started_on=event.started_on,
                    ended_on=event.ended_on,
                    return_intent=event.return_intent,
                    reason=event.reason,
                )
                item.progress_records.append(progress)
                item.status = progress.status

            proposal.promoted_event_id = event.event_id
            connection.execute(
                "UPDATE media_items SET payload = ? WHERE id = ?",
                (
                    json.dumps(
                        item.model_dump(mode="json"),
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                    item.id,
                ),
            )
            connection.execute(
                "UPDATE capture_proposals SET payload = ? WHERE id = ?",
                (
                    json.dumps(
                        proposal.model_dump(mode="json"),
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                    proposal.id,
                ),
            )
            return proposal, item

    def list_recommendations(self) -> list[RecommendationRecord]:
        with _connect(self._database_path) as connection:
            rows = connection.execute(
                "SELECT payload FROM recommendations ORDER BY id"
            ).fetchall()
        return [RecommendationRecord.model_validate_json(row["payload"]) for row in rows]

    def insert_recommendation_guarded(
        self,
        recommendation: RecommendationRecord,
        validate: Callable[
            [list[MediaItem], list[Creator], list[Proposal], list[RecommendationRecord]], None
        ],
    ) -> tuple[RecommendationRecord, bool]:
        """Insert one immutable recommendation occurrence under a reserved transaction."""
        with _connect(self._database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            items, creators, proposals, recommendations = self._load_document_from_connection(
                connection
            )
            existing = next(
                (record for record in recommendations if record.id == recommendation.id),
                None,
            )
            if existing is not None:
                existing_occurrence = RecommendationRecord.model_validate(
                    {
                        **existing.model_dump(mode="json"),
                        "outcomes": [],
                    }
                )
                if existing_occurrence != recommendation:
                    raise RecommendationIdentityConflictError(
                        f"recommendation id conflict: {recommendation.id!r}"
                    )
                return existing, False
            validate(items, creators, proposals, [*recommendations, recommendation])
            connection.execute(
                "INSERT INTO recommendations (id, payload) VALUES (?, ?)",
                (
                    recommendation.id,
                    json.dumps(
                        recommendation.model_dump(mode="json"),
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                ),
            )
            return recommendation, True

    def append_recommendation_outcome_guarded(
        self,
        recommendation_id: str,
        outcome: RecommendationOutcomeEvent,
    ) -> tuple[RecommendationRecord, bool]:
        """Append one immutable outcome event under a reserved transaction."""
        with _connect(self._database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload FROM recommendations WHERE id = ?",
                (recommendation_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"no recommendation exists with id {recommendation_id!r}")
            recommendation = RecommendationRecord.model_validate_json(row["payload"])
            existing = next(
                (event for event in recommendation.outcomes if event.id == outcome.id),
                None,
            )
            if existing is not None:
                if existing != outcome:
                    raise RecommendationOutcomeIdentityConflictError(
                        f"recommendation outcome id conflict: {outcome.id!r}"
                    )
                return recommendation, False
            updated = RecommendationRecord.model_validate(
                {
                    **recommendation.model_dump(mode="json"),
                    "outcomes": [
                        *[
                            event.model_dump(mode="json")
                            for event in recommendation.outcomes
                        ],
                        outcome.model_dump(mode="json"),
                    ],
                }
            )
            connection.execute(
                "UPDATE recommendations SET payload = ? WHERE id = ?",
                (
                    json.dumps(
                        updated.model_dump(mode="json"),
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                    recommendation_id,
                ),
            )
            return updated, True

    def load_document(
        self,
    ) -> tuple[list[MediaItem], list[Creator], list[Proposal], list[RecommendationRecord]]:
        """Read every application-owned collection from one SQLite snapshot."""
        with _connect(self._database_path) as connection:
            connection.execute("BEGIN")
            return self._load_document_from_connection(connection)

    def load_document_with_capture_proposals(
        self,
    ) -> tuple[
        list[MediaItem],
        list[Creator],
        list[Proposal],
        list[RecommendationRecord],
        list[TypedEventProposal],
    ]:
        """Read the complete current export collections from one SQLite snapshot."""
        with _connect(self._database_path) as connection:
            connection.execute("BEGIN")
            return self._load_document_with_capture_proposals_from_connection(connection)

    @staticmethod
    def _load_document_from_connection(
        connection: sqlite3.Connection,
    ) -> tuple[list[MediaItem], list[Creator], list[Proposal], list[RecommendationRecord]]:
        item_rows = connection.execute("SELECT payload FROM media_items ORDER BY id").fetchall()
        creator_rows = connection.execute("SELECT payload FROM creators ORDER BY id").fetchall()
        proposal_rows = connection.execute("SELECT payload FROM proposals ORDER BY id").fetchall()
        recommendation_rows = connection.execute(
            "SELECT payload FROM recommendations ORDER BY id"
        ).fetchall()
        return (
            [MediaItem.model_validate_json(row["payload"]) for row in item_rows],
            [Creator.model_validate_json(row["payload"]) for row in creator_rows],
            [Proposal.model_validate_json(row["payload"]) for row in proposal_rows],
            [
                RecommendationRecord.model_validate_json(row["payload"])
                for row in recommendation_rows
            ],
        )

    def _load_document_with_capture_proposals_from_connection(
        self,
        connection: sqlite3.Connection,
    ) -> tuple[
        list[MediaItem],
        list[Creator],
        list[Proposal],
        list[RecommendationRecord],
        list[TypedEventProposal],
    ]:
        items, creators, proposals, recommendations = self._load_document_from_connection(
            connection
        )
        capture_rows = connection.execute(
            "SELECT payload FROM capture_proposals ORDER BY id"
        ).fetchall()
        return (
            items,
            creators,
            proposals,
            recommendations,
            [
                parse_typed_event_proposal(json.loads(row["payload"]))
                for row in capture_rows
            ],
        )

    def save_media_guarded(
        self,
        item: MediaItem,
        validate: Callable[[MediaItem | None, list[RecommendationRecord]], None],
    ) -> None:
        """Validate recommendation references and save one item in one transaction."""
        payload = json.dumps(
            item.model_dump(mode="json"), separators=(",", ":"), sort_keys=True
        )
        with _connect(self._database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            current_row = connection.execute(
                "SELECT payload FROM media_items WHERE id = ?", (item.id,)
            ).fetchone()
            recommendation_rows = connection.execute(
                "SELECT payload FROM recommendations ORDER BY id"
            ).fetchall()
            validate(
                MediaItem.model_validate_json(current_row["payload"])
                if current_row
                else None,
                [
                    RecommendationRecord.model_validate_json(row["payload"])
                    for row in recommendation_rows
                ],
            )
            connection.execute(
                """
                INSERT INTO media_items (id, payload) VALUES (?, ?)
                ON CONFLICT(id) DO UPDATE SET payload = excluded.payload
                """,
                (item.id, payload),
            )

    def delete_media_guarded(
        self,
        item_id: str,
        validate: Callable[[MediaItem, list[RecommendationRecord]], None],
    ) -> None:
        """Validate recommendation references and delete one item in one transaction."""
        with _connect(self._database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            current_row = connection.execute(
                "SELECT payload FROM media_items WHERE id = ?", (item_id,)
            ).fetchone()
            if current_row is None:
                raise KeyError(f"no media item exists with id {item_id!r}")
            recommendation_rows = connection.execute(
                "SELECT payload FROM recommendations ORDER BY id"
            ).fetchall()
            validate(
                MediaItem.model_validate_json(current_row["payload"]),
                [
                    RecommendationRecord.model_validate_json(row["payload"])
                    for row in recommendation_rows
                ],
            )
            connection.execute("DELETE FROM media_items WHERE id = ?", (item_id,))

    def set_archived_on_guarded(
        self,
        item_id: str,
        archived_on: date | None,
        validate: Callable[[MediaItem, MediaItem, list[RecommendationRecord]], None],
    ) -> MediaItem:
        """Change only archive state from a fresh row under one reserved write transaction."""
        with _connect(self._database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = self._get_media_from_connection(connection, item_id)
            recommendation_rows = connection.execute(
                "SELECT payload FROM recommendations ORDER BY id"
            ).fetchall()
            recommendations = [
                RecommendationRecord.model_validate_json(row["payload"])
                for row in recommendation_rows
            ]
            updated = current.model_copy(update={"archived_on": archived_on})
            validate(current, updated, recommendations)
            connection.execute(
                "UPDATE media_items SET payload = ? WHERE id = ?",
                (
                    json.dumps(
                        updated.model_dump(mode="json"),
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                    item_id,
                ),
            )
            return updated

    def insert_proposal(self, proposal: Proposal) -> None:
        """Insert a new proposal without allowing an existing identity to be overwritten."""
        with _connect(self._database_path) as connection:
            connection.execute(
                "INSERT INTO proposals (id, payload) VALUES (?, ?)",
                (
                    proposal.id,
                    json.dumps(
                        proposal.model_dump(mode="json"), separators=(",", ":"), sort_keys=True
                    ),
                ),
            )

    def save_proposal(self, proposal: Proposal) -> None:
        with _connect(self._database_path) as connection:
            connection.execute(
                """
                INSERT INTO proposals (id, payload) VALUES (?, ?)
                ON CONFLICT(id) DO UPDATE SET payload = excluded.payload
                """,
                (
                    proposal.id,
                    json.dumps(
                        proposal.model_dump(mode="json"), separators=(",", ":"), sort_keys=True
                    ),
                ),
            )

    def review_proposal(self, proposal_id: str, review_state: ReviewState) -> Proposal:
        """Atomically review an unpromoted proposal without racing promotion."""
        with _connect(self._database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload FROM proposals WHERE id = ?", (proposal_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"no proposal exists with id {proposal_id!r}")
            proposal = Proposal.model_validate_json(row["payload"])
            if proposal.promoted_observation_id is not None or proposal.promoted_media_item_id is not None:
                raise ValueError("promoted proposal review outcome is immutable")
            proposal.review_state = review_state
            connection.execute(
                "UPDATE proposals SET payload = ? WHERE id = ?",
                (
                    json.dumps(
                        proposal.model_dump(mode="json"), separators=(",", ":"), sort_keys=True
                    ),
                    proposal.id,
                ),
            )
            return proposal

    def promote_observation_proposal(self, proposal_id: str) -> tuple[Proposal, MediaItem]:
        """Atomically append one accepted proposal and remember its canonical identity."""
        with _connect(self._database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            proposal_row = connection.execute(
                "SELECT payload FROM proposals WHERE id = ?", (proposal_id,)
            ).fetchone()
            if proposal_row is None:
                raise KeyError(f"no proposal exists with id {proposal_id!r}")
            proposal = Proposal.model_validate_json(proposal_row["payload"])
            item_row = connection.execute(
                "SELECT payload FROM media_items WHERE id = ?",
                (proposal.target_media_item_id,),
            ).fetchone()
            if item_row is None:
                raise KeyError(
                    f"no media item exists with id {proposal.target_media_item_id!r}"
                )
            item = MediaItem.model_validate_json(item_row["payload"])

            if proposal.promoted_observation_id is not None:
                return proposal, item
            if proposal.kind is not ProposalKind.OBSERVATION or proposal.proposed_observation is None:
                raise ValueError("only observation proposals can be promoted")
            if proposal.review_state is not ReviewState.ACCEPTED:
                raise ValueError("only accepted proposals can be promoted")

            existing_ids = {observation.id for observation in item.observations}
            base_id = proposal.proposed_observation.id
            observation_id = base_id
            suffix = 2
            while observation_id in existing_ids:
                observation_id = f"{base_id}-{suffix}"
                suffix += 1
            observation = proposal.proposed_observation.model_copy(
                update={"id": observation_id, "review_state": ReviewState.ACCEPTED}
            )
            item.observations.append(observation)
            proposal.promoted_observation_id = observation_id
            connection.execute(
                "UPDATE media_items SET payload = ? WHERE id = ?",
                (
                    json.dumps(
                        item.model_dump(mode="json"), separators=(",", ":"), sort_keys=True
                    ),
                    item.id,
                ),
            )
            connection.execute(
                "UPDATE proposals SET payload = ? WHERE id = ?",
                (
                    json.dumps(
                        proposal.model_dump(mode="json"), separators=(",", ":"), sort_keys=True
                    ),
                    proposal.id,
                ),
            )
            return proposal, item

    def promote_media_proposal(self, proposal_id: str) -> tuple[Proposal, MediaItem]:
        """Atomically create one accepted media candidate and retain its canonical identity."""
        with _connect(self._database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            proposal_row = connection.execute(
                "SELECT payload FROM proposals WHERE id = ?", (proposal_id,)
            ).fetchone()
            if proposal_row is None:
                raise KeyError(f"no proposal exists with id {proposal_id!r}")
            proposal = Proposal.model_validate_json(proposal_row["payload"])
            if proposal.kind is not ProposalKind.MEDIA_ITEM or proposal.proposed_media_item is None:
                raise ValueError("only media proposals can be promoted")
            if proposal.review_state is not ReviewState.ACCEPTED:
                raise ValueError("only accepted proposals can be promoted")
            candidate = proposal.proposed_media_item
            if proposal.promoted_media_item_id is not None:
                item = self._get_media_from_connection(connection, proposal.promoted_media_item_id)
                return proposal, item
            existing = connection.execute(
                "SELECT 1 FROM media_items WHERE id = ?", (candidate.id,)
            ).fetchone()
            if existing is not None:
                raise ValueError(f"media item id already exists: {candidate.id!r}")
            connection.execute(
                "INSERT INTO media_items (id, payload) VALUES (?, ?)",
                (
                    candidate.id,
                    json.dumps(candidate.model_dump(mode="json"), separators=(",", ":"), sort_keys=True),
                ),
            )
            proposal.promoted_media_item_id = candidate.id
            connection.execute(
                "UPDATE proposals SET payload = ? WHERE id = ?",
                (
                    json.dumps(proposal.model_dump(mode="json"), separators=(",", ":"), sort_keys=True),
                    proposal.id,
                ),
            )
            return proposal, candidate

    def auto_promote_observation_proposal(self, proposal_id: str) -> tuple[Proposal, MediaItem]:
        """Accept and append one eligible observation proposal atomically."""
        with _connect(self._database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            proposal_row = connection.execute(
                "SELECT payload FROM proposals WHERE id = ?", (proposal_id,)
            ).fetchone()
            if proposal_row is None:
                raise KeyError(f"no proposal exists with id {proposal_id!r}")
            proposal = Proposal.model_validate_json(proposal_row["payload"])
            item_row = connection.execute(
                "SELECT payload FROM media_items WHERE id = ?",
                (proposal.target_media_item_id,),
            ).fetchone()
            if item_row is None:
                raise KeyError(
                    f"no media item exists with id {proposal.target_media_item_id!r}"
                )
            item = MediaItem.model_validate_json(item_row["payload"])
            if proposal.promoted_observation_id is not None:
                return proposal, item
            if proposal.kind is not ProposalKind.OBSERVATION or proposal.proposed_observation is None:
                raise ValueError("only observation proposals can be auto-promoted")
            if proposal.review_state is not ReviewState.NEEDS_REVIEW:
                raise ValueError("only pending observation proposals can be auto-promoted")
            proposal.review_state = ReviewState.ACCEPTED

            existing_ids = {observation.id for observation in item.observations}
            base_id = proposal.proposed_observation.id
            observation_id = base_id
            suffix = 2
            while observation_id in existing_ids:
                observation_id = f"{base_id}-{suffix}"
                suffix += 1
            observation = proposal.proposed_observation.model_copy(
                update={"id": observation_id, "review_state": ReviewState.ACCEPTED}
            )
            item.observations.append(observation)
            proposal.promoted_observation_id = observation_id
            connection.execute(
                "UPDATE media_items SET payload = ? WHERE id = ?",
                (
                    json.dumps(
                        item.model_dump(mode="json"),
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                    item.id,
                ),
            )
            connection.execute(
                "UPDATE proposals SET payload = ? WHERE id = ?",
                (
                    json.dumps(
                        proposal.model_dump(mode="json"),
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                    proposal.id,
                ),
            )
            return proposal, item

    def auto_promote_media_proposal(self, proposal_id: str) -> tuple[Proposal, MediaItem]:
        """Accept and create one eligible media candidate atomically."""
        with _connect(self._database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            proposal_row = connection.execute(
                "SELECT payload FROM proposals WHERE id = ?", (proposal_id,)
            ).fetchone()
            if proposal_row is None:
                raise KeyError(f"no proposal exists with id {proposal_id!r}")
            proposal = Proposal.model_validate_json(proposal_row["payload"])
            if proposal.promoted_media_item_id is not None:
                item = self._get_media_from_connection(connection, proposal.promoted_media_item_id)
                return proposal, item
            if proposal.kind is not ProposalKind.MEDIA_ITEM or proposal.proposed_media_item is None:
                raise ValueError("only media proposals can be auto-promoted")
            if proposal.review_state is not ReviewState.NEEDS_REVIEW:
                raise ValueError("only pending media proposals can be auto-promoted")
            candidate = proposal.proposed_media_item
            existing = connection.execute(
                "SELECT 1 FROM media_items WHERE id = ?", (candidate.id,)
            ).fetchone()
            if existing is not None:
                raise ValueError(f"media item id already exists: {candidate.id!r}")
            proposal.review_state = ReviewState.ACCEPTED
            connection.execute(
                "INSERT INTO media_items (id, payload) VALUES (?, ?)",
                (
                    candidate.id,
                    json.dumps(candidate.model_dump(mode="json"), separators=(",", ":"), sort_keys=True),
                ),
            )
            proposal.promoted_media_item_id = candidate.id
            connection.execute(
                "UPDATE proposals SET payload = ? WHERE id = ?",
                (
                    json.dumps(proposal.model_dump(mode="json"), separators=(",", ":"), sort_keys=True),
                    proposal.id,
                ),
            )
            return proposal, candidate

    def get_proposal(self, proposal_id: str) -> Proposal:
        with _connect(self._database_path) as connection:
            row = connection.execute(
                "SELECT payload FROM proposals WHERE id = ?", (proposal_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"no proposal exists with id {proposal_id!r}")
        return Proposal.model_validate_json(row["payload"])

    def get_creator(self, creator_id: str) -> Creator:
        with _connect(self._database_path) as connection:
            row = connection.execute(
                "SELECT payload FROM creators WHERE id = ?", (creator_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"no creator exists with id {creator_id!r}")
        return Creator.model_validate_json(row["payload"])

    def get(self, item_id: str) -> MediaItem:
        with _connect(self._database_path) as connection:
            return self._get_media_from_connection(connection, item_id)

    @staticmethod
    def _get_media_from_connection(
        connection: sqlite3.Connection, item_id: str
    ) -> MediaItem:
        row = connection.execute(
            "SELECT payload FROM media_items WHERE id = ?", (item_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"no media item exists with id {item_id!r}")
        return MediaItem.model_validate_json(row["payload"])

    def delete(self, item_id: str) -> None:
        with _connect(self._database_path) as connection:
            cursor = connection.execute("DELETE FROM media_items WHERE id = ?", (item_id,))
        if cursor.rowcount != 1:
            raise KeyError(f"no media item exists with id {item_id!r}")

    def replace_document(
        self,
        items: list[MediaItem],
        creators: list[Creator],
        proposals: list[Proposal],
        recommendations: list[RecommendationRecord],
        capture_proposals: list[TypedEventProposal] | None = None,
        verify: Callable[
            [
                list[MediaItem],
                list[Creator],
                list[Proposal],
                list[RecommendationRecord],
                list[TypedEventProposal],
            ],
            None,
        ] | None = None,
    ) -> None:
        """Replace and optionally verify every application collection in one transaction."""
        capture_proposal_payloads = [
            (
                proposal.id,
                proposal.idempotency_key,
                proposal.rating_event.event_id
                if proposal.kind == "rating_event"
                else proposal.progress_event.event_id,
                json.dumps(
                    proposal.model_dump(mode="json"),
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            )
            for proposal in capture_proposals or []
        ]
        creator_payloads = [
            (
                creator.id,
                json.dumps(
                    creator.model_dump(mode="json"), separators=(",", ":"), sort_keys=True
                ),
            )
            for creator in creators
        ]
        item_payloads = [
            (
                item.id,
                json.dumps(
                    item.model_dump(mode="json"), separators=(",", ":"), sort_keys=True
                ),
            )
            for item in items
        ]
        proposal_payloads = [
            (
                proposal.id,
                json.dumps(
                    proposal.model_dump(mode="json"), separators=(",", ":"), sort_keys=True
                ),
            )
            for proposal in proposals
        ]
        recommendation_payloads = [
            (
                recommendation.id,
                json.dumps(
                    recommendation.model_dump(mode="json"),
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            )
            for recommendation in recommendations
        ]
        with _connect(self._database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM media_items")
            connection.execute("DELETE FROM creators")
            connection.execute("DELETE FROM proposals")
            connection.execute("DELETE FROM recommendations")
            connection.execute("DELETE FROM capture_proposals")
            connection.executemany("INSERT INTO creators (id, payload) VALUES (?, ?)", creator_payloads)
            connection.executemany("INSERT INTO media_items (id, payload) VALUES (?, ?)", item_payloads)
            connection.executemany("INSERT INTO proposals (id, payload) VALUES (?, ?)", proposal_payloads)
            connection.executemany(
                "INSERT INTO recommendations (id, payload) VALUES (?, ?)",
                recommendation_payloads,
            )
            connection.executemany(
                """
                INSERT INTO capture_proposals (id, idempotency_key, event_id, payload)
                VALUES (?, ?, ?, ?)
                """,
                capture_proposal_payloads,
            )
            if verify is not None:
                verify(
                    *self._load_document_with_capture_proposals_from_connection(connection)
                )


@contextmanager
def _connect(database_path: Path) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(database_path, timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA busy_timeout = 30000")
        try:
            connection.execute("PRAGMA journal_mode = WAL")
        except sqlite3.OperationalError as error:
            if "database is locked" not in str(error).casefold():
                raise
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()
