import pytest

import database.connection as connection_module
from data.strategy_data import (
    get_available_tickers,
    get_next_trading_day,
    get_price_on_date,
    get_ticker_history,
    get_trading_dates,
)
from database.repositories import upsert_daily_prices, upsert_security
from database.schema import initialize_database


@pytest.fixture
def temp_database(tmp_path, monkeypatch):
    db_path = tmp_path / "strategy_data.db"
    initialize_database(db_path)
    monkeypatch.setattr(connection_module, "DATABASE_PATH", db_path)
    return db_path


def make_row(ticker, trade_date, close):
    return {
        "ticker": ticker,
        "trade_date": trade_date,
        "open": close,
        "high": close + 1.0,
        "low": close - 1.0,
        "close": close,
        "adjusted_close": close,
        "volume": 1000,
        "downloaded_at": "2026-06-24T00:00:00+00:00",
    }


def seed_prices():
    upsert_security("AAPL")
    upsert_security("MSFT")
    upsert_daily_prices(
        [
            make_row("AAPL", "2024-01-02", 100.0),
            make_row("AAPL", "2024-01-03", 101.0),
            make_row("AAPL", "2024-01-05", 102.0),
            make_row("MSFT", "2024-01-03", 200.0),
        ]
    )


def test_get_available_tickers_and_history(temp_database):
    seed_prices()

    assert get_available_tickers() == ["AAPL", "MSFT"]
    rows = get_ticker_history("AAPL", start_date="2024-01-03", end_date="2024-01-05")
    assert [row["trade_date"] for row in rows] == ["2024-01-03", "2024-01-05"]


def test_next_trading_day_lookup(temp_database):
    seed_prices()

    assert get_next_trading_day("AAPL", "2024-01-03") == "2024-01-05"
    assert get_next_trading_day("AAPL", "2024-01-05") is None


def test_get_trading_dates(temp_database):
    seed_prices()

    assert get_trading_dates(["AAPL", "MSFT"], start_date="2024-01-03") == ["2024-01-03", "2024-01-05"]


def test_missing_ticker_behavior(temp_database):
    seed_prices()

    assert get_ticker_history("MISSING") == []
    assert get_price_on_date("MISSING", "2024-01-02") is None
    assert get_next_trading_day("MISSING", "2024-01-02") is None
