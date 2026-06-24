from reporting.comparison import compare_backtests
from tests.reporting_helpers import seed_backtest


def test_comparison_identical_date_ranges_and_return_to_drawdown(temp_database):
    first = seed_backtest(ending=1100)
    second = seed_backtest(ending=1050)
    comparison = compare_backtests([first, second])
    assert comparison["warnings"] == []
    assert comparison["rows"][0]["backtest_id"] == first
    assert comparison["rows"][0]["return_to_drawdown_ratio"] == 0.1 / 0.02


def test_comparison_warnings_for_different_ranges_tickers_and_benchmarks(temp_database):
    first = seed_backtest(start="2024-01-01", end="2024-02-28", tickers=["AAPL"], benchmark="AAPL")
    second = seed_backtest(start="2024-03-01", end="2024-04-30", tickers=["MSFT"], benchmark="MSFT")
    warnings = compare_backtests([first, second])["warnings"]
    assert "runs have different date ranges" in warnings
    assert "runs have different ticker universes" in warnings
    assert "runs have different benchmarks" in warnings


def test_sorting_comparison_results(temp_database):
    first = seed_backtest(ending=1050)
    second = seed_backtest(ending=1100)
    rows = compare_backtests([first, second], sort_by="backtest_id", descending=True)["rows"]
    assert [row["backtest_id"] for row in rows] == [second, first]

