import pytest

import database.connection as connection_module
from backtesting.engine import run_backtest
from backtesting.models import BacktestConfig, Signal, SignalAction
from database.repositories import count_price_rows, get_price_history, upsert_daily_prices, upsert_security
from database.schema import initialize_database
from strategies.base import BaseStrategy


class DateSignalStrategy(BaseStrategy):
    name = "date_signal"

    def __init__(self, entry_dates=None, exit_dates=None):
        self.entry_dates = entry_dates or {}
        self.exit_dates = exit_dates or {}
        self.entry_history_max_dates = []
        self.exit_history_max_dates = []

    def generate_entry_signal(self, ticker, as_of_date, price_history):
        if price_history:
            self.entry_history_max_dates.append(max(row["trade_date"] for row in self.history_as_of(price_history, as_of_date)))
        score = self.entry_dates.get((ticker, as_of_date))
        if score is None:
            return Signal(ticker, as_of_date, self.name, SignalAction.HOLD, 0.0, "hold")
        return Signal(ticker, as_of_date, self.name, SignalAction.BUY, score, "entry")

    def generate_exit_signal(self, position, as_of_date, price_history):
        if price_history:
            self.exit_history_max_dates.append(max(row["trade_date"] for row in self.history_as_of(price_history, as_of_date)))
        if (position.ticker, as_of_date) in self.exit_dates:
            return Signal(position.ticker, as_of_date, self.name, SignalAction.SELL, 1.0, "exit")
        return Signal(position.ticker, as_of_date, self.name, SignalAction.HOLD, 0.0, "hold")


@pytest.fixture
def temp_database(tmp_path, monkeypatch):
    db_path = tmp_path / "engine.db"
    initialize_database(db_path)
    monkeypatch.setattr(connection_module, "DATABASE_PATH", db_path)
    return db_path


def row(ticker, day, open_price, close_price):
    return {
        "ticker": ticker,
        "trade_date": f"2024-01-{day:02d}",
        "open": float(open_price),
        "high": max(open_price, close_price) + 1,
        "low": min(open_price, close_price) - 1,
        "close": float(close_price),
        "adjusted_close": float(close_price),
        "volume": 1000,
        "downloaded_at": "2026-06-24T00:00:00+00:00",
    }


def seed(ticker="AAPL", days=None):
    upsert_security(ticker)
    days = days or [(1, 10, 10), (2, 10, 10), (3, 9, 9), (4, 10, 10), (5, 10, 10), (6, 11, 11), (7, 12, 12)]
    upsert_daily_prices([row(ticker, *item) for item in days])


def config(tickers=None, max_positions=1):
    return BacktestConfig("date_signal", tickers or ["AAPL"], "2024-01-01", "2024-01-07", 10000, max_positions, 0.5, 0.0, 0.0, 60)


def test_end_to_end_manual_scenario(temp_database):
    seed()
    strategy = DateSignalStrategy({("AAPL", "2024-01-03"): 1.0}, {("AAPL", "2024-01-06"): 1.0})

    result = run_backtest(config(), strategy, persist=False)

    assert result.backtest_id is None
    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.signal_date == "2024-01-03"
    assert trade.entry_date == "2024-01-04"
    assert trade.entry_price == 10
    assert trade.exit_date == "2024-01-07"
    assert trade.exit_price == 12
    assert trade.quantity == 500
    assert trade.gross_pnl == 1000
    assert trade.net_pnl == 1000
    assert result.metrics["ending_portfolio_value"] == 11000


def test_signal_after_close_executes_at_next_open(temp_database):
    seed()
    strategy = DateSignalStrategy({("AAPL", "2024-01-03"): 1.0}, {("AAPL", "2024-01-06"): 1.0})
    result = run_backtest(config(), strategy, persist=False)
    assert result.trades[0].entry_date == "2024-01-04"
    assert result.trades[0].entry_price == 10


def test_future_rows_are_not_passed_to_strategies(temp_database):
    seed()
    strategy = DateSignalStrategy({("AAPL", "2024-01-03"): 1.0})
    run_backtest(config(), strategy, persist=False)
    assert all(date <= "2024-01-07" for date in strategy.entry_history_max_dates)
    assert "2024-01-07" not in strategy.entry_history_max_dates[:3]


def test_missing_current_close_is_handled_safely(temp_database):
    seed("AAPL", [(1, 10, 10), (2, 10, 10), (4, 10, 10)])
    strategy = DateSignalStrategy({("AAPL", "2024-01-03"): 1.0})
    result = run_backtest(config(), strategy, persist=False)
    assert result.trades == []


def test_missing_next_day_open_cancels_order(temp_database):
    seed("AAPL", [(1, 10, 10), (2, 10, 10), (3, 10, 10)])
    strategy = DateSignalStrategy({("AAPL", "2024-01-03"): 1.0})
    result = run_backtest(config(), strategy, persist=False)
    assert result.trades == []


def test_pending_duplicate_buy_is_prevented(temp_database):
    seed()
    strategy = DateSignalStrategy({("AAPL", "2024-01-02"): 1.0, ("AAPL", "2024-01-03"): 1.0}, {("AAPL", "2024-01-06"): 1.0})
    result = run_backtest(config(), strategy, persist=False)
    assert len(result.trades) == 1


def test_pending_duplicate_sell_is_prevented(temp_database):
    seed()
    strategy = DateSignalStrategy({("AAPL", "2024-01-02"): 1.0}, {("AAPL", "2024-01-04"): 1.0, ("AAPL", "2024-01-05"): 1.0})
    result = run_backtest(config(), strategy, persist=False)
    assert len(result.trades) == 1


def test_signal_ranking_score_descending_and_ticker_ascending(temp_database):
    seed("AAPL")
    seed("MSFT")
    strategy = DateSignalStrategy({("MSFT", "2024-01-03"): 0.5, ("AAPL", "2024-01-03"): 0.5}, {("AAPL", "2024-01-06"): 1.0})
    result = run_backtest(config(["MSFT", "AAPL"], max_positions=1), strategy, persist=False)
    assert len(result.trades) == 1
    assert result.trades[0].ticker == "AAPL"


def test_daily_snapshots_and_drawdown(temp_database):
    seed("AAPL", [(1, 10, 10), (2, 10, 10), (3, 10, 10), (4, 10, 8), (5, 8, 8), (6, 8, 8), (7, 8, 8)])
    strategy = DateSignalStrategy({("AAPL", "2024-01-03"): 1.0})
    result = run_backtest(config(), strategy, persist=False)
    assert len(result.portfolio_snapshots) == 7
    assert min(snapshot.drawdown for snapshot in result.portfolio_snapshots) < 0


def test_final_open_position_liquidation(temp_database):
    seed()
    strategy = DateSignalStrategy({("AAPL", "2024-01-03"): 1.0})
    result = run_backtest(config(), strategy, persist=False)
    assert result.trades[0].exit_reason == "OPEN_AT_END_LIQUIDATION"
    assert result.trades[0].exit_date == "2024-01-07"


def test_invalid_configuration_is_rejected(temp_database):
    seed()
    bad = BacktestConfig("date_signal", ["AAPL", "AAPL"], "2024-01-01", "2024-01-07", 10000, 1, 0.5, 0, 0, 60)
    with pytest.raises(ValueError, match="duplicate"):
        run_backtest(bad, DateSignalStrategy(), persist=False)


def test_missing_database_history_produces_useful_error(temp_database):
    with pytest.raises(ValueError, match="update-prices"):
        run_backtest(config(), DateSignalStrategy(), persist=False)


def test_daily_prices_remain_unchanged(temp_database):
    seed()
    before_count = count_price_rows()
    before_rows = get_price_history("AAPL")
    run_backtest(config(), DateSignalStrategy({("AAPL", "2024-01-03"): 1.0}), persist=False)
    assert count_price_rows() == before_count
    assert get_price_history("AAPL") == before_rows


def test_benchmark_return_is_included(temp_database):
    seed()
    result = run_backtest(config(), DateSignalStrategy(), benchmark_ticker="AAPL", persist=False)
    assert result.metrics["benchmark"]["return_pct"] == 0.2
