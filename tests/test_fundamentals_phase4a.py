import math

import pytest
import requests

from data.sec_client import SECClient, SECConfigurationError, SECJSONError, SECRequestError
from data.sec_ticker_map import (
    CIKMappingError,
    cache_ticker_map,
    get_cik_for_ticker,
    load_sec_ticker_map,
    normalize_cik,
    normalize_ticker,
)
from database.connection import get_connection
from database.repositories import count_price_rows, upsert_daily_prices, upsert_security
from database.schema import initialize_database
from fundamentals.concepts import standardized_field_for_concept
from fundamentals.normalization import classify_period, validate_numeric
from fundamentals.repository import (
    count_fundamental_facts,
    count_fundamental_filings,
    get_facts_for_filing,
    get_filings_for_ticker,
    upsert_filing,
    upsert_fundamental_facts,
)
from fundamentals.service import (
    get_fundamental_history,
    get_fundamentals_as_of,
    get_latest_known_filing,
    update_fundamentals_for_ticker,
    update_fundamentals_universe,
)
from main import parse_args


class FakeResponse:
    def __init__(self, status_code=200, payload=None, json_error=False):
        self.status_code = status_code
        self.payload = payload or {}
        self.json_error = json_error

    def json(self):
        if self.json_error:
            raise ValueError("bad json")
        return self.payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, headers=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "timeout": timeout})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class FakeSECSource:
    def __init__(self, ticker_payload=None, submissions=None, facts=None):
        self.ticker_payload = ticker_payload or {"0": {"ticker": "AAPL", "cik_str": 320193, "title": "Apple Inc."}}
        self.submissions = submissions if submissions is not None else sample_submissions()
        self.facts = facts if facts is not None else sample_company_facts()

    def get_company_tickers(self):
        return self.ticker_payload

    def get_submissions(self, cik):
        return self.submissions

    def get_company_facts(self, cik):
        return self.facts


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "fundamentals.db"
    initialize_database(path)
    return path


def sample_submissions():
    return {
        "name": "Apple Inc.",
        "filings": {
            "recent": {
                "accessionNumber": [
                    "0000320193-24-000001",
                    "0000320193-24-000002",
                    "0000320193-24-000003",
                    "0000320193-24-000004",
                ],
                "form": ["10-K", "10-K/A", "10-Q", "8-K"],
                "filingDate": ["2024-03-01", "2024-04-15", "2024-07-30", "2024-08-01"],
                "acceptanceDateTime": ["2024-03-01T10:00:00", "2024-04-15T10:00:00", "2024-07-30T10:00:00", "2024-08-01T10:00:00"],
                "reportDate": ["2023-12-31", "2023-12-31", "2024-06-30", "2024-06-30"],
                "fy": [2023, 2023, 2024, 2024],
                "fp": ["FY", "FY", "Q2", "Q2"],
            }
        },
    }


def sample_company_facts():
    return {
        "facts": {
            "us-gaap": {
                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                    "units": {"USD": [
                        {"accn": "0000320193-24-000001", "fy": 2023, "fp": "FY", "form": "10-K", "filed": "2024-03-01", "start": "2023-01-01", "end": "2023-12-31", "val": 1000.0},
                        {"accn": "0000320193-24-000002", "fy": 2023, "fp": "FY", "form": "10-K/A", "filed": "2024-04-15", "start": "2023-01-01", "end": "2023-12-31", "val": 1100.0},
                    ]}
                },
                "NetIncomeLoss": {
                    "units": {"USD": [
                        {"accn": "0000320193-24-000001", "fy": 2023, "fp": "FY", "form": "10-K", "filed": "2024-03-01", "start": "2023-01-01", "end": "2023-12-31", "val": -50.0},
                        {"accn": "0000320193-24-000003", "fy": 2024, "fp": "Q2", "form": "10-Q", "filed": "2024-07-30", "start": "2024-04-01", "end": "2024-06-30", "val": 75.0},
                    ]}
                },
                "EarningsPerShareDiluted": {
                    "units": {"USD/shares": [
                        {"accn": "0000320193-24-000001", "fy": 2023, "fp": "FY", "form": "10-K", "filed": "2024-03-01", "start": "2023-01-01", "end": "2023-12-31", "val": 2.5}
                    ]}
                },
                "Assets": {
                    "units": {"USD": [
                        {"accn": "0000320193-24-000001", "fy": 2023, "fp": "FY", "form": "10-K", "filed": "2024-03-01", "end": "2023-12-31", "val": 5000.0}
                    ]}
                },
                "UnsupportedConcept": {
                    "units": {"USD": [
                        {"accn": "0000320193-24-000001", "fy": 2023, "fp": "FY", "form": "10-K", "filed": "2024-03-01", "start": "2023-01-01", "end": "2023-12-31", "val": 1.0}
                    ]}
                },
            }
        }
    }


def test_cik_and_ticker_normalization():
    assert normalize_cik(320193) == "0000320193"
    assert normalize_ticker(" brk-b ") == "BRK.B"
    with pytest.raises(ValueError):
        normalize_cik("12345678901")


def test_ticker_mapping_and_missing_mapping(db_path):
    cache_ticker_map([{"ticker": "AAPL", "cik": "0000320193", "title": "Apple Inc."}], database_path=db_path)
    assert get_cik_for_ticker("aapl", database_path=db_path, refresh=False) == "0000320193"
    with pytest.raises(CIKMappingError):
        get_cik_for_ticker("MISSING", database_path=db_path, refresh=False)


def test_load_sec_ticker_map_caches_rows(db_path):
    rows = load_sec_ticker_map(client=FakeSECSource(), database_path=db_path)
    assert rows[0]["ticker"] == "AAPL"
    assert get_cik_for_ticker("AAPL", database_path=db_path, refresh=False) == "0000320193"


def test_sec_user_agent_required():
    with pytest.raises(SECConfigurationError):
        SECClient(user_agent="")


def test_sec_headers_and_retry_behavior(monkeypatch):
    monkeypatch.setattr("data.sec_client.time.sleep", lambda seconds: None)
    session = FakeSession([FakeResponse(429), FakeResponse(500), FakeResponse(200, {"ok": True})])
    client = SECClient(user_agent="AlgoTradProject/0.1 tests@example.com", session=session, request_delay_seconds=0)
    assert client.get_json("https://example.test") == {"ok": True}
    assert session.calls[-1]["headers"]["User-Agent"] == "AlgoTradProject/0.1 tests@example.com"
    assert len(session.calls) == 3


def test_sec_permanent_404_and_malformed_json(monkeypatch):
    monkeypatch.setattr("data.sec_client.time.sleep", lambda seconds: None)
    with pytest.raises(SECRequestError):
        SECClient(user_agent="AlgoTradProject/0.1 tests@example.com", session=FakeSession([FakeResponse(404)]), request_delay_seconds=0).get_json("x")
    with pytest.raises(SECJSONError):
        SECClient(user_agent="AlgoTradProject/0.1 tests@example.com", session=FakeSession([FakeResponse(200, json_error=True)]), request_delay_seconds=0).get_json("x")


def test_schema_migration_fresh_existing_and_idempotent(tmp_path):
    db = tmp_path / "migration.db"
    initialize_database(db)
    upsert_security("AAPL", database_path=db)
    upsert_daily_prices([{"ticker": "AAPL", "trade_date": "2024-01-02", "open": 1, "high": 2, "low": 1, "close": 2, "adjusted_close": 2, "volume": 10, "downloaded_at": "now"}], database_path=db)
    before = count_price_rows(database_path=db)
    initialize_database(db)
    initialize_database(db)
    with get_connection(db) as connection:
        tables = {row["name"] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"fundamental_filings", "fundamental_facts", "sec_ticker_map"}.issubset(tables)
    assert count_price_rows(database_path=db) == before


def filing_row(accession="0000320193-24-000001", form="10-K", accepted_at="2024-03-01T10:00:00", amendment=0):
    return {
        "ticker": "AAPL",
        "cik": "0000320193",
        "accession_number": accession,
        "form_type": form,
        "filing_date": accepted_at[:10],
        "accepted_at": accepted_at,
        "report_period": "2023-12-31",
        "fiscal_year": 2023,
        "fiscal_period": "FY",
        "is_amendment": amendment,
        "source_url": "https://sec.test",
        "downloaded_at": "2026-06-24T00:00:00+00:00",
    }


def fact_row(filing_id, value=1000.0, concept="RevenueFromContractWithCustomerExcludingAssessedTax", field="revenue", accepted_at="2024-03-01T10:00:00"):
    return {
        "filing_id": filing_id,
        "ticker": "AAPL",
        "taxonomy": "us-gaap",
        "concept": concept,
        "standardized_field": field,
        "unit": "USD",
        "value": value,
        "period_start": "2023-01-01",
        "period_end": "2023-12-31",
        "frame": None,
        "form_type": "10-K",
        "filed_date": accepted_at[:10],
        "accepted_at": accepted_at,
        "fiscal_year": 2023,
        "fiscal_period": "FY",
        "accession_number": "0000320193-24-000001",
        "source_name": "test",
        "downloaded_at": "2026-06-24T00:00:00+00:00",
    }


def test_filing_and_fact_upsert_duplicate_prevention(db_path):
    upsert_security("AAPL", database_path=db_path)
    filing_id = upsert_filing(filing_row(), database_path=db_path)
    assert filing_id == upsert_filing(filing_row(), database_path=db_path)
    assert count_fundamental_filings("AAPL", database_path=db_path) == 1
    assert upsert_fundamental_facts([fact_row(filing_id)], database_path=db_path) == 1
    assert upsert_fundamental_facts([fact_row(filing_id)], database_path=db_path) == 1
    assert count_fundamental_facts("AAPL", database_path=db_path) == 1
    assert len(get_facts_for_filing(filing_id, database_path=db_path)) == 1


def test_concept_mappings_and_period_classification():
    assert standardized_field_for_concept("RevenueFromContractWithCustomerExcludingAssessedTax") == "revenue"
    assert standardized_field_for_concept("NetIncomeLoss") == "net_income"
    assert standardized_field_for_concept("EarningsPerShareDiluted") == "diluted_eps"
    assert standardized_field_for_concept("Assets") == "total_assets"
    assert standardized_field_for_concept("NotSupported") is None
    assert classify_period("2023-01-01", "2023-12-31") == "annual"
    assert classify_period("2024-04-01", "2024-06-30") == "quarter"
    assert classify_period("2024-01-01", "2024-06-30") == "year_to_date"
    assert classify_period(None, "2024-06-30") == "instant"


def test_validation_rejects_nan_infinity_and_malformed_period():
    with pytest.raises(ValueError):
        validate_numeric(math.nan)
    with pytest.raises(ValueError):
        validate_numeric(math.inf)
    with pytest.raises(ValueError):
        classify_period("2024-06-30", "2024-01-01")


def test_service_update_and_point_in_time_queries(db_path):
    summary = update_fundamentals_for_ticker("AAPL", client=FakeSECSource(), database_path=db_path)
    assert summary["status"] == "updated"
    assert summary["filings_stored"] == 3
    assert summary["facts_stored"] >= 5
    before_amendment = get_fundamentals_as_of("AAPL", "2024-03-20T00:00:00", database_path=db_path)
    after_amendment = get_fundamentals_as_of("AAPL", "2024-05-01T00:00:00", database_path=db_path)
    before_later_q = get_fundamentals_as_of("AAPL", "2024-06-01T00:00:00", database_path=db_path)
    assert before_amendment["fields"]["revenue"]["value"] == 1000.0
    assert after_amendment["fields"]["revenue"]["value"] == 1100.0
    assert after_amendment["fields"]["revenue"]["is_amendment"] is True
    assert "net_income" in before_later_q["fields"]
    assert before_later_q["fields"]["net_income"]["value"] == -50.0
    assert before_later_q["fields"]["net_income"]["accession_number"] != "0000320193-24-000003"
    assert before_amendment["fields"]["revenue"]["accession_number"] == "0000320193-24-000001"


def test_filing_date_fallback_latest_filing_and_history(db_path):
    upsert_security("AAPL", database_path=db_path)
    filing = filing_row(accepted_at="2024-03-01T10:00:00")
    filing["accepted_at"] = None
    filing_id = upsert_filing(filing, database_path=db_path)
    fact = fact_row(filing_id)
    fact["accepted_at"] = None
    upsert_fundamental_facts([fact], database_path=db_path)
    result = get_fundamentals_as_of("AAPL", "2024-03-02", database_path=db_path)
    assert result["fields"]["revenue"]["accepted_at_fallback_used"] is True
    assert get_latest_known_filing("AAPL", "2024-03-02", database_path=db_path)["accession_number"] == filing["accession_number"]
    assert len(get_fundamental_history("AAPL", "revenue", as_of_date="2024-03-02", database_path=db_path)) == 1
    assert len(get_filings_for_ticker("AAPL", form_types=["10-K"], database_path=db_path)) == 1


def test_universe_update_continues_after_unmapped_and_empty_responses(db_path):
    client = FakeSECSource(ticker_payload={"0": {"ticker": "AAPL", "cik_str": 320193, "title": "Apple Inc."}})
    summary = update_fundamentals_universe(["AAPL", "MSFT"], client=client, database_path=db_path)
    assert summary["updated"] == 1
    assert summary["unmapped"] == 1
    empty = update_fundamentals_for_ticker("AAPL", client=FakeSECSource(submissions={}, facts={}), database_path=db_path)
    assert empty["status"] == "no_supported_filings"


def test_unsupported_forms_negative_values_and_repeated_update(db_path):
    client = FakeSECSource()
    first = update_fundamentals_for_ticker("AAPL", client=client, database_path=db_path)
    counts = (count_fundamental_filings("AAPL", database_path=db_path), count_fundamental_facts("AAPL", database_path=db_path))
    second = update_fundamentals_for_ticker("AAPL", client=client, database_path=db_path)
    assert second["status"] == "updated"
    assert (count_fundamental_filings("AAPL", database_path=db_path), count_fundamental_facts("AAPL", database_path=db_path)) == counts
    filings = get_filings_for_ticker("AAPL", database_path=db_path)
    assert all(row["form_type"] != "8-K" for row in filings)
    assert get_fundamentals_as_of("AAPL", "2024-03-20T00:00:00", database_path=db_path)["fields"]["net_income"]["value"] == -50.0


def test_fundamentals_update_does_not_modify_price_or_backtest_tables(db_path):
    upsert_security("AAPL", database_path=db_path)
    upsert_daily_prices([{"ticker": "AAPL", "trade_date": "2024-01-02", "open": 1, "high": 2, "low": 1, "close": 2, "adjusted_close": 2, "volume": 10, "downloaded_at": "now"}], database_path=db_path)
    with get_connection(db_path) as connection:
        connection.execute("INSERT INTO backtest_runs (strategy, start_date, end_date, starting_capital, ending_capital, parameters_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)", ("s", "2024-01-01", "2024-01-02", 1, 1, "{}", "now"))
        before_backtests = connection.execute("SELECT COUNT(*) AS c FROM backtest_runs").fetchone()["c"]
    before_prices = count_price_rows(database_path=db_path)
    update_fundamentals_for_ticker("AAPL", client=FakeSECSource(), database_path=db_path)
    with get_connection(db_path) as connection:
        after_backtests = connection.execute("SELECT COUNT(*) AS c FROM backtest_runs").fetchone()["c"]
    assert count_price_rows(database_path=db_path) == before_prices
    assert after_backtests == before_backtests


def test_query_results_are_deterministic_and_cli_parsing(db_path):
    update_fundamentals_for_ticker("AAPL", client=FakeSECSource(), database_path=db_path)
    first = get_fundamentals_as_of("AAPL", "2024-05-01T00:00:00", database_path=db_path)
    second = get_fundamentals_as_of("AAPL", "2024-05-01T00:00:00", database_path=db_path)
    assert first == second
    args = parse_args(["show-fundamentals", "AAPL", "--as-of", "2025-06-01"])
    assert args.command == "show-fundamentals"
    assert parse_args(["update-fundamentals", "--tickers", "AAPL", "MSFT", "--years", "5"]).years == 5
