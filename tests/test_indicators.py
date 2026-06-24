import pytest

from indicators.moving_averages import distance_from_moving_average, simple_moving_average
from indicators.returns import percentage_return, rolling_return


def test_simple_moving_average_calculation():
    assert simple_moving_average([1, 2, 3, 4, 5], 3) == 4.0


def test_percentage_return_calculation():
    assert percentage_return(110.0, 100.0) == 0.10


def test_rolling_return_uses_only_prior_rows():
    assert rolling_return([100.0, 105.0, 110.0], 1) == [None, 0.05, pytest.approx(0.0476190476)]


def test_distance_from_moving_average():
    assert distance_from_moving_average([100.0, 100.0, 90.0], 3) == pytest.approx(-0.0689655172)


def test_invalid_windows_raise_errors():
    with pytest.raises(ValueError):
        simple_moving_average([1, 2, 3], 0)
    with pytest.raises(ValueError):
        rolling_return([1, 2, 3], -1)


def test_insufficient_history_returns_none():
    assert simple_moving_average([1, 2], 3) is None
    assert distance_from_moving_average([1, 2], 3) is None


def test_invalid_prior_price_raises_error():
    with pytest.raises(ValueError):
        percentage_return(100.0, 0.0)
