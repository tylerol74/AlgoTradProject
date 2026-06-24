import sqlite3

import pytest

from database.connection import get_connection
from database.schema import initialize_database


EXPECTED_TABLES = {
    "securities",
    "daily_prices",
    "fundamentals",
    "signals",
    "backtest_runs",
    "backtest_trades",
    "portfolio_snapshots",
}


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "test_algotrad.db"


@pytest.fixture
def initialized_db(db_path):
    initialize_database(db_path)
    return db_path


def insert_security(connection, ticker="AAPL"):
    connection.execute(
        """
        INSERT INTO securities (
            ticker, company_name, exchange, security_type, is_active, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (ticker, "Apple Inc.", "NASDAQ", "Common Stock", 1, "2026-06-24T00:00:00Z"),
    )


def insert_daily_price(connection, ticker="AAPL", close=200.0):
    connection.execute(
        """
        INSERT INTO daily_prices (
            ticker, trade_date, open, high, low, close, adjusted_close, volume, downloaded_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            ticker,
            "2026-06-23",
            198.0,
            202.0,
            197.5,
            close,
            close,
            1000000,
            "2026-06-24T00:00:00Z",
        ),
    )


def test_database_initializes(initialized_db):
    with get_connection(initialized_db) as connection:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = ?", ("table",)
        ).fetchall()

    table_names = {row["name"] for row in rows}
    assert EXPECTED_TABLES.issubset(table_names)


def test_duplicate_daily_price_rows_are_rejected(initialized_db):
    with get_connection(initialized_db) as connection:
        insert_security(connection)
        insert_daily_price(connection)
        with pytest.raises(sqlite3.IntegrityError):
            insert_daily_price(connection, close=201.0)


def test_daily_price_can_be_updated_safely_with_upsert(initialized_db):
    with get_connection(initialized_db) as connection:
        insert_security(connection)
        insert_daily_price(connection)
        connection.execute(
            """
            INSERT INTO daily_prices (
                ticker, trade_date, open, high, low, close, adjusted_close, volume, downloaded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker, trade_date) DO UPDATE SET
                open = excluded.open,
                high = excluded.high,
                low = excluded.low,
                close = excluded.close,
                adjusted_close = excluded.adjusted_close,
                volume = excluded.volume,
                downloaded_at = excluded.downloaded_at
            """,
            (
                "AAPL",
                "2026-06-23",
                199.0,
                203.0,
                198.5,
                201.0,
                201.0,
                1500000,
                "2026-06-24T01:00:00Z",
            ),
        )
        row = connection.execute(
            "SELECT close, volume FROM daily_prices WHERE ticker = ? AND trade_date = ?",
            ("AAPL", "2026-06-23"),
        ).fetchone()

    assert row["close"] == 201.0
    assert row["volume"] == 1500000


def test_records_can_be_inserted_and_retrieved(initialized_db):
    with get_connection(initialized_db) as connection:
        insert_security(connection, ticker="MSFT")
        insert_daily_price(connection, ticker="MSFT", close=450.0)
        row = connection.execute(
            """
            SELECT s.company_name, p.close
            FROM securities AS s
            JOIN daily_prices AS p ON p.ticker = s.ticker
            WHERE s.ticker = ? AND p.trade_date = ?
            """,
            ("MSFT", "2026-06-23"),
        ).fetchone()

    assert row["company_name"] == "Apple Inc."
    assert row["close"] == 450.0


def test_foreign_keys_are_enabled(initialized_db):
    with get_connection(initialized_db) as connection:
        foreign_keys_enabled = connection.execute("PRAGMA foreign_keys").fetchone()[0]
        assert foreign_keys_enabled == 1
        with pytest.raises(sqlite3.IntegrityError):
            insert_daily_price(connection, ticker="MISSING")
