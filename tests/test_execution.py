import pytest

from backtesting.execution import calculate_buy_fill_price, calculate_sell_fill_price


def test_buy_fill_increases_with_slippage():
    assert calculate_buy_fill_price(100, 0.01) == 101


def test_sell_fill_decreases_with_slippage():
    assert calculate_sell_fill_price(100, 0.01) == 99


def test_invalid_execution_price():
    with pytest.raises(ValueError):
        calculate_buy_fill_price(0, 0.01)
    with pytest.raises(ValueError):
        calculate_sell_fill_price(100, 1.0)
