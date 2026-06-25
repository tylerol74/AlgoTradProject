import json

from data.sec_ticker_map import cache_ticker_map
from data.universe import build_universe_from_sec_map, export_production_universe
from reporting.daily_opportunities import build_daily_opportunities


def test_export_eligible_universe_excludes_unsupported_types(temp_database, tmp_path):
    cache_ticker_map(
        [
            {"ticker": "AAA", "cik": "0000000001", "title": "AAA Common Stock"},
            {"ticker": "BBB", "cik": "0000000002", "title": "BBB Common Stock"},
            {"ticker": "BADW", "cik": "0000000003", "title": "Bad Warrant"},
            {"ticker": "BANK", "cik": "0000000004", "title": "Bank Financial Corp"},
        ],
        database_path=temp_database,
    )
    build_universe_from_sec_map(database_path=temp_database)
    output = tmp_path / "eligible.txt"

    payload = export_production_universe(str(output), database_path=temp_database)

    assert output.read_text(encoding="utf-8").splitlines() == ["AAA", "BBB"]
    assert payload["exported_tickers"] == 2
    assert payload["excluded_by_reason"]["warrant"] == 1
    assert payload["excluded_by_reason"]["financial"] == 1


def test_daily_report_caps_sections_and_keeps_watchlists_distinct():
    rows = [
        {"ticker": "AAA", "price": 10, "data_ready": True, "graham_score": 80, "graham_qualified": True, "primary_graham_failure": "", "panic_score": 12, "technical_qualified": True, "primary_technical_failure": "", "combined_score": 90, "combined_qualified": True, "primary_data_issue": "", "five_day_return": -0.2, "ten_day_return": -0.3, "relative_volume": 2.0, "rsi": 20, "margin_of_safety": 0.5},
        {"ticker": "BBB", "price": 11, "data_ready": True, "graham_score": 70, "graham_qualified": False, "primary_graham_failure": "graham_score_below_minimum", "panic_score": 8, "technical_qualified": False, "primary_technical_failure": "panic_score_below_minimum", "combined_score": 60, "combined_qualified": False, "primary_data_issue": "", "five_day_return": -0.1, "ten_day_return": -0.2, "relative_volume": 1.4, "rsi": 35, "margin_of_safety": 0.2},
        {"ticker": "CCC", "price": 12, "data_ready": True, "graham_score": 60, "graham_qualified": False, "primary_graham_failure": "margin_of_safety_below_minimum", "panic_score": 14, "technical_qualified": True, "primary_technical_failure": "", "combined_score": 55, "combined_qualified": False, "primary_data_issue": "", "five_day_return": -0.3, "ten_day_return": -0.4, "relative_volume": 3.0, "rsi": 18, "margin_of_safety": 0.1},
    ]
    metadata = {ticker: {"company_name": ticker, "exchange": "NYSE", "security_type": "Common Stock"} for ticker in ["AAA", "BBB", "CCC"]}
    readiness = {ticker: {"latest_price_date": "2026-06-24", "final_readiness_category": "READY", "graham_evaluability_reason": ""} for ticker in ["AAA", "BBB", "CCC"]}

    payload = build_daily_opportunities(rows, metadata, readiness, "2026-06-24", max_results=1)

    assert [row["ticker"] for row in payload["sections"]["COMBINED CANDIDATES"]] == ["AAA"]
    assert [row["ticker"] for row in payload["sections"]["GRAHAM WATCHLIST"]] == ["BBB"]
    assert [row["ticker"] for row in payload["sections"]["TECHNICAL WATCHLIST"]] == ["CCC"]
    assert json.loads(json.dumps(payload, sort_keys=True))
