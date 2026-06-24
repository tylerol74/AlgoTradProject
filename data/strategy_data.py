"""Read-only strategy data access backed by SQLite repositories."""

from typing import Any, Dict, List, Optional

from database import repositories


def get_available_tickers() -> List[str]:
    """Return tickers with stored daily price history."""
    return repositories.get_available_tickers()


def get_ticker_history(
    ticker: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return stored price history for a ticker and optional date range."""
    return repositories.get_price_history(ticker, start_date=start_date, end_date=end_date)


def get_trading_dates(
    tickers: List[str],
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> List[str]:
    """Return distinct stored trading dates for the requested tickers."""
    return repositories.get_trading_dates(tickers, start_date=start_date, end_date=end_date)


def get_price_on_date(ticker: str, trade_date: str) -> Optional[Dict[str, Any]]:
    """Return a stored price row for one ticker/date, if present."""
    return repositories.get_price_on_date(ticker, trade_date)


def get_next_trading_day(ticker: str, trade_date: str) -> Optional[str]:
    """Return the next stored trading day after trade_date for ticker."""
    return repositories.get_next_trading_day(ticker, trade_date)
