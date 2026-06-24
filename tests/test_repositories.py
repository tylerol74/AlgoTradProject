import sqlite3

import pytest

from database.connection import get_connection
from database.repositories import (
    count_price_rows,
    get_database_status,
    get_latest_price_date,
    get_price_history,
    security_exists,
    upsert_daily_prices,
    upsert_security,
)
from database.schema import initialize_database


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "repo_test.db"
    initialize_database(path)
    return path


def price_row(ticker="AAPL", trade_date="2024-01-02", close=101.0):
    return {
        "ticker": ticker,
        "trade_date": trade_date,
        "open": 100.0,
        "high": 102.0,
        "low": 99.0,
        "close": close,
        "adjusted_close": close,
        "volume": 1000,
        "downloaded_at": "2026-06-24T00:00:00+00:00",
    }


def test_security_upsert_and_exists(db_path):
    upsert_security(" aapl ", company_name="Apple Inc.", database_path=db_path)

    assert security_exists("AAPL", database_path=db_path)
    assert not security_exists("MSFT", database_path=db_path)


def test_duplicate_price_rows_update_safely(db_path):
    upsert_security("AAPL", database_path=db_path)
    upsert_daily_prices([price_row(close=101.0)], database_path=db_path)
    upsert_daily_prices([price_row(close=105.0)], database_path=db_path)

    rows = get_price_history("AAPL", database_path=db_path)
    assert len(rows) == 1
    assert rows[0]["close"] == 105.0
    assert count_price_rows("AAPL", database_path=db_path) == 1


def test_price_history_retrieval_by_ticker_and_date_range(db_path):
    upsert_security("AAPL", database_path=db_path)
    upsert_daily_prices(
        [
            price_row(trade_date="2024-01-02"),
            price_row(trade_date="2024-01-03", close=102.0),
            price_row(trade_date="2024-01-04", close=103.0),
        ],
        database_path=db_path,
    )

    rows = get_price_history(
        "AAPL", start_date="2024-01-03", end_date="2024-01-04", database_path=db_path
    )

    assert [row["trade_date"] for row in rows] == ["2024-01-03", "2024-01-04"]
    assert get_latest_price_date("AAPL", database_path=db_path) == "2024-01-04"


def test_database_row_counts(db_path):
    upsert_security("AAPL", database_path=db_path)
    upsert_security("MSFT", database_path=db_path)
    upsert_daily_prices([price_row(), price_row(ticker="MSFT")], database_path=db_path)

    status = get_database_status(database_path=db_path)

    assert count_price_rows(database_path=db_path) == 2
    assert status["securities"] == 2
    assert status["daily_price_rows"] == 2
    assert status["rows_by_ticker"] == {"AAPL": 1, "MSFT": 1}


def test_foreign_key_enforcement_in_repository_database(db_path):
    with pytest.raises(sqlite3.IntegrityError):
        upsert_daily_prices([price_row(ticker="MISSING")], database_path=db_path)

    with get_connection(db_path) as connection:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
