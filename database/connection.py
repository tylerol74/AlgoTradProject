"""SQLite connection helpers."""

import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional, Union

from config.settings import DATABASE_PATH

logger = logging.getLogger(__name__)
DatabasePath = Union[str, Path]


@contextmanager
def get_connection(database_path: Optional[DatabasePath] = None) -> Iterator[sqlite3.Connection]:
    """Yield a SQLite connection with foreign key enforcement enabled."""
    path = Path(database_path) if database_path is not None else DATABASE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(str(path))
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
