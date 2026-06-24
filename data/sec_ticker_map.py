"""SEC ticker-to-CIK mapping helpers."""

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from data.sec_client import SECClient
from database.connection import get_connection


class CIKMappingError(LookupError):
    """Raised when a ticker cannot be mapped to a CIK."""


def normalize_ticker(ticker: str) -> str:
    """Normalize a ticker symbol for local and SEC lookup."""
    normalized = ticker.strip().upper().replace("-", ".")
    if not normalized:
        raise ValueError("ticker is required")
    return normalized


def normalize_cik(cik: Any) -> str:
    """Normalize CIK to SEC's zero-padded 10-digit string."""
    digits = "".join(ch for ch in str(cik).strip() if ch.isdigit())
    if not digits or len(digits) > 10:
        raise ValueError(f"invalid CIK: {cik}")
    return digits.zfill(10)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _mapping_rows(payload: Any) -> List[Dict[str, str]]:
    if isinstance(payload, dict):
        values: Iterable[Any] = payload.values()
    elif isinstance(payload, list):
        values = payload
    else:
        values = []
    rows = []
    for item in values:
        if not isinstance(item, dict):
            continue
        ticker = item.get("ticker") or item.get("symbol")
        cik = item.get("cik_str") or item.get("cik")
        if ticker is None or cik is None:
            continue
        rows.append(
            {
                "ticker": normalize_ticker(str(ticker)),
                "cik": normalize_cik(cik),
                "title": item.get("title") or item.get("name"),
            }
        )
    return rows


def cache_ticker_map(rows: List[Dict[str, str]], source: str = "SEC company_tickers", database_path=None) -> int:
    """Persist ticker/CIK mapping rows locally."""
    if not rows:
        return 0
    now = _utc_now_iso()
    params = [(row["ticker"], row["cik"], row.get("title"), source, now) for row in rows]
    with get_connection(database_path) as connection:
        connection.executemany(
            """
            INSERT INTO sec_ticker_map (ticker, cik, title, source, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(ticker) DO UPDATE SET
                cik = excluded.cik,
                title = excluded.title,
                source = excluded.source,
                updated_at = excluded.updated_at
            """,
            params,
        )
    return len(params)


def load_sec_ticker_map(client: Optional[SECClient] = None, database_path=None) -> List[Dict[str, str]]:
    """Download and cache SEC's ticker map."""
    sec_client = client or SECClient()
    rows = _mapping_rows(sec_client.get_company_tickers())
    cache_ticker_map(rows, database_path=database_path)
    return rows


def get_cik_for_ticker(ticker: str, database_path=None, client: Optional[SECClient] = None, refresh: bool = True) -> str:
    """Return a CIK for ticker, refreshing the local SEC map if needed."""
    normalized = normalize_ticker(ticker)
    with get_connection(database_path) as connection:
        row = connection.execute(
            "SELECT cik FROM sec_ticker_map WHERE ticker = ?",
            (normalized,),
        ).fetchone()
    if row:
        return row["cik"]
    if refresh:
        load_sec_ticker_map(client=client, database_path=database_path)
        with get_connection(database_path) as connection:
            row = connection.execute(
                "SELECT cik FROM sec_ticker_map WHERE ticker = ?",
                (normalized,),
            ).fetchone()
        if row:
            return row["cik"]
    raise CIKMappingError(f"No SEC CIK mapping found for ticker {normalized}")
