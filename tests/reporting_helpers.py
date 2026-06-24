import json

import pytest

import database.connection as connection_module
from backtesting.models import BacktestConfig, BacktestResult, PortfolioSnapshot, Trade
from database.repositories import (
    complete_backtest_run,
    create_backtest_run,
    get_price_history,
    insert_backtest_trades,
    insert_portfolio_snapshots,
    upsert_daily_prices,
    upsert_security,
)
from database.schema import initialize_database


@pytest.fixture
def temp_database(tmp_path, monkeypatch):
    db_path = tmp_path / "reporting.db"
    initialize_database(db_path)
    monkeypatch.setattr(connection_module, "DATABASE_PATH", db_path)
    return db_path


def trade(ticker, net, ret, entry="2024-01-02", exit="2024-01-05", reason="exit"):
    return Trade(ticker, "strategy", "2024-01-01", entry, 100.0, exit, 100.0 + net, 1.0, net, net, ret, reason)


def snapshot(date, cash, holdings, total, drawdown):
    return PortfolioSnapshot(date, cash, holdings, total, drawdown)


def seed_market_data():
    upsert_security("AAPL")
    upsert_daily_prices([
        {"ticker":"AAPL","trade_date":"2024-01-02","open":100,"high":101,"low":99,"close":100,"adjusted_close":100,"volume":1000,"downloaded_at":"x"},
        {"ticker":"AAPL","trade_date":"2024-01-03","open":101,"high":102,"low":100,"close":101,"adjusted_close":101,"volume":1000,"downloaded_at":"x"},
    ])


def seed_backtest(backtest_id_expected=None, strategy="strategy", start="2024-01-01", end="2024-02-28", tickers=None, benchmark="AAPL", ending=1100.0, trades=None, snapshots=None):
    tickers = tickers or ["AAPL", "MSFT"]
    trades = trades if trades is not None else [
        trade("AAPL", 100, 0.10, reason="moving-average recovery"),
        trade("MSFT", -50, -0.05, entry="2024-01-04", exit="2024-01-10", reason="stop loss"),
        trade("AAPL", 0, 0.0, entry="2024-01-11", exit="2024-01-12", reason="OPEN_AT_END_LIQUIDATION"),
    ]
    snapshots = snapshots if snapshots is not None else [
        snapshot("2024-01-02", 1000, 0, 1000, 0),
        snapshot("2024-01-31", 900, 150, 1050, 0),
        snapshot("2024-02-28", 1000, 100, ending, -0.02),
    ]
    metrics = {
        "total_return_pct": (ending - 1000) / 1000,
        "ending_portfolio_value": ending,
        "maximum_drawdown": min(s.drawdown for s in snapshots) if snapshots else 0,
        "completed_trade_count": len(trades),
        "win_rate": 1 / 3 if trades else 0,
        "profit_factor": 2.0,
        "average_trade_return": 0.0166666667,
        "average_winning_trade_return": 0.10,
        "average_losing_trade_return": -0.05,
        "average_holding_period_days": 4,
        "total_commissions": 0,
        "capital_invested_pct_time": 2 / 3,
        "benchmark": {"return_pct": 0.05, "ticker": benchmark},
    }
    payload = json.dumps({"config": {"strategy_name": strategy, "tickers": tickers, "benchmark_ticker": benchmark}, "metrics": metrics, "benchmark": metrics["benchmark"]}, sort_keys=True)
    for ticker in sorted({trade.ticker for trade in trades} | set(tickers)):
        upsert_security(ticker)
    backtest_id = create_backtest_run(strategy, start, end, 1000.0, payload)
    insert_backtest_trades(backtest_id, trades)
    insert_portfolio_snapshots(backtest_id, snapshots)
    complete_backtest_run(backtest_id, ending)
    if backtest_id_expected is not None:
        assert backtest_id == backtest_id_expected
    return backtest_id

