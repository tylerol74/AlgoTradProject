import json
from datetime import date

import pytest

import database.connection as connection_module
from data import market_data
from data.sec_ticker_map import cache_ticker_map
from data.universe import build_universe_from_sec_map
from database.repositories import count_price_rows, get_active_common_stock_tickers, upsert_daily_prices, upsert_security
from database.schema import initialize_database
from main import main, parse_args


@pytest.fixture
def temp_database(tmp_path, monkeypatch):
    db_path = tmp_path / "integration.db"
    initialize_database(db_path)
    monkeypatch.setattr(connection_module, "DATABASE_PATH", db_path)
    return db_path


def _cache_eligible_map(db_path, count=120):
    rows = [
        {"ticker": f"T{i:03d}", "cik": f"{i:010d}", "title": f"Test Company {i} Common Stock"}
        for i in range(count)
    ]
    rows.extend(
        [
            {"ticker": "BADW", "cik": "9999999991", "title": "Bad Warrant"},
            {"ticker": "BADU", "cik": "9999999992", "title": "Bad Unit"},
            {"ticker": "BANK", "cik": "9999999993", "title": "Bank Financial Corp"},
        ]
    )
    cache_ticker_map(rows, database_path=db_path)


def test_phase5a_and_phase4c_commands_are_registered():
    commands = {
        "update-universe": [],
        "universe-status": [],
        "list-universe": [],
        "sample-universe": [],
        "update-universe-prices": ["--universe", "all-eligible"],
        "update-universe-fundamentals": ["--universe", "all-eligible"],
        "refresh-fundamentals-normalization": ["--universe", "all-eligible"],
        "universe-coverage-report": ["--universe", "all-eligible"],
        "screen-combined": ["--as-of", "2025-06-01"],
        "run-combined-backtest": ["--start-date", "2025-01-01", "--end-date", "2025-02-01"],
        "compare-strategies": ["--start-date", "2025-01-01", "--end-date", "2025-02-01"],
    }
    for command, args in commands.items():
        assert parse_args([command] + args).command == command


def test_universe_resolves_100_eligible_and_excludes_security_types(temp_database):
    _cache_eligible_map(temp_database, count=120)
    result = build_universe_from_sec_map(database_path=temp_database)
    tickers = get_active_common_stock_tickers(limit=100, database_path=temp_database)
    assert result.eligible_count >= 100
    assert len(tickers) == 100
    assert len(set(tickers)) == 100
    assert "BADW" not in tickers
    assert "BADU" not in tickers
    assert "BANK" not in tickers


def test_combined_screen_universe_limit_offset_and_100_rows(temp_database, capsys):
    _cache_eligible_map(temp_database, count=120)
    build_universe_from_sec_map(database_path=temp_database)
    main(["screen-combined", "--universe", "all-eligible", "--limit", "100", "--as-of", "2025-06-01", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["rows"]) == 100
    assert payload["coverage_summary"]["requested_tickers"] == 100
    main(["screen-combined", "--universe", "all-eligible", "--limit", "5", "--offset", "10", "--as-of", "2025-06-01", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["rows"]) == 5


def test_combined_screen_accepts_ticker_file_and_is_deterministic(temp_database, tmp_path, capsys):
    ticker_file = tmp_path / "tickers.txt"
    ticker_file.write_text("AAA\nAAA\nBBB\n", encoding="utf-8")
    main(["screen-combined", "--ticker-file", str(ticker_file), "--as-of", "2025-06-01", "--json"])
    first = capsys.readouterr().out
    main(["screen-combined", "--ticker-file", str(ticker_file), "--as-of", "2025-06-01", "--json"])
    second = capsys.readouterr().out
    assert first == second
    assert len(json.loads(first)["rows"]) == 2


def test_market_data_current_guard_preserved(temp_database, monkeypatch):
    upsert_security("AAPL")
    upsert_daily_prices(
        [
            {
                "ticker": "AAPL",
                "trade_date": date.today().isoformat(),
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0,
                "adjusted_close": 100.0,
                "volume": 1000,
                "downloaded_at": "2026-06-24T00:00:00+00:00",
            }
        ]
    )
    monkeypatch.setattr(market_data.yf, "download", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("no yfinance")))
    assert market_data.update_ticker_prices("AAPL")["status"] == "already_current"


def test_screening_does_not_mutate_prices(temp_database, tmp_path, capsys):
    ticker_file = tmp_path / "tickers.txt"
    ticker_file.write_text("AAPL\n", encoding="utf-8")
    upsert_security("AAPL")
    before = count_price_rows("AAPL")
    main(["screen-combined", "--ticker-file", str(ticker_file), "--as-of", "2025-06-01", "--json"])
    capsys.readouterr()
    assert count_price_rows("AAPL") == before
