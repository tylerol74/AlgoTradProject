import pytest

from portfolio.position_sizing import calculate_position_quantity


def test_whole_share_position_sizing():
    assert calculate_position_quantity(10000, 10000, 30, 0.5) == 166


def test_position_sizing_accounts_for_commission():
    assert calculate_position_quantity(1000, 1000, 100, 1.0, commission=5) == 9


def test_position_sizing_accounts_for_slippage():
    assert calculate_position_quantity(1000, 1000, 100, 1.0, slippage_pct=0.10) == 9


def test_one_share_cannot_be_afforded():
    assert calculate_position_quantity(1000, 99, 100, 1.0) == 0


def test_invalid_position_percentage():
    with pytest.raises(ValueError):
        calculate_position_quantity(1000, 1000, 100, 0)
    with pytest.raises(ValueError):
        calculate_position_quantity(1000, 1000, 100, 1.1)


def test_position_sizing_rejects_invalid_inputs():
    with pytest.raises(ValueError):
        calculate_position_quantity(1000, 1000, 0, 0.5)
    with pytest.raises(ValueError):
        calculate_position_quantity(1000, -1, 100, 0.5)
