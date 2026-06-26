"""Repository functions for database access.

Raw SQL should live here so downloaders, CLIs, and future strategy modules read
and write through a consistent database boundary.
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

from database.connection import get_connection

logger = logging.getLogger(__name__)
DatabasePath = Union[str, Path]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _parse_provider_timestamp(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


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


def get_security(ticker: str, database_path: Optional[DatabasePath] = None) -> Optional[Dict[str, Any]]:
    """Return one security row."""
    with get_connection(database_path) as connection:
        row = connection.execute(
            """
            SELECT ticker, company_name, exchange, security_type, is_active, updated_at
            FROM securities
            WHERE ticker = ?
            """,
            (ticker.strip().upper(),),
        ).fetchone()
    return dict(row) if row else None


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


def get_sec_ticker_map_rows(database_path: Optional[DatabasePath] = None) -> List[Dict[str, Any]]:
    """Return cached SEC ticker-map rows in deterministic ticker order."""
    with get_connection(database_path) as connection:
        rows = connection.execute(
            """
            SELECT ticker, cik, title, source, updated_at
            FROM sec_ticker_map
            ORDER BY ticker
            """
        ).fetchall()
    return [dict(row) for row in rows]


def get_ciks_for_tickers(tickers: Sequence[str], database_path: Optional[DatabasePath] = None) -> Dict[str, Optional[str]]:
    """Return best-known CIK values for normalized tickers."""
    normalized = [ticker.strip().upper() for ticker in tickers if ticker and ticker.strip()]
    if not normalized:
        return {}
    placeholders = ", ".join("?" for _ in normalized)
    result = {ticker: None for ticker in normalized}
    with get_connection(database_path) as connection:
        rows = connection.execute(
            f"""
            SELECT normalized_ticker AS ticker, cik
            FROM security_universe
            WHERE normalized_ticker IN ({placeholders})
            """,
            normalized,
        ).fetchall()
        for row in rows:
            if row["cik"]:
                result[row["ticker"]] = str(row["cik"]).zfill(10)
        missing = [ticker for ticker, cik in result.items() if not cik]
        if missing:
            missing_placeholders = ", ".join("?" for _ in missing)
            map_rows = connection.execute(
                f"""
                SELECT ticker, cik
                FROM sec_ticker_map
                WHERE ticker IN ({missing_placeholders})
                """,
                missing,
            ).fetchall()
            for row in map_rows:
                if row["cik"]:
                    result[row["ticker"]] = str(row["cik"]).zfill(10)
    return result


def get_provider_cooldown(
    provider: str,
    key_type: str,
    key_value: str,
    as_of: str,
    database_path: Optional[DatabasePath] = None,
) -> Optional[Dict[str, Any]]:
    """Return an active provider cooldown for a key, if one exists."""
    with get_connection(database_path) as connection:
        row = connection.execute(
            """
            SELECT *
            FROM provider_failure_cooldowns
            WHERE provider = ?
              AND key_type = ?
              AND key_value = ?
              AND (cooldown_until IS NULL OR cooldown_until >= ?)
            ORDER BY last_seen_at DESC
            LIMIT 1
            """,
            (provider, key_type, key_value, as_of),
        ).fetchone()
    return dict(row) if row else None


def upsert_provider_cooldown(
    provider: str,
    key_type: str,
    key_value: str,
    failure_type: str,
    retry_classification: str,
    error_message: str,
    cooldown_until: Optional[str],
    ticker: Optional[str] = None,
    database_path: Optional[DatabasePath] = None,
) -> None:
    """Record or refresh provider failure cooldown state."""
    now = _utc_now_iso()
    with get_connection(database_path) as connection:
        connection.execute(
            """
            INSERT INTO provider_failure_cooldowns (
                provider, key_type, key_value, ticker, failure_type, retry_classification,
                error_message, first_seen_at, last_seen_at, cooldown_until, occurrence_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(provider, key_type, key_value, failure_type) DO UPDATE SET
                ticker = COALESCE(excluded.ticker, provider_failure_cooldowns.ticker),
                retry_classification = excluded.retry_classification,
                error_message = excluded.error_message,
                last_seen_at = excluded.last_seen_at,
                cooldown_until = excluded.cooldown_until,
                occurrence_count = provider_failure_cooldowns.occurrence_count + 1
            """,
            (
                provider,
                key_type,
                key_value,
                ticker,
                failure_type,
                retry_classification,
                (error_message or "")[:300],
                now,
                now,
                cooldown_until,
            ),
        )


def upsert_provider_refresh_success(
    provider: str,
    key_type: str,
    key_value: str,
    http_status: int = 200,
    ticker: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    retrieved_at: Optional[str] = None,
    database_path: Optional[DatabasePath] = None,
) -> None:
    """Persist a successful provider retrieval keyed by issuer/provider identity."""
    now = retrieved_at or _utc_now_iso()
    with get_connection(database_path) as connection:
        connection.execute(
            """
            INSERT INTO provider_refresh_status (
                provider, key_type, key_value, status, http_status, last_success_at,
                last_retrieved_at, response_retrieved_at, ticker, metadata_json
            ) VALUES (?, ?, ?, 'success', ?, ?, ?, ?, ?, ?)
            ON CONFLICT(provider, key_type, key_value) DO UPDATE SET
                status = 'success',
                http_status = excluded.http_status,
                last_success_at = excluded.last_success_at,
                last_retrieved_at = excluded.last_retrieved_at,
                response_retrieved_at = excluded.response_retrieved_at,
                ticker = COALESCE(excluded.ticker, provider_refresh_status.ticker),
                metadata_json = excluded.metadata_json
            """,
            (
                provider,
                key_type,
                key_value,
                int(http_status),
                now,
                now,
                now,
                ticker,
                json.dumps(metadata or {}, sort_keys=True),
            ),
        )


def get_provider_refresh_status(
    provider: str,
    key_type: str,
    key_value: str,
    database_path: Optional[DatabasePath] = None,
) -> Optional[Dict[str, Any]]:
    """Return provider refresh status for a canonical key."""
    with get_connection(database_path) as connection:
        row = connection.execute(
            """
            SELECT *
            FROM provider_refresh_status
            WHERE provider = ? AND key_type = ? AND key_value = ?
            """,
            (provider, key_type, key_value),
        ).fetchone()
    return dict(row) if row else None


def provider_refresh_is_fresh(
    status: Optional[Dict[str, Any]],
    interval_hours: int,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Return freshness decision details for a provider refresh status row."""
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    last_success = _parse_provider_timestamp((status or {}).get("last_success_at"))
    cutoff = current - timedelta(hours=max(0, int(interval_hours)))
    fresh = bool(status and status.get("status") == "success" and last_success and last_success >= cutoff)
    stale_reason = ""
    if not status:
        stale_reason = "missing provider success status"
    elif status.get("status") != "success":
        stale_reason = f"provider status is {status.get('status')}"
    elif last_success is None:
        stale_reason = "missing or invalid last_success_at"
    elif last_success < cutoff:
        stale_reason = "provider success status is stale"
    return {
        "fresh": fresh,
        "last_success_at": last_success.isoformat() if last_success else None,
        "freshness_cutoff": cutoff.isoformat(),
        "stale_reason": stale_reason,
    }


def upsert_security_universe(rows: Sequence[Dict[str, Any]], database_path: Optional[DatabasePath] = None) -> int:
    """Insert or update central security-universe metadata."""
    if not rows:
        return 0
    now = _utc_now_iso()
    parameters = []
    for row in rows:
        parameters.append(
            (
                row["ticker"],
                row["normalized_ticker"],
                row.get("company_name"),
                row.get("cik"),
                row.get("exchange"),
                row.get("security_type"),
                row.get("sector"),
                row.get("industry"),
                1 if row.get("is_active", True) else 0,
                1 if row.get("is_common_stock") else 0,
                1 if row.get("is_adr") else 0,
                1 if row.get("is_etf") else 0,
                1 if row.get("is_etn") else 0,
                1 if row.get("is_reit") else 0,
                1 if row.get("is_financial") else 0,
                1 if row.get("is_warrant") else 0,
                1 if row.get("is_right") else 0,
                1 if row.get("is_unit") else 0,
                1 if row.get("is_preferred") else 0,
                1 if row.get("is_otc") else 0,
                row.get("source"),
                row.get("source_updated_at"),
                row.get("first_seen_at") or now,
                row.get("last_seen_at") or now,
                row.get("delisted_at"),
                row.get("eligibility_status"),
                row.get("eligibility_reasons"),
                json.dumps(row.get("metadata_json", {}), sort_keys=True) if isinstance(row.get("metadata_json"), dict) else row.get("metadata_json"),
            )
        )
    with get_connection(database_path) as connection:
        connection.executemany(
            """
            INSERT INTO security_universe (
                ticker, normalized_ticker, company_name, cik, exchange, security_type, sector, industry,
                is_active, is_common_stock, is_adr, is_etf, is_etn, is_reit, is_financial,
                is_warrant, is_right, is_unit, is_preferred, is_otc, source, source_updated_at,
                first_seen_at, last_seen_at, delisted_at, eligibility_status, eligibility_reasons, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker) DO UPDATE SET
                normalized_ticker=excluded.normalized_ticker,
                company_name=excluded.company_name,
                cik=excluded.cik,
                exchange=excluded.exchange,
                security_type=excluded.security_type,
                sector=excluded.sector,
                industry=excluded.industry,
                is_active=excluded.is_active,
                is_common_stock=excluded.is_common_stock,
                is_adr=excluded.is_adr,
                is_etf=excluded.is_etf,
                is_etn=excluded.is_etn,
                is_reit=excluded.is_reit,
                is_financial=excluded.is_financial,
                is_warrant=excluded.is_warrant,
                is_right=excluded.is_right,
                is_unit=excluded.is_unit,
                is_preferred=excluded.is_preferred,
                is_otc=excluded.is_otc,
                source=excluded.source,
                source_updated_at=excluded.source_updated_at,
                last_seen_at=excluded.last_seen_at,
                delisted_at=excluded.delisted_at,
                eligibility_status=excluded.eligibility_status,
                eligibility_reasons=excluded.eligibility_reasons,
                metadata_json=excluded.metadata_json
            """,
            parameters,
        )
    return len(parameters)


def list_security_universe(
    eligible_only: bool = False,
    exchange: Optional[str] = None,
    security_type: Optional[str] = None,
    limit: Optional[int] = None,
    offset: int = 0,
    sort_by: str = "normalized_ticker",
    descending: bool = False,
    database_path: Optional[DatabasePath] = None,
) -> List[Dict[str, Any]]:
    """List central universe rows with deterministic ordering."""
    allowed_sort = {"ticker", "normalized_ticker", "company_name", "exchange", "security_type", "eligibility_status"}
    order = sort_by if sort_by in allowed_sort else "normalized_ticker"
    query = ["SELECT * FROM security_universe WHERE 1=1"]
    params: List[Any] = []
    if eligible_only:
        query.append("AND eligibility_status = 'eligible'")
    if exchange:
        query.append("AND UPPER(exchange) = ?")
        params.append(exchange.upper())
    if security_type:
        query.append("AND UPPER(security_type) = ?")
        params.append(security_type.upper())
    query.append(f"ORDER BY {order} {'DESC' if descending else 'ASC'}, normalized_ticker ASC")
    if limit is not None:
        query.append("LIMIT ?")
        params.append(int(limit))
    if offset:
        query.append("OFFSET ?")
        params.append(int(offset))
    with get_connection(database_path) as connection:
        rows = connection.execute("\n".join(query), params).fetchall()
    return [dict(row) for row in rows]


def get_active_common_stock_tickers(limit: Optional[int] = None, offset: int = 0, database_path: Optional[DatabasePath] = None) -> List[str]:
    """Return eligible common-stock tickers, preferring Phase 5A universe rows."""
    with get_connection(database_path) as connection:
        universe_exists = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='security_universe'"
        ).fetchone()
        if universe_exists:
            count = connection.execute(
                "SELECT COUNT(*) AS c FROM security_universe WHERE eligibility_status = 'eligible'"
            ).fetchone()["c"]
            if count:
                params: List[Any] = []
                query = [
                    """
                    SELECT normalized_ticker
                    FROM security_universe
                    WHERE eligibility_status = 'eligible'
                    ORDER BY normalized_ticker
                    """
                ]
                if limit is not None:
                    query.append("LIMIT ?")
                    params.append(int(limit))
                if offset:
                    query.append("OFFSET ?")
                    params.append(int(offset))
                rows = connection.execute("\n".join(query), params).fetchall()
                return [row["normalized_ticker"] for row in rows]
        query = [
            """
            SELECT ticker
            FROM securities
            WHERE COALESCE(is_active, 1) = 1
            ORDER BY ticker
            """
        ]
        params = []
        if limit is not None:
            query.append("LIMIT ?")
            params.append(int(limit))
        if offset:
            query.append("OFFSET ?")
            params.append(int(offset))
        rows = connection.execute("\n".join(query), params).fetchall()
    return [row["ticker"] for row in rows]


def security_universe_status(database_path: Optional[DatabasePath] = None) -> Dict[str, Any]:
    """Return aggregate universe counts."""
    with get_connection(database_path) as connection:
        row = connection.execute(
            """
            SELECT
                COUNT(*) AS total_securities,
                SUM(CASE WHEN is_active = 1 THEN 1 ELSE 0 END) AS active_securities,
                SUM(CASE WHEN is_common_stock = 1 THEN 1 ELSE 0 END) AS common_stocks,
                SUM(CASE WHEN eligibility_status = 'eligible' THEN 1 ELSE 0 END) AS eligible_graham_securities,
                SUM(CASE WHEN eligibility_reasons LIKE '%exchange%' THEN 1 ELSE 0 END) AS excluded_by_exchange,
                SUM(CASE WHEN is_financial = 1 THEN 1 ELSE 0 END) AS excluded_financials,
                SUM(CASE WHEN is_reit = 1 THEN 1 ELSE 0 END) AS excluded_reits,
                SUM(CASE WHEN cik IS NULL OR cik = '' THEN 1 ELSE 0 END) AS missing_cik,
                SUM(CASE WHEN exchange IS NULL OR exchange = '' THEN 1 ELSE 0 END) AS missing_exchange,
                MAX(last_seen_at) AS last_update_timestamp
            FROM security_universe
            """
        ).fetchone()
        reason_rows = connection.execute("SELECT eligibility_reasons FROM security_universe WHERE eligibility_reasons IS NOT NULL").fetchall()
    reasons: Dict[str, int] = {}
    excluded_type_count = 0
    for item in reason_rows:
        for reason in (item["eligibility_reasons"] or "").split(";"):
            reason = reason.strip()
            if reason:
                reasons[reason] = reasons.get(reason, 0) + 1
                if reason not in {"financial", "reit", "exchange", "missing_cik", "invalid_ticker"}:
                    excluded_type_count += 1
    return {
        "total_securities": int(row["total_securities"] or 0),
        "active_securities": int(row["active_securities"] or 0),
        "common_stocks": int(row["common_stocks"] or 0),
        "eligible_graham_securities": int(row["eligible_graham_securities"] or 0),
        "excluded_by_security_type": excluded_type_count,
        "excluded_by_exchange": int(row["excluded_by_exchange"] or 0),
        "excluded_financials": int(row["excluded_financials"] or 0),
        "excluded_reits": int(row["excluded_reits"] or 0),
        "missing_cik": int(row["missing_cik"] or 0),
        "missing_exchange": int(row["missing_exchange"] or 0),
        "invalid_tickers": reasons.get("invalid_ticker", 0),
        "last_update_timestamp": row["last_update_timestamp"],
        "exclusion_reasons": reasons,
    }


def create_ingestion_run(run_type: str, requested_count: int, configuration: Dict[str, Any], database_path: Optional[DatabasePath] = None) -> int:
    with get_connection(database_path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO ingestion_runs (run_type, started_at, requested_count, configuration_json, status)
            VALUES (?, ?, ?, ?, 'running')
            """,
            (run_type, _utc_now_iso(), requested_count, json.dumps(configuration, sort_keys=True)),
        )
        return int(cursor.lastrowid)


def add_ingestion_run_item(run_id: int, item: Dict[str, Any], database_path: Optional[DatabasePath] = None) -> None:
    with get_connection(database_path) as connection:
        connection.execute(
            """
            INSERT INTO ingestion_run_items (
                run_id, ticker, status, inserted_count, updated_count, unchanged_count, skipped_count,
                retry_count, error_type, error_message, started_at, completed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                item["ticker"],
                item["status"],
                int(item.get("inserted_count", 0)),
                int(item.get("updated_count", 0)),
                int(item.get("unchanged_count", 0)),
                int(item.get("skipped_count", 0)),
                int(item.get("retry_count", 0)),
                item.get("error_type"),
                item.get("error_message"),
                item.get("started_at"),
                item.get("completed_at"),
            ),
        )


def complete_ingestion_run(run_id: int, status: str, database_path: Optional[DatabasePath] = None) -> None:
    with get_connection(database_path) as connection:
        counts = connection.execute(
            """
            SELECT
                SUM(CASE WHEN status = 'succeeded' THEN 1 ELSE 0 END) AS succeeded,
                SUM(CASE WHEN status = 'partial' THEN 1 ELSE 0 END) AS partial,
                SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed
            FROM ingestion_run_items
            WHERE run_id = ?
            """,
            (run_id,),
        ).fetchone()
        connection.execute(
            """
            UPDATE ingestion_runs
            SET completed_at = ?, succeeded_count = ?, partial_count = ?, failed_count = ?, status = ?
            WHERE run_id = ?
            """,
            (_utc_now_iso(), int(counts["succeeded"] or 0), int(counts["partial"] or 0), int(counts["failed"] or 0), status, run_id),
        )


def get_ingestion_run_items(run_id: int, statuses: Optional[Sequence[str]] = None, database_path: Optional[DatabasePath] = None) -> List[Dict[str, Any]]:
    query = ["SELECT * FROM ingestion_run_items WHERE run_id = ?"]
    params: List[Any] = [run_id]
    if statuses:
        query.append("AND status IN (" + ", ".join("?" for _ in statuses) + ")")
        params.extend(statuses)
    query.append("ORDER BY ticker")
    with get_connection(database_path) as connection:
        rows = connection.execute("\n".join(query), params).fetchall()
    return [dict(row) for row in rows]


def price_freshness(tickers: Sequence[str], database_path: Optional[DatabasePath] = None) -> Dict[str, Optional[str]]:
    return {ticker: get_latest_price_date(ticker, database_path=database_path) for ticker in tickers}


def fundamentals_freshness(tickers: Sequence[str], database_path: Optional[DatabasePath] = None) -> Dict[str, Dict[str, Optional[str]]]:
    normalized = [ticker.strip().upper() for ticker in tickers if ticker and ticker.strip()]
    if not normalized:
        return {}
    placeholders = ", ".join("?" for _ in normalized)
    with get_connection(database_path) as connection:
        rows = connection.execute(
            f"""
            SELECT ticker, MAX(accepted_at) AS latest_accepted_at, MAX(report_period) AS latest_report_period
            FROM fundamental_filings
            WHERE ticker IN ({placeholders})
            GROUP BY ticker
            """,
            normalized,
        ).fetchall()
    result = {ticker: {"latest_accepted_at": None, "latest_report_period": None} for ticker in normalized}
    for row in rows:
        result[row["ticker"]] = {
            "latest_accepted_at": row["latest_accepted_at"],
            "latest_report_period": row["latest_report_period"],
        }
    return result


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

def create_backtest_run(
    strategy: str,
    start_date: str,
    end_date: str,
    starting_capital: float,
    parameters_json: str,
    database_path: Optional[DatabasePath] = None,
) -> int:
    """Create a persisted backtest run and return its ID."""
    with get_connection(database_path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO backtest_runs (
                strategy, start_date, end_date, starting_capital, ending_capital, parameters_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (strategy, start_date, end_date, starting_capital, None, parameters_json, _utc_now_iso()),
        )
        backtest_id = int(cursor.lastrowid)
    return backtest_id


def complete_backtest_run(
    backtest_id: int,
    ending_capital: float,
    database_path: Optional[DatabasePath] = None,
) -> None:
    """Set ending capital for a persisted backtest run."""
    with get_connection(database_path) as connection:
        connection.execute(
            "UPDATE backtest_runs SET ending_capital = ? WHERE backtest_id = ?",
            (ending_capital, backtest_id),
        )


def insert_backtest_trades(
    backtest_id: int,
    trades: Sequence[Any],
    database_path: Optional[DatabasePath] = None,
) -> int:
    """Persist completed backtest trades."""
    parameters = [
        (
            backtest_id,
            trade.ticker,
            trade.signal_date,
            trade.entry_date,
            trade.entry_price,
            trade.exit_date,
            trade.exit_price,
            trade.quantity,
            trade.net_pnl,
            trade.return_pct,
            trade.exit_reason,
        )
        for trade in trades
    ]
    if not parameters:
        return 0
    with get_connection(database_path) as connection:
        connection.executemany(
            """
            INSERT INTO backtest_trades (
                backtest_id, ticker, signal_date, entry_date, entry_price, exit_date,
                exit_price, quantity, pnl, return_pct, exit_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            parameters,
        )
    return len(parameters)


def insert_portfolio_snapshots(
    backtest_id: int,
    snapshots: Sequence[Any],
    database_path: Optional[DatabasePath] = None,
) -> int:
    """Persist end-of-day portfolio snapshots."""
    parameters = [
        (
            backtest_id,
            snapshot.snapshot_date,
            snapshot.cash,
            snapshot.holdings_value,
            snapshot.total_value,
            snapshot.drawdown,
        )
        for snapshot in snapshots
    ]
    if not parameters:
        return 0
    with get_connection(database_path) as connection:
        connection.executemany(
            """
            INSERT INTO portfolio_snapshots (
                backtest_id, snapshot_date, cash, holdings_value, total_value, drawdown
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            parameters,
        )
    return len(parameters)


def get_backtest_run(backtest_id: int, database_path: Optional[DatabasePath] = None) -> Optional[Dict[str, Any]]:
    """Retrieve one saved backtest run."""
    with get_connection(database_path) as connection:
        row = connection.execute(
            """
            SELECT backtest_id, strategy, start_date, end_date, starting_capital,
                   ending_capital, parameters_json, created_at
            FROM backtest_runs
            WHERE backtest_id = ?
            """,
            (backtest_id,),
        ).fetchone()
    return dict(row) if row else None


def get_backtest_trades(backtest_id: int, database_path: Optional[DatabasePath] = None) -> List[Dict[str, Any]]:
    """Retrieve trades for a saved backtest run."""
    with get_connection(database_path) as connection:
        rows = connection.execute(
            """
            SELECT trade_id, backtest_id, ticker, signal_date, entry_date, entry_price,
                   exit_date, exit_price, quantity, pnl, return_pct, exit_reason
            FROM backtest_trades
            WHERE backtest_id = ?
            ORDER BY trade_id
            """,
            (backtest_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_portfolio_snapshots(backtest_id: int, database_path: Optional[DatabasePath] = None) -> List[Dict[str, Any]]:
    """Retrieve portfolio snapshots for a saved backtest run."""
    with get_connection(database_path) as connection:
        rows = connection.execute(
            """
            SELECT backtest_id, snapshot_date, cash, holdings_value, total_value, drawdown
            FROM portfolio_snapshots
            WHERE backtest_id = ?
            ORDER BY snapshot_date
            """,
            (backtest_id,),
        ).fetchall()
    return [dict(row) for row in rows]

def list_backtest_runs(
    strategy: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: Optional[int] = None,
    database_path: Optional[DatabasePath] = None,
) -> List[Dict[str, Any]]:
    """List saved backtest runs with optional filters."""
    query = [
        """
        SELECT backtest_id, strategy, start_date, end_date, starting_capital,
               ending_capital, parameters_json, created_at
        FROM backtest_runs
        WHERE 1 = 1
        """
    ]
    parameters: List[Any] = []
    if strategy:
        query.append("AND strategy = ?")
        parameters.append(strategy)
    if start_date:
        query.append("AND start_date >= ?")
        parameters.append(start_date)
    if end_date:
        query.append("AND end_date <= ?")
        parameters.append(end_date)
    query.append("ORDER BY backtest_id")
    if limit is not None:
        if limit <= 0:
            raise ValueError("limit must be positive")
        query.append("LIMIT ?")
        parameters.append(limit)
    with get_connection(database_path) as connection:
        rows = connection.execute("\n".join(query), parameters).fetchall()
    return [dict(row) for row in rows]


def get_backtest_bundle(backtest_id: int, database_path: Optional[DatabasePath] = None) -> Dict[str, Any]:
    """Return a saved run, trades, snapshots, and parsed parameters_json."""
    import json

    run = get_backtest_run(backtest_id, database_path=database_path)
    if run is None:
        raise ValueError(f"Backtest {backtest_id} not found")
    parameters = json.loads(run["parameters_json"]) if run.get("parameters_json") else {}
    return {
        "run": run,
        "trades": get_backtest_trades(backtest_id, database_path=database_path),
        "snapshots": get_portfolio_snapshots(backtest_id, database_path=database_path),
        "parameters": parameters,
    }


