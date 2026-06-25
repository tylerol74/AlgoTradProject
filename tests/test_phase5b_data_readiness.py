import csv
import json

import pytest

from data.readiness import (
    FUNDAMENTALS_MISSING,
    PRICE_HISTORY_INSUFFICIENT,
    PRICE_MISSING,
    READY,
    REQUIRED_GRAHAM_FIELDS_MISSING,
    UNRESOLVED_TICKER,
    build_readiness_report,
    export_readiness_report,
    normalize_requested_symbols,
    prepare_universe_data,
    read_input_symbols,
    reconcile_readiness,
)
from database.connection import get_connection
from main import main, parse_args


def _security(db_path, ticker, eligible=True, security_type="Common Stock"):
    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO securities
            (ticker, company_name, exchange, security_type, is_active, updated_at)
            VALUES (?, ?, 'NYSE', ?, 1, '2025-01-01T00:00:00Z')
            """,
            (ticker, ticker + " Corp", security_type),
        )
        conn.execute(
            """
            INSERT INTO security_universe (
                ticker, normalized_ticker, company_name, cik, exchange, security_type,
                is_active, is_common_stock, eligibility_status, eligibility_reasons
            ) VALUES (?, ?, ?, ?, 'NYSE', ?, 1, ?, ?, ?)
            """,
            (
                ticker,
                ticker,
                ticker + " Corp",
                "1",
                security_type,
                1 if eligible else 0,
                "eligible" if eligible else "excluded",
                "" if eligible else "preferred",
            ),
        )


def _prices(db_path, ticker, start_year=2019, end_year=2025, rows=30):
    with get_connection(db_path) as conn:
        for index in range(rows):
            year = start_year if index == 0 else end_year
            month = 1 if index == 0 else 5
            day = min(index + 1, 28)
            trade_date = f"{year}-{month:02d}-{day:02d}"
            conn.execute(
                """
                INSERT OR REPLACE INTO daily_prices
                (ticker, trade_date, open, high, low, close, adjusted_close, volume, downloaded_at)
                VALUES (?, ?, 10, 11, 9, 10, 10, 1000000, '2025-06-01T00:00:00Z')
                """,
                (ticker, trade_date),
            )


def _fundamentals(db_path, ticker, fields=None):
    fields = fields or [
        "diluted_eps",
        "shares_outstanding",
        "current_assets",
        "current_liabilities",
        "shareholders_equity",
        "total_debt",
    ]
    with get_connection(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO fundamental_filings
            (ticker, cik, accession_number, form_type, filing_date, accepted_at, report_period, is_amendment, downloaded_at)
            VALUES (?, '1', ?, '10-K', '2025-02-01', '2025-02-01T00:00:00Z', '2024-12-31', 0, '2025-02-01T00:00:00Z')
            """,
            (ticker, ticker + "-ACC"),
        )
        filing_id = cursor.lastrowid
        for field in fields:
            conn.execute(
                """
                INSERT INTO fundamental_facts
                (filing_id, ticker, taxonomy, concept, standardized_field, unit, value, period_end, form_type, filed_date, accepted_at, accession_number, source_name, downloaded_at)
                VALUES (?, ?, 'us-gaap', ?, ?, 'USD', 1, '2024-12-31', '10-K', '2025-02-01', '2025-02-01T00:00:00Z', ?, 'test', '2025-02-01T00:00:00Z')
                """,
                (filing_id, ticker, field, field, ticker + "-ACC"),
            )


def test_readiness_classification_complete_data(temp_database):
    _security(temp_database, "AAA")
    _prices(temp_database, "AAA")
    _fundamentals(temp_database, "AAA")
    report = build_readiness_report(["AAA"], "2025-06-01", database_path=str(temp_database))
    row = report["rows"][0]
    assert row["final_readiness_category"] == READY
    assert row["price_ready"]
    assert row["graham_evaluable"]
    assert row["technical_evaluable"]
    assert report["reconciliation"]["invariant_holds"]


def test_readiness_classification_missing_prices(temp_database):
    _security(temp_database, "AAA")
    _fundamentals(temp_database, "AAA")
    row = build_readiness_report(["AAA"], "2025-06-01", database_path=str(temp_database))["rows"][0]
    assert row["final_readiness_category"] == PRICE_MISSING


def test_readiness_classification_insufficient_price_history(temp_database):
    _security(temp_database, "AAA")
    _prices(temp_database, "AAA", start_year=2025, rows=25)
    _fundamentals(temp_database, "AAA")
    row = build_readiness_report(["AAA"], "2025-06-01", database_path=str(temp_database))["rows"][0]
    assert row["final_readiness_category"] == PRICE_HISTORY_INSUFFICIENT


def test_readiness_classification_missing_filings(temp_database):
    _security(temp_database, "AAA")
    _prices(temp_database, "AAA")
    row = build_readiness_report(["AAA"], "2025-06-01", database_path=str(temp_database))["rows"][0]
    assert row["final_readiness_category"] == FUNDAMENTALS_MISSING


def test_readiness_classification_missing_required_fields(temp_database):
    _security(temp_database, "AAA")
    _prices(temp_database, "AAA")
    _fundamentals(temp_database, "AAA", fields=["diluted_eps"])
    row = build_readiness_report(["AAA"], "2025-06-01", database_path=str(temp_database))["rows"][0]
    assert row["final_readiness_category"] == REQUIRED_GRAHAM_FIELDS_MISSING
    assert "shareholders_equity" in row["graham_evaluability_reason"]


def test_readiness_classification_unsupported_and_unresolved(temp_database):
    _security(temp_database, "PREF", eligible=False, security_type="Preferred")
    rows = build_readiness_report(["PREF", "MISSING"], "2025-06-01", database_path=str(temp_database))["rows"]
    categories = {row["normalized_symbol"]: row["final_readiness_category"] for row in rows}
    assert categories["PREF"] == "UNSUPPORTED_SECURITY"
    assert categories["MISSING"] == UNRESOLVED_TICKER


def test_reconciliation_invariant_and_no_silent_drop(temp_database):
    _security(temp_database, "AAA")
    _prices(temp_database, "AAA")
    _fundamentals(temp_database, "AAA")
    report = build_readiness_report(["AAA", "MISSING"], "2025-06-01", database_path=str(temp_database))
    reconciliation = reconcile_readiness(report["rows"])
    assert reconciliation.requested_count == 2
    assert reconciliation.invariant_holds
    assert {row["normalized_symbol"] for row in report["rows"]} == {"AAA", "MISSING"}


def test_prepare_skips_completed_stages_on_rerun(temp_database, monkeypatch):
    _security(temp_database, "AAA")
    _prices(temp_database, "AAA")
    _fundamentals(temp_database, "AAA")
    calls = {"price": 0, "fundamentals": 0}

    def price_worker(*args):
        calls["price"] += 1
        return {"status": "updated"}

    def fundamental_worker(*args):
        calls["fundamentals"] += 1
        return {"status": "updated"}

    payload = prepare_universe_data(["AAA"], 6, 6, as_of="2025-06-01", resume=True, price_worker=price_worker, fundamental_worker=fundamental_worker, database_path=str(temp_database))
    assert payload["summary"]["already_complete_count"] == 1
    assert calls == {"price": 0, "fundamentals": 0}


def test_prepare_retries_failed_stage_without_losing_successful_ticker(temp_database, monkeypatch):
    _security(temp_database, "AAA")
    _security(temp_database, "BBB")
    _prices(temp_database, "AAA")
    _fundamentals(temp_database, "AAA")
    calls = []

    def price_worker(ticker, start, end):
        calls.append(ticker)
        if ticker == "BBB":
            raise RuntimeError("no data")
        return {"status": "updated"}

    payload = prepare_universe_data(["AAA", "BBB"], 6, 6, as_of="2025-06-01", resume=True, price_worker=price_worker, fundamental_worker=lambda ticker, years: {"status": "updated"}, database_path=str(temp_database))
    assert "BBB" in calls
    assert payload["summary"]["already_complete_count"] == 1
    assert payload["failures"][0]["ticker"] == "BBB"


def test_readiness_reporting_performs_no_network_calls(temp_database):
    _security(temp_database, "AAA")
    report = build_readiness_report(["AAA"], "2025-06-01", database_path=str(temp_database))
    assert report["rows"][0]["final_readiness_category"] == PRICE_MISSING


def test_deterministic_csv_and_json_ordering(temp_database, tmp_path):
    _security(temp_database, "BBB")
    _security(temp_database, "AAA")
    report = build_readiness_report(["BBB", "AAA"], "2025-06-01", database_path=str(temp_database))
    paths = export_readiness_report(report, str(tmp_path))
    assert [row["normalized_symbol"] for row in report["rows"]] == ["AAA", "BBB"]
    data = json.loads((tmp_path / "data-readiness-2025-06-01.json").read_text(encoding="utf-8"))
    assert data["rows"][0]["normalized_symbol"] == "AAA"
    with open(paths["csv"], newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["normalized_symbol"] == "AAA"


def test_duplicate_ticker_normalization_and_empty_file(tmp_path):
    assert normalize_requested_symbols(["aapl", "AAPL", "brk/b"]) == [("aapl", "AAPL"), ("brk/b", "BRK.B")]
    path = tmp_path / "tickers.txt"
    path.write_text("\n# comment\n\n", encoding="utf-8")
    assert read_input_symbols(str(path)) == []


def test_malformed_ticker_file_lines_are_explicitly_unresolved(temp_database, tmp_path):
    path = tmp_path / "tickers.txt"
    path.write_text("???\nAAA\n", encoding="utf-8")
    _security(temp_database, "AAA")
    rows = build_readiness_report(read_input_symbols(str(path)), "2025-06-01", database_path=str(temp_database))["rows"]
    assert {row["normalized_symbol"] for row in rows} == {"???", "AAA"}
    assert any(row["final_readiness_category"] == UNRESOLVED_TICKER for row in rows)


def test_phase5b_cli_commands_registered():
    assert parse_args(["data-readiness-report", "--ticker-file", "x", "--as-of", "2025-06-01"]).command == "data-readiness-report"
    assert parse_args(["prepare-universe-data", "--ticker-file", "x", "--resume"]).command == "prepare-universe-data"


def test_data_readiness_cli_uses_stored_data_only(temp_database, tmp_path, capsys):
    _security(temp_database, "AAA")
    ticker_file = tmp_path / "tickers.txt"
    ticker_file.write_text("AAA\n", encoding="utf-8")
    main(["data-readiness-report", "--ticker-file", str(ticker_file), "--as-of", "2025-06-01"])
    captured = capsys.readouterr().out
    assert "requested=1" in captured
