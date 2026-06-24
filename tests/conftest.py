import pytest

import database.connection as connection_module
from database.schema import initialize_database


@pytest.fixture
def temp_database(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    initialize_database(db_path)
    monkeypatch.setattr(connection_module, "DATABASE_PATH", db_path)
    return db_path
