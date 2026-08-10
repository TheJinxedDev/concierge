"""Read-only Hermes state adapter for completed-session capture.

The adapter is the only package-owned seam that knows how to open Hermes
state. It snapshots ended session rows and their active messages through an
injected database factory. The worker/source selector still owns eligibility,
ordering, watermark, and proposal safety; this module never reads an active
session and never writes Hermes state.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any


SessionDBFactory = Callable[..., Any]


class HermesStateSessionReader:
    """Expose one read-only completed-session snapshot to the source adapter."""

    def __init__(self, db_path: Path, *, db_factory: SessionDBFactory | None = None):
        path = Path(db_path).expanduser()
        if not path.is_absolute():
            raise ValueError("Hermes state database path must be absolute")
        self.db_path = path
        self._db_factory = db_factory or self._default_db_factory
        self._sessions: tuple[dict[str, object], ...] = ()
        self._messages: dict[str, tuple[dict[str, object], ...]] = {}
        self._loaded = False

    @staticmethod
    def _default_db_factory(path: Path, *, read_only: bool):
        from hermes_state import SessionDB

        return SessionDB(path, read_only=read_only)

    def list_sessions(self) -> Iterable[Mapping[str, object]]:
        """Snapshot ended rows only; active-session messages are never fetched."""

        rows: list[dict[str, object]] = []
        messages: dict[str, tuple[dict[str, object], ...]] = {}
        database = self._db_factory(self.db_path, read_only=True)
        try:
            offset = 0
            page_size = 500
            while True:
                page = database.list_sessions_rich(
                    limit=page_size,
                    offset=offset,
                    include_children=True,
                    project_compression_tips=False,
                    include_archived=True,
                    compact_rows=False,
                )
                page_rows = [dict(row) for row in page]
                for row in page_rows:
                    session_id = row.get("id")
                    if not isinstance(session_id, str) or not session_id.strip():
                        continue
                    # This is the hard active-session gate. Do not call
                    # get_messages for a row without a durable end timestamp.
                    if row.get("ended_at") is None:
                        continue
                    row["archived"] = bool(row.get("archived", False))
                    rows.append(row)
                    messages[session_id] = tuple(
                        dict(message)
                        for message in database.get_messages(
                            session_id,
                            include_inactive=False,
                        )
                    )
                if len(page_rows) < page_size:
                    break
                offset += len(page_rows)
        finally:
            database.close()

        self._sessions = tuple(rows)
        self._messages = messages
        self._loaded = True
        return tuple(dict(row) for row in self._sessions)

    def list_messages(self, session_id: str) -> Iterable[Mapping[str, object]]:
        """Return messages only from the last ended-session snapshot."""

        if not self._loaded:
            return ()
        return tuple(dict(row) for row in self._messages.get(session_id, ()))
