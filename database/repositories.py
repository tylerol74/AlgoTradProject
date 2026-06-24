"""Repository functions for database access.

Raw SQL should live here so downloaders, CLIs, and future strategy modules read
and write through a consistent database boundary.
"""

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

from database.connection import get_connection

logger = logging.getLogger(__name__)
DatabasePath = Union[str, Path]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def upsert_security(
    ticker: str,
    company_name: Optional[str] = None,
    exchange: Optional[str] = None,
    security_type: Optional[str] = None,
    is_active: bool = True,
    database_path: Optional[DatabasePath] = None,
) -> None:
    """Insert or update a security record."""
    normalized = ticker.strip().upper()
    if not normalized:
        raise ValueError("ticker is required")

    with get_connection(database_path) as connection:
        connection.execute(
            """
            INSERT INTO securities (
                ticker, company_name, exchange, security_type, is_active, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker) DO UPDATE SET
                company_name = COALESCE(excluded.company_name, securities.company_name),
                exchange = COALESCE(excluded.exchange, securities.exchange),
                security_type = COALESCE(excluded.security_type, securities.security_type),
                is_active = excluded.is_active,
                updated_at = excluded.updated_at
            """,
            (
                normalized,
                company_name,
                exchange,
                security_type,
                1 if is_active else 0,
                _utc_now_iso(),
            ),
        )
    logger.debug("Upserted security %s", normalized)


def upsert_daily_prices(
    rows: Sequence[Dict[str, Any]],
    database_path: Optional[DatabasePath] = None,
) -> int:
    """Insert or update daily price rows using SQLite UPSERT."""
    if not rows:
        return 0

    parameters = [
        (
            row["ticker"],
            row["trade_date"],
            row["open"],
            row["high"],
            row["low"],
            row["close"],
            row["adjusted_close"],
            row["volume"],
            row["downloaded_at"],
        )
        for row in rows
    ]

    with get_connection(database_path) as connection:
        connection.executemany(
            """
            INSERT INTO daily_prices (
                ticker, trade_date, open, high, low, close, adjusted_close, volume, downloaded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker, trade_date) DO UPDATE SET
                open = excluded.open,
                high = excluded.high,
                low = excluded.low,
                close = excluded.close,
                adjusted_close = excluded.adjusted_close,
                volume = excluded.volume,
                downloaded_at = excluded.downloaded_at
            """,
            parameters,
        )

    logger.info("Upserted %s daily price rows", len(parameters))
    return len(parameters)


def get_latest_price_date(
    ticker: str,
    database_path: Optional[DatabasePath] = None,
) -> Optional[str]:
    """Return the latest stored trade date for a ticker."""
    with get_connection(database_path) as connection:
        row = connection.execute(
            "SELECT MAX(trade_date) AS latest_date FROM daily_prices WHERE ticker = ?",
            (ticker.strip().upper(),),
        ).fetchone()
    return row["latest_date"] if row and row["latest_date"] else None


def get_price_history(
    ticker: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    database_path: Optional[DatabasePath] = None,
) -> List[Dict[str, Any]]:
    """Retrieve stored daily prices for a ticker and optional date range."""
    query = [
        """
        SELECT ticker, trade_date, open, high, low, close, adjusted_close, volume, downloaded_at
        FROM daily_prices
        WHERE ticker = ?
        """
    ]
    parameters: List[Any] = [ticker.strip().upper()]

    if start_date:
        query.append("AND trade_date >= ?")
        parameters.append(start_date)
    if end_date:
        query.append("AND trade_date <= ?")
        parameters.append(end_date)

    query.append("ORDER BY trade_date")

    with get_connection(database_path) as connection:
        rows = connection.execute("\n".join(query), parameters).fetchall()
    return [dict(row) for row in rows]


def count_price_rows(
    ticker: Optional[str] = None,
    database_path: Optional[DatabasePath] = None,
) -> int:
    """Count daily price rows, optionally for one ticker."""
    with get_connection(database_path) as connection:
        if ticker:
            row = connection.execute(
                "SELECT COUNT(*) AS row_count FROM daily_prices WHERE ticker = ?",
                (ticker.strip().upper(),),
            ).fetchone()
        else:
            row = connection.execute(
                "SELECT COUNT(*) AS row_count FROM daily_prices"
            ).fetchone()
    return int(row["row_count"])


def security_exists(ticker: str, database_path: Optional[DatabasePath] = None) -> bool:
    """Return whether a security exists."""
    with get_connection(database_path) as connection:
        row = connection.execute(
            "SELECT 1 FROM securities WHERE ticker = ? LIMIT 1",
            (ticker.strip().upper(),),
        ).fetchone()
    return row is not None


def get_database_status(database_path: Optional[DatabasePath] = None) -> Dict[str, Any]:
    """Return high-level database status metrics for CLI reporting."""
    with get_connection(database_path) as connection:
        securities = connection.execute(
            "SELECT COUNT(*) AS row_count FROM securities"
        ).fetchone()["row_count"]
        prices = connection.execute(
            """
            SELECT
                COUNT(*) AS row_count,
                MIN(trade_date) AS earliest_date,
                MAX(trade_date) AS latest_date
            FROM daily_prices
            """
        ).fetchone()
        by_ticker = connection.execute(
            """
            SELECT ticker, COUNT(*) AS row_count
            FROM daily_prices
            GROUP BY ticker
            ORDER BY ticker
            """
        ).fetchall()

    return {
        "securities": int(securities),
        "daily_price_rows": int(prices["row_count"]),
        "earliest_date": prices["earliest_date"],
        "latest_date": prices["latest_date"],
        "rows_by_ticker": {row["ticker"]: int(row["row_count"]) for row in by_ticker},
    }

def get_available_tickers(database_path: Optional[DatabasePath] = None) -> List[str]:
    """Return active tickers that have stored daily prices."""
    with get_connection(database_path) as connection:
        rows = connection.execute(
            """
            SELECT DISTINCT ticker
            FROM daily_prices
            ORDER BY ticker
            """
        ).fetchall()
    return [row["ticker"] for row in rows]


def get_trading_dates(
    tickers: Sequence[str],
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    database_path: Optional[DatabasePath] = None,
) -> List[str]:
    """Return distinct stored trading dates for one or more tickers."""
    normalized = [ticker.strip().upper() for ticker in tickers if ticker and ticker.strip()]
    if not normalized:
        return []

    placeholders = ", ".join("?" for _ in normalized)
    query = [
        f"""
        SELECT DISTINCT trade_date
        FROM daily_prices
        WHERE ticker IN ({placeholders})
        """
    ]
    parameters: List[Any] = list(normalized)

    if start_date:
        query.append("AND trade_date >= ?")
        parameters.append(start_date)
    if end_date:
        query.append("AND trade_date <= ?")
        parameters.append(end_date)
    query.append("ORDER BY trade_date")

    with get_connection(database_path) as connection:
        rows = connection.execute("\n".join(query), parameters).fetchall()
    return [row["trade_date"] for row in rows]


def get_price_on_date(
    ticker: str,
    trade_date: str,
    database_path: Optional[DatabasePath] = None,
) -> Optional[Dict[str, Any]]:
    """Return one stored price row for a ticker/date, if present."""
    with get_connection(database_path) as connection:
        row = connection.execute(
            """
            SELECT ticker, trade_date, open, high, low, close, adjusted_close, volume, downloaded_at
            FROM daily_prices
            WHERE ticker = ? AND trade_date = ?
            """,
            (ticker.strip().upper(), trade_date),
        ).fetchone()
    return dict(row) if row else None


def get_next_trading_day(
    ticker: str,
    trade_date: str,
    database_path: Optional[DatabasePath] = None,
) -> Optional[str]:
    """Return the next stored trading day after trade_date for a ticker."""
    with get_connection(database_path) as connection:
        row = connection.execute(
            """
            SELECT MIN(trade_date) AS next_date
            FROM daily_prices
            WHERE ticker = ? AND trade_date > ?
            """,
            (ticker.strip().upper(), trade_date),
        ).fetchone()
    return row["next_date"] if row and row["next_date"] else None
