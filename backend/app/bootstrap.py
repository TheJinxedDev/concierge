"""Bootstrap a local media library at the configured local data directory."""

import os
from pathlib import Path

from app.library_service import LibraryService
from app.persistence import MediaRepository, migrate
from app.setup_contract import resolve_data_directory


DATABASE_FILENAME = "taste-database.sqlite3"


def default_data_directory() -> Path:
    """Return a stable user-local data location without creating it."""
    return resolve_data_directory(os.environ, home=Path.home(), platform=os.name)


def open_default_library() -> LibraryService:
    """Open the library in the current user's conventional local data location."""
    return open_library(default_data_directory())


def open_library(data_directory: Path) -> LibraryService:
    """Create or migrate a local library and return its ready application service."""
    data_directory.mkdir(parents=True, exist_ok=True)
    database_path = data_directory / DATABASE_FILENAME
    migrate(database_path)
    return LibraryService(MediaRepository(database_path), data_directory / "backups")
