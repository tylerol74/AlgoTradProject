import pytest

from database.repositories import count_price_rows
from reporting.backtest_report import build_equity_curve, calculate_monthly_returns, calculate_yearly_returns, create_backtest_report
from tests.reporting_helpers import seed_backtest, seed_market_data, snapshot


def test_loading_complete_saved_backtest_report(temp_database):
    backtest_id = seed_backtest()
    report = create_backtest_report(backtest_id)
    assert report.backtest_id == backtest_id
    assert report.performance["total_return"] == 0.1
    assert report.performance["excess_return"] == 0.05
    assert report.data_integrity["trade_count_loaded"] == 3
    assert report.data_integrity["snapshot_count_loaded"] == 3


def test_missing_backtest_id(temp_database):
    with pytest.raises(ValueError, match="not found"):
        create_backtest_report(999)


def test_incomplete_snapshot_warning(temp_database):
    backtest_id = seed_backtest(ending=1200, snapshots=[snapshot("2024-01-02", 1000, 0, 1000, 0)])
    report = create_backtest_report(backtest_id)
    assert any("final snapshot" in warning for warning in report.warnings)


def test_monthly_and_yearly_returns_from_known_snapshots():
    snapshots = [
        {"snapshot_date":"2024-01-31","cash":100,"holdings_value":900,"total_value":1000,"drawdown":0},
        {"snapshot_date":"2024-02-29","cash":100,"holdings_value":1000,"total_value":1100,"drawdown":0},
        {"snapshot_date":"2025-01-31","cash":100,"holdings_value":1200,"total_value":1300,"drawdown":0},
    ]
    assert calculate_monthly_returns(snapshots)[1]["monthly_return"] == pytest.approx(0.1)
    assert calculate_yearly_returns(snapshots)[1]["yearly_return"] == 1300 / 1100 - 1


def test_equity_curve_and_accounting_warning():
    result = build_equity_curve([
        {"snapshot_date":"2024-01-01","cash":1000,"holdings_value":0,"total_value":1000,"drawdown":0},
        {"snapshot_date":"2024-01-02","cash":500,"holdings_value":400,"total_value":950,"drawdown":-0.05},
    ])
    assert result["rows"][0]["normalized_equity"] == 1.0
    assert result["rows"][1]["drawdown"] == -0.05
    assert result["rows"][1]["cash_pct"] == 500 / 950
    assert result["warnings"]


def test_reporting_does_not_modify_daily_prices(temp_database):
    seed_market_data()
    before = count_price_rows()
    backtest_id = seed_backtest()
    create_backtest_report(backtest_id)
    assert count_price_rows() == before


def test_repeated_reports_are_deterministic(temp_database):
    backtest_id = seed_backtest()
    first = create_backtest_report(backtest_id)
    second = create_backtest_report(backtest_id)
    assert first.performance == second.performance
    assert first.ticker_attribution == second.ticker_attribution

