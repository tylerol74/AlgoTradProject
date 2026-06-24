from backtesting.metrics import calculate_benchmark_return, calculate_metrics
from backtesting.models import PortfolioSnapshot, Trade


def trade(net, ret, entry="2024-01-01", exit="2024-01-03"):
    return Trade("AAPL", "test", "2024-01-01", entry, 100, exit, 100 + net, 1, net, net, ret, "exit")


def test_zero_trade_metrics_are_correct():
    snapshots = [PortfolioSnapshot("2024-01-01", 1000, 0, 1000, 0)]
    metrics = calculate_metrics(1000, 1000, [], snapshots)
    assert metrics["completed_trade_count"] == 0
    assert metrics["win_rate"] == 0.0
    assert metrics["profit_factor"] is None


def test_win_rate_and_profit_factor_with_losses():
    trades = [trade(10, 0.1), trade(-5, -0.05), trade(0, 0.0)]
    metrics = calculate_metrics(1000, 1005, trades, [])
    assert metrics["winning_trades"] == 1
    assert metrics["losing_trades"] == 1
    assert metrics["breakeven_trades"] == 1
    assert metrics["win_rate"] == 1 / 3
    assert metrics["profit_factor"] == 2


def test_profit_factor_without_losses_is_handled():
    metrics = calculate_metrics(1000, 1010, [trade(10, 0.1)], [])
    assert metrics["profit_factor"] is None


def test_average_holding_period_and_drawdown():
    snapshots = [
        PortfolioSnapshot("2024-01-01", 1000, 0, 1000, 0),
        PortfolioSnapshot("2024-01-02", 900, 0, 900, -0.1),
    ]
    metrics = calculate_metrics(1000, 900, [trade(10, 0.1, "2024-01-01", "2024-01-06")], snapshots)
    assert metrics["average_holding_period_days"] == 5
    assert metrics["maximum_drawdown"] == -0.1


def test_benchmark_return_is_correct():
    result = calculate_benchmark_return(
        [
            {"trade_date": "2024-01-02", "close": 100},
            {"trade_date": "2024-01-05", "close": 110},
        ],
        "2024-01-01",
        "2024-01-06",
    )
    assert result["return_pct"] == 0.1
