"""Return indicators."""

from typing import Iterable, List, Optional


def percentage_return(current_price: float, prior_price: float) -> float:
    """Return percentage return as a decimal, e.g. 0.05 for 5%."""
    if prior_price <= 0:
        raise ValueError("prior_price must be positive")
    return (float(current_price) - float(prior_price)) / float(prior_price)


def rolling_return(close_series: Iterable[float], periods: int) -> List[Optional[float]]:
    """Return trailing percentage returns for each close without future rows."""
    if not isinstance(periods, int):
        raise TypeError("periods must be an integer")
    if periods <= 0:
        raise ValueError("periods must be positive")

    values = [float(value) for value in close_series]
    returns: List[Optional[float]] = []
    for index, current in enumerate(values):
        if index < periods:
            returns.append(None)
        else:
            returns.append(percentage_return(current, values[index - periods]))
    return returns
