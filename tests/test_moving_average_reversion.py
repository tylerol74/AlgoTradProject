import pytest

import database.connection as connection_module
from backtesting.models import Position, SignalAction
from database.repositories import count_price_rows, get_price_history, upsert_daily_prices, upsert_security
from database.schema import initialize_database
from strategies.moving_average_reversion import MovingAverageReversionStrategy


@pytest.fixture
def temp_database(tmp_path, monkeypatch):
    db_path = tmp_path / "strategy.db"
    initialize_database(db_path)
    monkeypatch.setattr(connection_module, "DATABASE_PATH", db_path)
    return db_path


def make_history(closes, start_day=1):
    rows = []
    for offset, close in enumerate(closes):
        day = start_day + offset
        rows.append(
            {
                "ticker": "AAPL",
                "trade_date": f"2024-01-{day:02d}",
                "open": float(close),
                "high": float(close) + 1.0,
                "low": float(close) - 1.0,
                "close": float(close),
                "adjusted_close": float(close),
                "volume": 1000,
                "downloaded_at": "2026-06-24T00:00:00+00:00",
            }
        )
    return rows


def test_entry_signal_generation_when_threshold_is_met():
    history = make_history([100.0] * 19 + [90.0])
    strategy = MovingAverageReversionStrategy()

    signal = strategy.generate_entry_signal("AAPL", "2024-01-20", history)

    assert signal.action == SignalAction.BUY
    assert signal.score == 0.095477
    assert "below" in signal.reason


def test_no_entry_signal_when_threshold_not_met():
    history = make_history([100.0] * 19 + [97.0])
    strategy = MovingAverageReversionStrategy()

    signal = strategy.generate_entry_signal("AAPL", "2024-01-20", history)

    assert signal.action == SignalAction.HOLD
    assert signal.score == 0.0


def test_exit_at_moving_average():
    history = make_history([100.0] * 19 + [100.0])
    strategy = MovingAverageReversionStrategy()
    position = Position("AAPL", strategy.name, 10.0, "2024-01-01", 90.0, 100.0)

    signal = strategy.generate_exit_signal(position, "2024-01-20", history)

    assert signal.action == SignalAction.SELL
    assert "SMA" in signal.reason


def test_stop_loss_exit():
    history = make_history([100.0] * 19 + [80.0])
    strategy = MovingAverageReversionStrategy(stop_loss_pct=0.10)
    position = Position("AAPL", strategy.name, 10.0, "2024-01-01", 90.0, 80.0)

    signal = strategy.generate_exit_signal(position, "2024-01-20", history)

    assert signal.action == SignalAction.SELL
    assert "stop loss" in signal.reason


def test_maximum_holding_period_exit():
    history = make_history([100.0] * 19 + [95.0])
    strategy = MovingAverageReversionStrategy(maximum_holding_days=10)
    position = Position("AAPL", strategy.name, 10.0, "2024-01-01", 100.0, 95.0)

    signal = strategy.generate_exit_signal(position, "2024-01-20", history)

    assert signal.action == SignalAction.SELL
    assert "maximum holding period" in signal.reason


def test_strategy_receives_no_rows_after_as_of_date():
    history = make_history([100.0] * 19 + [90.0])
    history.append(
        {
            "ticker": "AAPL",
            "trade_date": "2024-01-21",
            "open": 300.0,
            "high": 301.0,
            "low": 299.0,
            "close": 300.0,
            "adjusted_close": 300.0,
            "volume": 1000,
            "downloaded_at": "2026-06-24T00:00:00+00:00",
        }
    )
    strategy = MovingAverageReversionStrategy()

    signal = strategy.generate_entry_signal("AAPL", "2024-01-20", history)

    assert signal.action == SignalAction.BUY
    assert signal.score == 0.095477


def test_deterministic_signal_scores():
    history = make_history([100.0] * 19 + [90.0])
    strategy = MovingAverageReversionStrategy()

    first = strategy.generate_entry_signal("AAPL", "2024-01-20", history)
    second = strategy.generate_entry_signal("AAPL", "2024-01-20", list(reversed(history)))

    assert first.score == second.score == 0.095477
    assert first.action == second.action


def test_insufficient_history_generates_hold():
    strategy = MovingAverageReversionStrategy()

    signal = strategy.generate_entry_signal("AAPL", "2024-01-10", make_history([100.0] * 10))

    assert signal.action == SignalAction.HOLD
    assert signal.reason == "insufficient history"


def test_strategy_does_not_modify_daily_prices(temp_database):
    upsert_security("AAPL")
    upsert_daily_prices(make_history([100.0] * 19 + [90.0]))
    before_count = count_price_rows("AAPL")
    history = get_price_history("AAPL")
    strategy = MovingAverageReversionStrategy()

    strategy.generate_entry_signal("AAPL", "2024-01-20", history)

    assert count_price_rows("AAPL") == before_count

