"""Transparent Graham value calculations."""

import math
from typing import Optional


def _finite(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    numeric = float(value)
    if not math.isfinite(numeric):
        return None
    return numeric


def safe_divide(numerator: Optional[float], denominator: Optional[float]) -> Optional[float]:
    """Divide with finite-value and zero-denominator protection."""
    num = _finite(numerator)
    den = _finite(denominator)
    if num is None or den is None or den == 0:
        return None
    return num / den


def common_shareholders_equity(shareholders_equity: Optional[float], preferred_equity: Optional[float] = None) -> Optional[float]:
    """Return common equity, subtracting preferred equity when available."""
    equity = _finite(shareholders_equity)
    if equity is None:
        return None
    preferred = _finite(preferred_equity)
    return equity if preferred is None else equity - preferred


def book_value_per_share(common_equity: Optional[float], shares_outstanding: Optional[float]) -> Optional[float]:
    """Return book value per share; share count must be positive."""
    shares = _finite(shares_outstanding)
    equity = _finite(common_equity)
    if shares is None or shares <= 0 or equity is None:
        return None
    return equity / shares


def tangible_common_equity(common_equity: Optional[float], goodwill: Optional[float] = None, intangible_assets: Optional[float] = None) -> Optional[float]:
    """Return tangible common equity only when goodwill and intangibles are known."""
    equity = _finite(common_equity)
    goodwill_value = _finite(goodwill)
    intangible_value = _finite(intangible_assets)
    if equity is None or goodwill_value is None or intangible_value is None:
        return None
    return equity - goodwill_value - intangible_value


def tangible_book_value_per_share(tangible_equity: Optional[float], shares_outstanding: Optional[float]) -> Optional[float]:
    """Return tangible book value per share."""
    return book_value_per_share(tangible_equity, shares_outstanding)


def graham_number(eps: Optional[float], book_value: Optional[float]) -> Optional[float]:
    """Return Graham Number: sqrt(22.5 * EPS * book value per share)."""
    eps_value = _finite(eps)
    book = _finite(book_value)
    if eps_value is None or book is None or eps_value <= 0 or book <= 0:
        return None
    return math.sqrt(22.5 * eps_value * book)


def margin_of_safety(market_price: Optional[float], estimated_value: Optional[float]) -> Optional[float]:
    """Return (estimated value - market price) / estimated value."""
    price = _finite(market_price)
    value = _finite(estimated_value)
    if price is None or value is None or price <= 0 or value <= 0:
        return None
    return (value - price) / value


def price_to_earnings(market_price: Optional[float], eps: Optional[float]) -> Optional[float]:
    """Return price-to-earnings."""
    return safe_divide(market_price, eps)


def price_to_book(market_price: Optional[float], book_value: Optional[float]) -> Optional[float]:
    """Return price-to-book."""
    return safe_divide(market_price, book_value)


def pe_times_pb(pe: Optional[float], pb: Optional[float]) -> Optional[float]:
    """Return P/E times P/B."""
    pe_value = _finite(pe)
    pb_value = _finite(pb)
    if pe_value is None or pb_value is None:
        return None
    return pe_value * pb_value


def current_ratio(current_assets: Optional[float], current_liabilities: Optional[float]) -> Optional[float]:
    """Return current ratio."""
    return safe_divide(current_assets, current_liabilities)


def net_current_assets(current_assets: Optional[float], current_liabilities: Optional[float]) -> Optional[float]:
    """Return current assets less current liabilities."""
    assets = _finite(current_assets)
    liabilities = _finite(current_liabilities)
    if assets is None or liabilities is None:
        return None
    return assets - liabilities


def debt_to_equity(total_debt: Optional[float], common_equity: Optional[float]) -> Optional[float]:
    """Return debt-to-equity."""
    return safe_divide(total_debt, common_equity)


def interest_coverage(operating_income: Optional[float], interest_expense: Optional[float]) -> Optional[float]:
    """Return operating income divided by absolute interest expense."""
    expense = _finite(interest_expense)
    if expense is None:
        return None
    return safe_divide(operating_income, abs(expense))
