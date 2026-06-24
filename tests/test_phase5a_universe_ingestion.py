import json

import pytest

from data.universe import (
    build_universe_from_sec_map,
    classify_security,
    coverage_freshness,
    deterministic_sample,
    normalize_ticker_list,
    read_ticker_file,
    run_tracked_batch,
    universe_tickers,
)
from database.connection import get_connection
from database.repositories import (
    get_ingestion_run_items,
    get_sec_ticker_map_rows,
    list_security_universe,
    security_universe_status,
    upsert_daily_prices,
    upsert_security,
    upsert_security_universe,
)
from database.schema import initialize_database
from fundamentals.repository import upsert_filing, upsert_fundamental_facts


class FakeSECMapClient:
    def get_company_tickers(self):
        return {
            "0": {"ticker": "AAA", "cik_str": 1, "title": "Alpha Manufacturing Inc."},
            "1": {"ticker": "BBB", "cik_str": 2, "title": "Beta Bank Corp."},
            "2": {"ticker": "ETF", "cik_str": 3, "title": "Example ETF Trust"},
        }


def test_universe_schema_migration_and_update_idempotency(tmp_path):
    db = tmp_path / "u.db"
    initialize_database(db)
    with get_connection(db) as connection:
        tables = {row["name"] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"security_universe", "ingestion_runs", "ingestion_run_items"}.issubset(tables)

    first = build_universe_from_sec_map(client=FakeSECMapClient(), database_path=db)
    second = build_universe_from_sec_map(client=FakeSECMapClient(), database_path=db)

    assert first.rows_upserted == 3
    assert second.rows_upserted == 3
    assert security_universe_status(database_path=db)["total_securities"] == 3
    assert len(get_sec_ticker_map_rows(database_path=db)) == 3


@pytest.mark.parametrize(
    "title, expected_reason",
    [
        ("Example ETF Trust", "etf"),
        ("Preferred Income Corp Preferred", "preferred"),
        ("Warrant Acquisition Corp Warrant", "warrant"),
        ("Rights Offering Corp Rights", "right"),
        ("Units Holdings Units", "unit"),
        ("Apartment REIT Inc", "reit"),
        ("Regional Bank Corp", "financial"),
        ("OTC Company", "unsupported_exchange"),
        ("Global Depositary ADR", "adr"),
    ],
)
def test_security_type_exclusions_are_explicit(title, expected_reason):
    exchange = "OTC" if expected_reason == "unsupported_exchange" else "NASDAQ"
    row = classify_security({"ticker": "TEST", "cik": "0000000001", "title": title, "exchange": exchange})
    assert row["eligibility_status"] == "excluded"
    assert expected_reason in row["eligibility_reasons"]


def test_common_stock_identification_invalid_and_duplicate_tickers(tmp_path):
    row = classify_security({"ticker": "abc", "cik": "0000000001", "title": "Industrial Co", "exchange": "NYSE"})
    assert row["eligibility_status"] == "eligible"
    assert row["is_common_stock"] is True

    tickers, invalid = normalize_ticker_list(["aapl", "AAPL", "BRK-B", "BAD!"])
    assert tickers == ["AAPL", "BRK.B"]
    assert invalid == ["BAD!"]

    path = tmp_path / "tickers.csv"
    path.write_text("aapl,AAPL\nmsft\n", encoding="utf-8")
    assert read_ticker_file(str(path))[0] == ["AAPL", "MSFT"]


def test_deterministic_sample_of_100_and_clear_shortfall():
    tickers = [f"T{i:03d}" for i in range(150)]
    first = deterministic_sample(tickers, 100, seed=42)
    second = deterministic_sample(tickers, 100, seed=42)
    assert len(first) == 100
    assert len(set(first)) == 100
    assert first == second

    with pytest.raises(ValueError, match="requested 100 tickers"):
        deterministic_sample(tickers[:20], 100, seed=42)


def test_universe_status_limit_offset_and_eligible_order(temp_database):
    rows = [
        classify_security({"ticker": "AAA", "cik": "1", "title": "Alpha Co", "exchange": "NASDAQ"}),
        classify_security({"ticker": "BBB", "cik": "2", "title": "Beta ETF", "exchange": "NASDAQ"}),
        classify_security({"ticker": "CCC", "cik": "3", "title": "Gamma Co", "exchange": "NYSE"}),
    ]
    upsert_security_universe(rows, database_path=temp_database)

    status = security_universe_status(database_path=temp_database)
    assert status["total_securities"] == 3
    assert status["eligible_graham_securities"] == 2
    assert status["excluded_by_security_type"] >= 1
    assert universe_tickers(limit=1, offset=1, database_path=temp_database) == ["CCC"]
    assert [row["normalized_ticker"] for row in list_security_universe(eligible_only=True, database_path=temp_database)] == ["AAA", "CCC"]


def test_batch_tracking_success_failure_retry_and_resume(temp_database):
    attempts = {"BAD": 0}

    def worker(ticker):
        if ticker == "BAD":
            attempts["BAD"] += 1
            if attempts["BAD"] == 1:
                raise TimeoutError("temporary timeout")
            return {"ticker": ticker, "status": "updated", "rows_stored": 1}
        if ticker == "MISS":
            return {"ticker": ticker, "status": "failed", "error": "missing CIK"}
        return {"ticker": ticker, "status": "updated", "rows_stored": 1}

    result = run_tracked_batch("prices", ["AAA", "BAD", "MISS"], worker, {"token": "redacted"}, max_retries=1, database_path=temp_database)
    assert result["succeeded_count"] == 2
    assert result["failed_count"] == 1
    assert attempts["BAD"] == 2
    items = get_ingestion_run_items(result["run_id"], database_path=temp_database)
    assert len(items) == 3
    assert all("secret" not in json.dumps(item).lower() for item in items)

    resumed = run_tracked_batch("prices", [], lambda ticker: {"ticker": ticker, "status": "updated", "rows_stored": 1}, {}, resume_run=result["run_id"], database_path=temp_database)
    assert resumed["requested_count"] == 1


def test_source_tables_preserve_idempotent_price_and_fact_rows(temp_database):
    upsert_security("AAA", database_path=temp_database)
    price = {"ticker": "AAA", "trade_date": "2024-01-02", "open": 1, "high": 2, "low": 1, "close": 2, "adjusted_close": 2, "volume": 10, "downloaded_at": "now"}
    assert upsert_daily_prices([price], database_path=temp_database) == 1
    assert upsert_daily_prices([price], database_path=temp_database) == 1
    with get_connection(temp_database) as connection:
        assert connection.execute("SELECT COUNT(*) AS c FROM daily_prices").fetchone()["c"] == 1

    filing = {
        "ticker": "AAA",
        "cik": "0000000001",
        "accession_number": "0000000001-24-000001",
        "form_type": "10-K",
        "filing_date": "2024-03-01",
        "accepted_at": "2024-03-01T12:00:00",
        "report_period": "2023-12-31",
        "fiscal_year": 2023,
        "fiscal_period": "FY",
        "is_amendment": 0,
        "source_url": "https://sec.test",
        "downloaded_at": "now",
    }
    filing_id = upsert_filing(filing, database_path=temp_database)
    fact = {
        "filing_id": filing_id,
        "ticker": "AAA",
        "taxonomy": "us-gaap",
        "concept": "DebtCurrentAndNoncurrent",
        "standardized_field": "total_debt",
        "unit": "USD",
        "value": 10.0,
        "period_start": None,
        "period_end": "2023-12-31",
        "frame": None,
        "form_type": "10-K",
        "filed_date": "2024-03-01",
        "accepted_at": "2024-03-01T12:00:00",
        "fiscal_year": 2023,
        "fiscal_period": "FY",
        "accession_number": "0000000001-24-000001",
        "source_name": "test",
        "downloaded_at": "now",
    }
    assert upsert_fundamental_facts([fact], database_path=temp_database) == 1
    assert upsert_fundamental_facts([fact], database_path=temp_database) == 1
    with get_connection(temp_database) as connection:
        assert connection.execute("SELECT COUNT(*) AS c FROM fundamental_facts").fetchone()["c"] == 1


def test_freshness_report_flags_missing_and_present_data(temp_database):
    upsert_security("AAA", database_path=temp_database)
    upsert_daily_prices([{"ticker": "AAA", "trade_date": "2025-05-30", "open": 1, "high": 2, "low": 1, "close": 2, "adjusted_close": 2, "volume": 10, "downloaded_at": "now"}], database_path=temp_database)

    freshness = coverage_freshness(["AAA", "BBB"], "2025-06-01", database_path=temp_database)
    assert freshness["AAA"]["latest_price_date"] == "2025-05-30"
    assert freshness["AAA"]["price_stale"] is True
    assert freshness["BBB"]["latest_price_date"] is None
    assert freshness["BBB"]["fundamentals_stale"] is True
