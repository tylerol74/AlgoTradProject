"""Validation helpers for downloaded market data."""

import math
from typing import Any, Dict, List, Tuple

REQUIRED_PRICE_FIELDS = ("open", "high", "low", "close")


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def validate_price_row(row: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Validate one normalized daily price row.

    Zero volume is allowed because some valid exchange/index series and stale quote
    days can report zero volume while still carrying valid OHLC prices.
    """
    errors: List[str] = []

    ticker = row.get("ticker")
    if not isinstance(ticker, str) or not ticker.strip():
        errors.append("ticker is required")

    trade_date = row.get("trade_date")
    if not isinstance(trade_date, str) or not trade_date.strip():
        errors.append("trade_date is required")

    for field in REQUIRED_PRICE_FIELDS:
        value = row.get(field)
        if value is None:
            errors.append(f"{field} is required")
        elif not _is_number(value):
            errors.append(f"{field} must be numeric")
        elif value <= 0:
            errors.append(f"{field} must be positive")

    volume = row.get("volume")
    if volume is None:
        errors.append("volume is required")
    elif not _is_number(volume):
        errors.append("volume must be numeric")
    elif volume < 0:
        errors.append("volume must be non-negative")

    if errors:
        return False, errors

    low = float(row["low"])
    high = float(row["high"])
    open_price = float(row["open"])
    close = float(row["close"])

    if high < low:
        errors.append("high must be greater than or equal to low")
    if not low <= open_price <= high:
        errors.append("open must be between low and high")
    if not low <= close <= high:
        errors.append("close must be between low and high")

    return not errors, errors
