"""Centralized historical market-data download and storage."""

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import yfinance as yf

from config.settings import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_PRICE_HISTORY_START_DATE,
    YFINANCE_AUTO_ADJUST,
)
from data.validation import validate_price_row
from database.repositories import (
    get_latest_price_date,
    upsert_daily_prices,
    upsert_security,
)

logger = logging.getLogger(__name__)

PRICE_COLUMN_MAP = {
    "Open": "open",
    "High": "high",
    "Low": "low",
    "Close": "close",
    "Adj Close": "adjusted_close",
    "Volume": "volume",
}


def normalize_ticker(ticker: str) -> str:
    """Normalize a ticker symbol for storage and lookups."""
    if ticker is None:
        return ""
    return ticker.strip().upper()


def _parse_iso_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def _next_calendar_day(value: str) -> str:
    return (_parse_iso_date(value) + timedelta(days=1)).isoformat()


def _exclusive_yfinance_end_date(end_date: Optional[str]) -> Optional[str]:
    """Convert an inclusive requested end date to yfinance's exclusive end date."""
    if not end_date:
        return None
    return (_parse_iso_date(end_date) + timedelta(days=1)).isoformat()


def _today_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _standard_python_value(value: Any) -> Optional[float]:
    if pd.isna(value):
        return None
    return float(value)


def _standard_volume(value: Any) -> Optional[int]:
    if pd.isna(value):
        return None
    return int(value)


def _flatten_columns(frame: pd.DataFrame, ticker: str) -> pd.DataFrame:
    if not isinstance(frame.columns, pd.MultiIndex):
        return frame.copy()

    normalized = normalize_ticker(ticker)
    for level in range(frame.columns.nlevels):
        values = [normalize_ticker(str(value)) for value in frame.columns.get_level_values(level)]
        if normalized in values:
            actual = frame.columns.get_level_values(level)[values.index(normalized)]
            return frame.xs(actual, axis=1, level=level, drop_level=True).copy()

    if frame.columns.nlevels >= 2:
        first_key = frame.columns.get_level_values(1)[0]
        return frame.xs(first_key, axis=1, level=1, drop_level=True).copy()

    return frame.copy()


def _find_column(frame: pd.DataFrame, expected: str) -> Optional[Any]:
    expected_lower = expected.lower()
    for column in frame.columns:
        label = str(column).strip()
        if label.lower() == expected_lower:
            return column
    return None


def _normalize_yfinance_frame(frame: pd.DataFrame, ticker: str) -> pd.DataFrame:
    normalized = _flatten_columns(frame, ticker)
    rename_map: Dict[Any, str] = {}
    for source, target in PRICE_COLUMN_MAP.items():
        column = _find_column(normalized, source)
        if column is not None:
            rename_map[column] = target
    return normalized.rename(columns=rename_map)


def _rows_from_frame(frame: pd.DataFrame, ticker: str, downloaded_at: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    rows: List[Dict[str, Any]] = []
    validation_errors: List[Dict[str, Any]] = []
    normalized = _normalize_yfinance_frame(frame, ticker)

    for index, data in normalized.iterrows():
        trade_date = pd.Timestamp(index).date().isoformat()
        close = _standard_python_value(data.get("close"))
        adjusted_close = _standard_python_value(data.get("adjusted_close"))
        if adjusted_close is None:
            adjusted_close = close

        row = {
            "ticker": ticker,
            "trade_date": trade_date,
            "open": _standard_python_value(data.get("open")),
            "high": _standard_python_value(data.get("high")),
            "low": _standard_python_value(data.get("low")),
            "close": close,
            "adjusted_close": adjusted_close,
            "volume": _standard_volume(data.get("volume")),
            "downloaded_at": downloaded_at,
        }
        is_valid, errors = validate_price_row(row)
        if is_valid:
            rows.append(row)
        else:
            validation_errors.append({"trade_date": trade_date, "errors": errors})

    return rows, validation_errors


def download_price_history(
    ticker: str,
    start_date: str,
    end_date: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Download and validate daily OHLCV rows from yfinance.

    The public end_date argument is inclusive. yfinance treats end dates as
    exclusive, so this function adds one calendar day when calling yfinance.
    """
    normalized = normalize_ticker(ticker)
    if not normalized:
        raise ValueError("ticker is required")

    logger.info("Downloading %s from %s to %s", normalized, start_date, end_date or "latest")
    frame = yf.download(
        normalized,
        start=start_date,
        end=_exclusive_yfinance_end_date(end_date),
        progress=False,
        auto_adjust=YFINANCE_AUTO_ADJUST,
        group_by="column",
    )
    if frame is None or frame.empty:
        return []

    downloaded_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    rows, validation_errors = _rows_from_frame(frame, normalized, downloaded_at)
    for error in validation_errors:
        logger.warning(
            "Skipping invalid price row for %s on %s: %s",
            normalized,
            error["trade_date"],
            "; ".join(error["errors"]),
        )
    return rows


def update_ticker_prices(
    ticker: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> Dict[str, Any]:
    """Download missing prices for one ticker and store them in SQLite."""
    normalized = normalize_ticker(ticker)
    summary: Dict[str, Any] = {
        "ticker": normalized,
        "status": "failed",
        "rows_downloaded": 0,
        "rows_stored": 0,
        "start_date": None,
        "end_date": end_date,
        "error": None,
    }

    if not normalized:
        summary["error"] = "ticker is required"
        return summary

    try:
        latest_date = get_latest_price_date(normalized)
        download_start = _next_calendar_day(latest_date) if latest_date else (
            start_date or DEFAULT_PRICE_HISTORY_START_DATE
        )

        if start_date and latest_date:
            configured_start = _parse_iso_date(start_date)
            next_missing = _parse_iso_date(download_start)
            if configured_start > next_missing:
                download_start = configured_start.isoformat()

        summary["start_date"] = download_start
        effective_end_date = end_date or _today_iso()
        if _parse_iso_date(download_start) > _parse_iso_date(effective_end_date):
            summary["status"] = "already_current"
            summary["end_date"] = end_date
            return summary

        upsert_security(normalized, security_type="Equity", is_active=True)
        rows = download_price_history(normalized, download_start, end_date)
        summary["rows_downloaded"] = len(rows)

        if not rows:
            summary["status"] = "no_data"
            return summary

        summary["rows_stored"] = upsert_daily_prices(rows)
        summary["status"] = "updated" if summary["rows_stored"] else "no_data"
        return summary
    except Exception as exc:
        logger.exception("Failed to update prices for %s", normalized)
        summary["error"] = str(exc)
        return summary


def update_price_universe(
    tickers: List[str],
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    batch_size: Optional[int] = None,
) -> Dict[str, Any]:
    """Update prices for a list of tickers without one failure stopping the run."""
    effective_batch_size = batch_size or DEFAULT_BATCH_SIZE
    summaries: List[Dict[str, Any]] = []
    normalized_tickers = [normalize_ticker(ticker) for ticker in tickers]

    for index, ticker in enumerate(normalized_tickers, start=1):
        logger.info("Updating ticker %s (%s/%s)", ticker, index, len(normalized_tickers))
        summaries.append(update_ticker_prices(ticker, start_date=start_date, end_date=end_date))
        if effective_batch_size > 0 and index % effective_batch_size == 0:
            logger.info("Completed batch ending at ticker %s", ticker)

    return {
        "tickers_requested": len(tickers),
        "updated": sum(1 for item in summaries if item["status"] == "updated"),
        "already_current": sum(1 for item in summaries if item["status"] == "already_current"),
        "no_data": sum(1 for item in summaries if item["status"] == "no_data"),
        "failed": sum(1 for item in summaries if item["status"] == "failed"),
        "rows_downloaded": sum(int(item["rows_downloaded"]) for item in summaries),
        "rows_stored": sum(int(item["rows_stored"]) for item in summaries),
        "results": summaries,
    }

