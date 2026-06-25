from datetime import date

import pandas as pd
import pytest

import database.connection as connection_module
from data import market_data
from data.market_data import (
    download_price_history,
    normalize_ticker,
    update_price_universe,
    update_ticker_prices,
)
from data.validation import validate_price_row
from database.repositories import count_price_rows, get_price_history, upsert_daily_prices, upsert_security
from database.schema import initialize_database
from main import parse_args


@pytest.fixture
def temp_database(tmp_path, monkeypatch):
    db_path = tmp_path / "market_data_test.db"
    initialize_database(db_path)
    monkeypatch.setattr(connection_module, "DATABASE_PATH", db_path)
    return db_path


def make_frame(close=101.0):
    return pd.DataFrame(
        {
            "Open": [100.0, 101.0],
            "High": [102.0, 103.0],
            "Low": [99.0, 100.0],
            "Close": [close, close + 1.0],
            "Adj Close": [close, close + 1.0],
            "Volume": [1000, 2000],
        },
        index=pd.to_datetime(["2024-01-02", "2024-01-03"]),
    )


def test_ticker_normalization():
    assert normalize_ticker(" aapl ") == "AAPL"
    assert normalize_ticker("") == ""
    assert normalize_ticker(None) == ""


def test_empty_yfinance_response(monkeypatch):
    monkeypatch.setattr(market_data.yf, "download", lambda *args, **kwargs: pd.DataFrame())

    rows = download_price_history("AAPL", "2024-01-01", "2024-01-03")

    assert rows == []


def test_normal_single_ticker_dataframe(monkeypatch):
    captured = {}

    def fake_download(ticker, **kwargs):
        captured.update(kwargs)
        return make_frame()

    monkeypatch.setattr(market_data.yf, "download", fake_download)

    rows = download_price_history("aapl", "2024-01-01", "2024-01-03")

    assert captured["end"] == "2024-01-04"
    assert len(rows) == 2
    assert rows[0]["ticker"] == "AAPL"
    assert rows[0]["trade_date"] == "2024-01-02"
    assert isinstance(rows[0]["close"], float)
    assert isinstance(rows[0]["volume"], int)


def test_multiindex_dataframe_handling(monkeypatch):
    columns = pd.MultiIndex.from_product(
        [["Open", "High", "Low", "Close", "Adj Close", "Volume"], ["AAPL"]]
    )
    frame = pd.DataFrame(
        [[100.0, 102.0, 99.0, 101.0, 101.0, 1000]],
        columns=columns,
        index=pd.to_datetime(["2024-01-02"]),
    )
    monkeypatch.setattr(market_data.yf, "download", lambda *args, **kwargs: frame)

    rows = download_price_history("AAPL", "2024-01-01", "2024-01-02")

    assert len(rows) == 1
    assert rows[0]["close"] == 101.0


def test_missing_ohlc_fields_are_rejected(monkeypatch):
    frame = pd.DataFrame(
        {"Open": [100.0], "High": [102.0], "Close": [101.0], "Volume": [1000]},
        index=pd.to_datetime(["2024-01-02"]),
    )
    monkeypatch.setattr(market_data.yf, "download", lambda *args, **kwargs: frame)

    assert download_price_history("AAPL", "2024-01-01") == []


def test_invalid_negative_prices_are_rejected():
    row = {
        "ticker": "AAPL",
        "trade_date": "2024-01-02",
        "open": -1.0,
        "high": 102.0,
        "low": 99.0,
        "close": 101.0,
        "volume": 1000,
    }

    is_valid, errors = validate_price_row(row)

    assert not is_valid
    assert "open must be positive" in errors


def test_high_less_than_low_is_rejected():
    row = {
        "ticker": "AAPL",
        "trade_date": "2024-01-02",
        "open": 100.0,
        "high": 98.0,
        "low": 99.0,
        "close": 100.0,
        "volume": 1000,
    }

    is_valid, errors = validate_price_row(row)

    assert not is_valid
    assert "high must be greater than or equal to low" in errors


def test_existing_history_downloads_after_latest_date(temp_database, monkeypatch):
    upsert_security("AAPL")
    upsert_daily_prices(
        [
            {
                "ticker": "AAPL",
                "trade_date": "2024-01-03",
                "open": 100.0,
                "high": 102.0,
                "low": 99.0,
                "close": 101.0,
                "adjusted_close": 101.0,
                "volume": 1000,
                "downloaded_at": "2026-06-24T00:00:00+00:00",
            }
        ]
    )
    captured = {}

    def fake_download(ticker, **kwargs):
        captured.update(kwargs)
        return pd.DataFrame()

    monkeypatch.setattr(market_data.yf, "download", fake_download)

    summary = update_ticker_prices("AAPL", start_date="2024-01-01", end_date="2024-01-05")

    assert summary["start_date"] == "2024-01-04"
    assert captured["start"] == "2024-01-04"


def test_current_database_without_end_date_is_already_current(temp_database, monkeypatch):
    upsert_security("AAPL")
    upsert_daily_prices(
        [
            {
                "ticker": "AAPL",
                "trade_date": date.today().isoformat(),
                "open": 100.0,
                "high": 102.0,
                "low": 99.0,
                "close": 101.0,
                "adjusted_close": 101.0,
                "volume": 1000,
                "downloaded_at": "2026-06-24T00:00:00+00:00",
            }
        ]
    )

    def fail_download(*args, **kwargs):
        raise AssertionError("yfinance should not be called when prices are current")

    monkeypatch.setattr(market_data.yf, "download", fail_download)

    summary = update_ticker_prices("AAPL")

    assert summary["status"] == "already_current"
    assert summary["rows_downloaded"] == 0
    assert summary["rows_stored"] == 0

def test_one_failed_ticker_does_not_stop_others(temp_database, monkeypatch):
    def fake_batch(tickers, start_date, end_date=None):
        return {
            ticker: (
                []
                if ticker == "BAD"
                else [
                    {
                        "ticker": ticker,
                        "trade_date": "2024-01-02",
                        "open": 10.0,
                        "high": 11.0,
                        "low": 9.0,
                        "close": 10.0,
                        "adjusted_close": 10.0,
                        "volume": 1000,
                        "downloaded_at": "now",
                    }
                ]
            )
            for ticker in tickers
        }

    monkeypatch.setattr(market_data, "download_price_history_batch", fake_batch)

    summary = update_price_universe(["AAPL", "BAD", "MSFT"], start_date="2024-01-01")

    assert summary["updated"] == 2
    assert summary["no_data"] == 1
    assert [item["ticker"] for item in summary["results"]] == ["AAPL", "BAD", "MSFT"]


def test_running_same_price_update_twice_does_not_duplicate_records(temp_database, monkeypatch):
    monkeypatch.setattr(market_data.yf, "download", lambda *args, **kwargs: make_frame())

    first = update_ticker_prices("AAPL", start_date="2024-01-01", end_date="2024-01-03")
    second = update_ticker_prices("AAPL", start_date="2024-01-01", end_date="2024-01-03")

    assert first["status"] == "updated"
    assert second["status"] == "already_current"
    assert count_price_rows("AAPL") == 2


def test_cli_argument_parsing_for_update_prices():
    args = parse_args(["update-prices", "--tickers", "AAPL", "MSFT", "--start-date", "2024-01-01"])

    assert args.command == "update-prices"
    assert args.tickers == ["AAPL", "MSFT"]
    assert args.start_date == "2024-01-01"


def test_price_history_retrieval_from_market_data_database(temp_database, monkeypatch):
    monkeypatch.setattr(market_data.yf, "download", lambda *args, **kwargs: make_frame())

    update_ticker_prices("AAPL", start_date="2024-01-01", end_date="2024-01-03")
    rows = get_price_history("AAPL", start_date="2024-01-03", end_date="2024-01-03")

    assert len(rows) == 1
    assert rows[0]["trade_date"] == "2024-01-03"

