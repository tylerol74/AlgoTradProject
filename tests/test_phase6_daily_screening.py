import json
from argparse import Namespace
from pathlib import Path

from data.sec_ticker_map import cache_ticker_map
from data.universe import build_universe_from_sec_map, export_production_universe
from data.readiness import prepare_universe_data
from database.repositories import (
    get_provider_refresh_status,
    provider_refresh_is_fresh,
    upsert_daily_prices,
    upsert_provider_cooldown,
    upsert_provider_refresh_success,
    upsert_security,
)
from fundamentals.service import update_fundamentals_for_ticker
from reporting.daily_opportunities import build_daily_opportunities
from main import _database_maintenance_command, _run_daily_screen_command, parse_args


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
    readiness = {
        ticker: {
            "latest_price_date": "2026-06-24",
            "final_readiness_category": "READY",
            "graham_evaluability_reason": "",
            "graham_evaluable": True,
            "technical_evaluable": True,
            "combined_evaluable": True,
        }
        for ticker in ["AAA", "BBB", "CCC"]
    }

    payload = build_daily_opportunities(rows, metadata, readiness, "2026-06-24", max_results=1)

    assert [row["ticker"] for row in payload["sections"]["COMBINED CANDIDATES"]] == ["AAA"]
    assert [row["ticker"] for row in payload["sections"]["COMBINED WATCHLIST"]] == ["BBB"]
    assert [row["ticker"] for row in payload["sections"]["GRAHAM WATCHLIST"]] == ["BBB"]
    assert [row["ticker"] for row in payload["sections"]["TECHNICAL WATCHLIST"]] == ["CCC"]
    assert payload["summary"]["combined_qualified_count"] == 1
    assert payload["summary"]["combined_watchlist_count"] == 2
    assert payload["summary"]["combined_rows_displayed"] == 2
    assert json.loads(json.dumps(payload, sort_keys=True))


def _ready_price_rows(ticker):
    return [
        {
            "ticker": ticker,
            "trade_date": f"2026-06-{day:02d}",
            "open": 10.0,
            "high": 11.0,
            "low": 9.0,
            "close": 10.0,
            "adjusted_close": 10.0,
            "volume": 1000,
            "downloaded_at": "now",
        }
        for day in range(1, 24)
    ]


class FakeSECSource:
    def __init__(self, facts=None):
        self.calls = []
        self.facts = facts if facts is not None else {
            "facts": {
                "us-gaap": {
                    "EarningsPerShareDiluted": {
                        "units": {
                            "USD/shares": [
                                {
                                    "accn": "0000018654-24-000001",
                                    "fy": 2024,
                                    "fp": "FY",
                                    "form": "10-K",
                                    "filed": "2025-02-01",
                                    "start": "2024-01-01",
                                    "end": "2024-12-31",
                                    "val": 1.23,
                                }
                            ]
                        }
                    }
                }
            }
        }

    def get_cik_for_ticker(self, ticker):
        return "0000018654"

    def get_submissions(self, cik):
        self.calls.append(("submissions", cik))
        return {
            "name": "AAA Corp",
            "filings": {
                "recent": {
                    "accessionNumber": ["0000018654-24-000001"],
                    "form": ["10-K"],
                    "filingDate": ["2025-02-01"],
                    "acceptanceDateTime": ["2025-02-01T10:00:00"],
                    "reportDate": ["2024-12-31"],
                    "fy": [2024],
                    "fp": ["FY"],
                }
            },
        }

    def get_company_facts(self, cik):
        self.calls.append(("companyfacts", cik))
        return self.facts


def test_duplicate_sec_cik_is_requested_once(temp_database, monkeypatch):
    import data.readiness as readiness

    monkeypatch.setattr(readiness.settings, "SEC_USER_AGENT", "AlgoTradProject/0.1 test@example.com")
    cache_ticker_map(
        [
            {"ticker": "AAA", "cik": "0000018654", "title": "AAA Common Stock"},
            {"ticker": "BBB", "cik": "0000018654", "title": "BBB Common Stock"},
        ],
        database_path=temp_database,
    )
    build_universe_from_sec_map(database_path=temp_database)
    for ticker in ["AAA", "BBB"]:
        upsert_security(ticker, database_path=temp_database)
        upsert_daily_prices(_ready_price_rows(ticker), database_path=temp_database)
    calls = []

    def worker(ticker, years):
        calls.append(ticker)
        upsert_provider_refresh_success("sec", "cik", "0000018654", ticker=ticker, database_path=temp_database)
        return {"status": "updated", "provider_success_record_written": True}

    payload = prepare_universe_data(
        ["AAA", "BBB"],
        price_years=0,
        fundamental_years=6,
        as_of="2026-06-23",
        fundamental_worker=worker,
        database_path=temp_database,
    )

    assert calls == ["AAA"]
    assert payload["summary"]["sec_update_results"]["duplicate_cik_skipped"] == 1
    assert get_provider_refresh_status("sec", "cik", "0000018654", database_path=temp_database)["status"] == "success"


def test_successful_sec_persistence_skips_same_day_rerun(temp_database, monkeypatch):
    import data.readiness as readiness

    monkeypatch.setattr(readiness.settings, "SEC_USER_AGENT", "AlgoTradProject/0.1 test@example.com")
    cache_ticker_map([{"ticker": "AAA", "cik": "0000018654", "title": "AAA Common Stock"}], database_path=temp_database)
    build_universe_from_sec_map(database_path=temp_database)
    upsert_security("AAA", database_path=temp_database)
    upsert_daily_prices(_ready_price_rows("AAA"), database_path=temp_database)
    fake = FakeSECSource(facts={"facts": {"us-gaap": {}}})

    def worker(ticker, years):
        return update_fundamentals_for_ticker(ticker, years=years, client=fake, database_path=temp_database)

    first = prepare_universe_data(["AAA"], 0, 6, as_of="2026-06-23", fundamental_worker=worker, database_path=temp_database)
    assert first["summary"]["sec_update_results"]["success_records_written"] == 1
    assert len([call for call in fake.calls if call[0] == "companyfacts"]) == 1
    assert get_provider_refresh_status("sec", "cik", "0000018654", database_path=temp_database)["status"] == "success"

    def fail_worker(ticker, years):
        raise AssertionError("SEC should not be requested when provider success is fresh")

    second = prepare_universe_data(["AAA"], 0, 6, as_of="2026-06-23", fundamental_worker=fail_worker, database_path=temp_database)
    assert second["summary"]["sec_update_results"]["success_freshness_skipped"] == 1
    assert second["summary"]["sec_update_results"]["requests"] == 0


def test_incomplete_graham_data_does_not_force_same_day_redownload(temp_database, monkeypatch):
    import data.readiness as readiness

    monkeypatch.setattr(readiness.settings, "SEC_USER_AGENT", "AlgoTradProject/0.1 test@example.com")
    cache_ticker_map([{"ticker": "AAA", "cik": "0000018654", "title": "AAA Common Stock"}], database_path=temp_database)
    build_universe_from_sec_map(database_path=temp_database)
    upsert_security("AAA", database_path=temp_database)
    upsert_daily_prices(_ready_price_rows("AAA"), database_path=temp_database)
    fake = FakeSECSource(facts={"facts": {"us-gaap": {}}})

    prepare_universe_data(
        ["AAA"],
        0,
        6,
        as_of="2026-06-23",
        fundamental_worker=lambda ticker, years: update_fundamentals_for_ticker(ticker, years=years, client=fake, database_path=temp_database),
        database_path=temp_database,
    )
    second = prepare_universe_data(
        ["AAA"],
        0,
        6,
        as_of="2026-06-23",
        fundamental_worker=lambda ticker, years: (_ for _ in ()).throw(AssertionError("should not redownload incomplete Graham data")),
        database_path=temp_database,
    )

    assert second["summary"]["combined_evaluable_tickers"] == 0
    assert second["summary"]["sec_update_results"]["success_freshness_skipped"] == 1


def test_force_provider_refresh_bypasses_success_status(temp_database, monkeypatch):
    import data.readiness as readiness

    monkeypatch.setattr(readiness.settings, "SEC_USER_AGENT", "AlgoTradProject/0.1 test@example.com")
    cache_ticker_map([{"ticker": "AAA", "cik": "0000018654", "title": "AAA Common Stock"}], database_path=temp_database)
    build_universe_from_sec_map(database_path=temp_database)
    upsert_security("AAA", database_path=temp_database)
    upsert_daily_prices(_ready_price_rows("AAA"), database_path=temp_database)
    upsert_provider_refresh_success("sec", "cik", "0000018654", ticker="AAA", database_path=temp_database)
    calls = []

    def worker(ticker, years):
        calls.append(ticker)
        upsert_provider_refresh_success("sec", "cik", "0000018654", ticker=ticker, database_path=temp_database)
        return {"status": "updated", "provider_success_record_written": True}

    prepare_universe_data(["AAA"], 0, 6, as_of="2026-06-23", fundamental_worker=worker, database_path=temp_database, force_provider_refresh=True)

    assert calls == ["AAA"]


def test_provider_success_timestamp_freshness_window():
    from datetime import datetime, timezone

    status = {"status": "success", "last_success_at": "2026-06-25T12:00:00+00:00"}

    fresh = provider_refresh_is_fresh(status, 24, now=datetime(2026, 6, 25, 18, 0, tzinfo=timezone.utc))
    stale = provider_refresh_is_fresh(status, 1, now=datetime(2026, 6, 25, 14, 0, tzinfo=timezone.utc))

    assert fresh["fresh"]
    assert not stale["fresh"]
    assert stale["stale_reason"] == "provider success status is stale"


def test_sec_cooldown_skips_provider_request(temp_database, monkeypatch):
    import data.readiness as readiness

    monkeypatch.setattr(readiness.settings, "SEC_USER_AGENT", "AlgoTradProject/0.1 test@example.com")
    cache_ticker_map(
        [{"ticker": "AAA", "cik": "0000018654", "title": "AAA Common Stock"}],
        database_path=temp_database,
    )
    build_universe_from_sec_map(database_path=temp_database)
    upsert_security("AAA", database_path=temp_database)
    upsert_daily_prices(_ready_price_rows("AAA"), database_path=temp_database)
    upsert_provider_cooldown(
        "sec",
        "cik",
        "0000018654",
        "http_404",
        "permanent or unsupported",
        "SEC request failed: HTTP 404",
        "9999-12-31",
        ticker="AAA",
        database_path=temp_database,
    )

    def worker(ticker, years):
        raise AssertionError("SEC worker should not be called while cooldown is active")

    payload = prepare_universe_data(
        ["AAA"],
        price_years=0,
        fundamental_years=6,
        as_of="2026-06-23",
        fundamental_worker=worker,
        database_path=temp_database,
    )

    assert payload["summary"]["sec_update_results"]["cooldown_skipped"] == 1
    assert payload["summary"]["failures_by_retry_classification"]["permanent or unsupported"] == 1


def _storage_snapshot(tmp_path, free_mb=2048, size_mb=1):
    db = tmp_path / "algotrad.db"
    db.write_bytes(b"0" * 128)
    return {
        "database_path": str(db),
        "database_size_bytes": size_mb * 1024 * 1024,
        "database_size_mb": float(size_mb),
        "sidecar_sizes_bytes": {str(db): size_mb * 1024 * 1024},
        "sidecar_total_bytes": size_mb * 1024 * 1024,
        "sidecar_total_mb": float(size_mb),
        "free_space_bytes": free_mb * 1024 * 1024,
        "free_space_mb": float(free_mb),
    }


def _patch_daily_happy_path(monkeypatch, tmp_path):
    import main

    ticker_file = tmp_path / "tickers.txt"
    ticker_file.write_text("AAA\nBBB\n", encoding="utf-8")
    monkeypatch.setattr(main, "_database_storage_snapshot", lambda path: _storage_snapshot(tmp_path))
    monkeypatch.setattr(main, "initialize_database", lambda: None)
    monkeypatch.setattr(
        main,
        "prepare_universe_data",
        lambda *args, **kwargs: {
            "summary": {
                "resolved_tickers": 2,
                "price_update_succeeded": 0,
                "sec_update_succeeded": 0,
                "sec_update_attempted": 0,
                "failed_count_by_stage": {"PRICE_UPDATE_COMPLETE": 0},
                "price_update_results": {"already_current": 2, "updated": 0, "no_data": 0, "failed": 0, "rows_stored": 0, "requests": 0},
                "failures_by_reason": {},
            },
            "stage_items": [
                {"stage": "PRICE_UPDATE_COMPLETE", "status": "skipped", "reason": "already price-ready"},
                {"stage": "PRICE_UPDATE_COMPLETE", "status": "skipped", "reason": "already price-ready"},
                {"stage": "SEC_INGESTION_COMPLETE", "status": "skipped", "reason": "already has SEC filings"},
                {"stage": "NORMALIZATION_COMPLETE", "status": "skipped", "reason": "already normalized"},
            ],
        },
    )
    monkeypatch.setattr(
        main,
        "_combined_screen_payload",
        lambda args, tickers: {
            "rows": [
                {"ticker": "AAA", "price": 10, "data_ready": True, "graham_score": 80, "graham_qualified": False, "panic_score": 9, "technical_qualified": False, "combined_score": 70, "combined_qualified": False, "primary_data_issue": ""}
            ],
            "coverage_summary": {
                "price_ready_tickers": 2,
                "graham_evaluable_tickers": 1,
                "technical_evaluable_tickers": 1,
                "combined_evaluable_tickers": 1,
                "graham_qualified_tickers": 0,
                "technical_qualified_tickers": 0,
                "combined_qualified_tickers": 0,
            },
            "readiness": {
                "rows": [
                    {
                        "normalized_symbol": "AAA",
                        "latest_price_date": "2026-06-24",
                        "final_readiness_category": "READY",
                        "graham_evaluability_reason": "",
                        "graham_evaluable": True,
                        "technical_evaluable": True,
                        "combined_evaluable": True,
                    }
                ]
            },
        },
    )
    monkeypatch.setattr(main, "list_security_universe", lambda: [{"normalized_ticker": "AAA", "company_name": "AAA Inc."}])
    monkeypatch.setattr(main, "production_universe_summary", lambda: {"eligible_securities": 2})
    return ticker_file


def test_daily_ticker_file_does_not_refresh_universe_by_default(monkeypatch, tmp_path):
    import main

    ticker_file = _patch_daily_happy_path(monkeypatch, tmp_path)
    monkeypatch.setattr(main, "build_universe_from_sec_map", lambda: (_ for _ in ()).throw(AssertionError("unexpected universe rebuild")))

    args = parse_args(["run-daily-screen", "--ticker-file", str(ticker_file), "--export-dir", str(tmp_path / "daily")])
    _run_daily_screen_command(args)

    assert (tmp_path / "daily" / args.as_of / "daily-opportunities.json").exists()


def test_daily_explicit_refresh_universe_still_works(monkeypatch, tmp_path):
    import main

    ticker_file = _patch_daily_happy_path(monkeypatch, tmp_path)
    calls = {"refresh": 0}

    class Result:
        source = "test"
        rows_seen = 1
        rows_upserted = 1
        eligible_count = 1
        dry_run = False
        warnings = []

    def refresh():
        calls["refresh"] += 1
        return Result()

    monkeypatch.setattr(main, "build_universe_from_sec_map", refresh)

    args = parse_args(["run-daily-screen", "--ticker-file", str(ticker_file), "--refresh-universe", "--export-dir", str(tmp_path / "daily")])
    _run_daily_screen_command(args)

    assert calls["refresh"] == 1


def test_daily_low_disk_stops_before_write_heavy_work(monkeypatch, tmp_path):
    import main

    ticker_file = tmp_path / "tickers.txt"
    ticker_file.write_text("AAA\n", encoding="utf-8")
    monkeypatch.setattr(main, "_database_storage_snapshot", lambda path: _storage_snapshot(tmp_path, free_mb=10))
    monkeypatch.setattr(main, "prepare_universe_data", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not prepare")))
    monkeypatch.setattr(main, "build_universe_from_sec_map", lambda: (_ for _ in ()).throw(AssertionError("should not refresh")))

    args = parse_args(["run-daily-screen", "--ticker-file", str(ticker_file), "--minimum-free-space-mb", "1024"])
    _run_daily_screen_command(args)


def test_full_evaluation_export_is_opt_in(monkeypatch, tmp_path):
    ticker_file = _patch_daily_happy_path(monkeypatch, tmp_path)

    args = parse_args(["run-daily-screen", "--ticker-file", str(ticker_file), "--export-dir", str(tmp_path / "default")])
    _run_daily_screen_command(args)
    assert not (tmp_path / "default" / args.as_of / "combined-full-evaluations.json").exists()

    args = parse_args(["run-daily-screen", "--ticker-file", str(ticker_file), "--export-dir", str(tmp_path / "full"), "--include-full-evaluations"])
    _run_daily_screen_command(args)
    assert (tmp_path / "full" / args.as_of / "combined-full-evaluations.json").exists()


def test_database_maintenance_reports_integrity_and_checkpoint(temp_database, monkeypatch, capsys):
    import main

    monkeypatch.setattr(main, "DATABASE_PATH", temp_database)
    _database_maintenance_command(Namespace(vacuum=False, minimum_free_space_mb=1, json=False))

    output = capsys.readouterr().out
    assert "Integrity check: ok" in output
    assert "WAL checkpoint:" in output
