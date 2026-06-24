import json

import pytest

import database.connection as connection_module
from backtesting.engine import run_backtest
from backtesting.models import BacktestConfig, Signal, SignalAction
from database.repositories import (
    get_backtest_run,
    get_backtest_trades,
    get_portfolio_snapshots,
    get_price_history,
    upsert_daily_prices,
    upsert_security,
)
from database.schema import initialize_database
from main import _show_backtest
from strategies.base import BaseStrategy


class OneTradeStrategy(BaseStrategy):
    name = "one_trade"

    def generate_entry_signal(self, ticker, as_of_date, price_history):
        if as_of_date == "2024-01-03":
            return Signal(ticker, as_of_date, self.name, SignalAction.BUY, 1.0, "entry")
        return Signal(ticker, as_of_date, self.name, SignalAction.HOLD, 0.0, "hold")

    def generate_exit_signal(self, position, as_of_date, price_history):
        if as_of_date == "2024-01-06":
            return Signal(position.ticker, as_of_date, self.name, SignalAction.SELL, 1.0, "exit")
        return Signal(position.ticker, as_of_date, self.name, SignalAction.HOLD, 0.0, "hold")


@pytest.fixture
def temp_database(tmp_path, monkeypatch):
    db_path = tmp_path / "persist.db"
    initialize_database(db_path)
    monkeypatch.setattr(connection_module, "DATABASE_PATH", db_path)
    upsert_security("AAPL")
    rows = []
    for day, open_price, close_price in [(1,10,10),(2,10,10),(3,9,9),(4,10,10),(5,10,10),(6,11,11),(7,12,12)]:
        rows.append({
            "ticker": "AAPL", "trade_date": f"2024-01-{day:02d}", "open": open_price,
            "high": max(open_price, close_price)+1, "low": min(open_price, close_price)-1,
            "close": close_price, "adjusted_close": close_price, "volume": 1000,
            "downloaded_at": "2026-06-24T00:00:00+00:00",
        })
    upsert_daily_prices(rows)
    return db_path


def config():
    return BacktestConfig("one_trade", ["AAPL"], "2024-01-01", "2024-01-07", 10000, 1, 0.5, 0.0, 0.0, 60)


def test_backtest_run_trades_and_snapshots_persist(temp_database):
    before_prices = get_price_history("AAPL")
    result = run_backtest(config(), OneTradeStrategy(), persist=True)

    assert result.backtest_id is not None
    run = get_backtest_run(result.backtest_id)
    trades = get_backtest_trades(result.backtest_id)
    snapshots = get_portfolio_snapshots(result.backtest_id)
    assert run["strategy"] == "one_trade"
    assert len(trades) == 1
    assert len(snapshots) == 7
    assert get_price_history("AAPL") == before_prices


def test_show_backtest_retrieval_works(temp_database, capsys):
    result = run_backtest(config(), OneTradeStrategy(), persist=True)
    _show_backtest(result.backtest_id)
    output = capsys.readouterr().out
    assert f"Backtest ID: {result.backtest_id}" in output
    assert "Completed trades: 1" in output


def test_identical_backtests_are_deterministic_except_ids(temp_database):
    first = run_backtest(config(), OneTradeStrategy(), persist=True)
    second = run_backtest(config(), OneTradeStrategy(), persist=True)

    assert [trade.__dict__ for trade in first.trades] == [trade.__dict__ for trade in second.trades]
    first_metrics = dict(first.metrics)
    second_metrics = dict(second.metrics)
    assert first_metrics == second_metrics
    assert first.backtest_id != second.backtest_id


def test_parameters_json_contains_config_and_metrics(temp_database):
    result = run_backtest(config(), OneTradeStrategy(), persist=True)
    run = get_backtest_run(result.backtest_id)
    payload = json.loads(run["parameters_json"])
    assert payload["config"]["strategy_name"] == "one_trade"
    assert payload["metrics"]["completed_trade_count"] == 1
