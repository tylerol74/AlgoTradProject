"""Deterministic order execution helpers."""

from typing import Any, Dict, Optional, Tuple

from backtesting.models import Order, OrderSide
from data import strategy_data


def _validate_price_and_slippage(price: float, slippage_pct: float) -> None:
    if price <= 0:
        raise ValueError("execution price must be positive")
    if slippage_pct < 0 or slippage_pct >= 1:
        raise ValueError("slippage_pct must be non-negative and less than 1")


def calculate_buy_fill_price(next_open: float, slippage_pct: float) -> float:
    """Return buy fill price at next open plus slippage."""
    _validate_price_and_slippage(next_open, slippage_pct)
    return next_open * (1.0 + slippage_pct)


def calculate_sell_fill_price(next_open: float, slippage_pct: float) -> float:
    """Return sell fill price at next open minus slippage."""
    _validate_price_and_slippage(next_open, slippage_pct)
    return next_open * (1.0 - slippage_pct)


def resolve_next_execution(
    ticker: str,
    signal_date: str,
    end_date: str,
) -> Tuple[Optional[str], Optional[float], Optional[str]]:
    """Resolve a signal to the ticker's next valid trading-day open."""
    execution_date = strategy_data.get_next_trading_day(ticker, signal_date)
    if execution_date is None:
        return None, None, "missing next trading day"
    if execution_date > end_date:
        return None, None, "execution date beyond backtest end date"
    row = strategy_data.get_price_on_date(ticker, execution_date)
    if row is None:
        return None, None, "missing execution price row"
    open_price = row.get("open")
    if open_price is None or float(open_price) <= 0:
        return execution_date, None, "missing or invalid next-day open"
    return execution_date, float(open_price), None


def order_fill_price(order: Order, open_price: float, slippage_pct: float) -> float:
    """Return deterministic fill price for an order side."""
    if order.side == OrderSide.BUY:
        return calculate_buy_fill_price(open_price, slippage_pct)
    if order.side == OrderSide.SELL:
        return calculate_sell_fill_price(open_price, slippage_pct)
    raise ValueError(f"unsupported order side: {order.side}")
