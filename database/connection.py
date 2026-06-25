"""SQLite connection helpers."""

import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional, Union

from config.settings import DATABASE_PATH, resolve_database_path

logger = logging.getLogger(__name__)
DatabasePath = Union[str, Path]


@contextmanager
def get_connection(database_path: Optional[DatabasePath] = None) -> Iterator[sqlite3.Connection]:
    """Yield a SQLite connection with foreign key enforcement enabled."""
    path = resolve_database_path(database_path) if database_path is not None else Path(DATABASE_PATH).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)

    try:
        connection = sqlite3.connect(str(path))
    except sqlite3.Error as exc:
        raise RuntimeError(f"Could not open SQLite database at {path}: {exc}") from exc
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        enabled = connection.execute("PRAGMA foreign_keys").fetchone()[0]
        if enabled != 1:
            raise RuntimeError("SQLite foreign key enforcement could not be enabled")

        logger.debug("Opened SQLite connection at %s", path)
        yield connection
        connection.commit()
        logger.debug("Committed SQLite transaction at %s", path)
    except Exception:
        connection.rollback()
        logger.exception("Rolled back SQLite transaction at %s", path)
        raise
    finally:
        connection.close()
        logger.debug("Closed SQLite connection at %s", path)
