"""Moving-average indicators."""

from typing import Iterable, List, Optional

import pandas as pd


def _validate_window(window: int) -> None:
    if not isinstance(window, int):
        raise TypeError("window must be an integer")
    if window <= 0:
        raise ValueError("window must be positive")


def simple_moving_average(values: Iterable[float], window: int) -> Optional[float]:
    """Return the trailing simple moving average or None if history is insufficient."""
    _validate_window(window)
    series = pd.Series(list(values), dtype="float64")
    if len(series) < window:
        return None
    return float(series.tail(window).mean())


def distance_from_moving_average(close_series: Iterable[float], window: int) -> Optional[float]:
    """Return percentage distance of latest close from its trailing SMA."""
    values = list(close_series)
    sma = simple_moving_average(values, window)
    if sma is None:
        return None
    latest = float(values[-1])
    if sma == 0:
        raise ValueError("moving average cannot be zero")
    return (latest - sma) / sma
