"""Stored-data readiness and resumable preparation helpers."""

import csv
import json
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from config import settings
from data.market_data import update_price_universe, update_ticker_prices
from data.universe import normalize_ticker_value
from data.sec_ticker_map import normalize_cik
from database.connection import get_connection
from database import repositories
from fundamentals.service import update_fundamentals_for_ticker


READY = "READY"
PRICE_MISSING = "PRICE_MISSING"
PRICE_HISTORY_INSUFFICIENT = "PRICE_HISTORY_INSUFFICIENT"
FUNDAMENTALS_MISSING = "FUNDAMENTALS_MISSING"
FUNDAMENTALS_NOT_NORMALIZED = "FUNDAMENTALS_NOT_NORMALIZED"
REQUIRED_GRAHAM_FIELDS_MISSING = "REQUIRED_GRAHAM_FIELDS_MISSING"
UNSUPPORTED_SECURITY = "UNSUPPORTED_SECURITY"
INELIGIBLE_SECURITY = "INELIGIBLE_SECURITY"
UNRESOLVED_TICKER = "UNRESOLVED_TICKER"
OTHER_EXPLICIT_ERROR = "OTHER_EXPLICIT_ERROR"

RETRYABLE_NOW = "retryable now"
RETRYABLE_NEXT_TRADING_DAY = "retryable next trading day"
RETRYABLE_AFTER_FUNDAMENTALS_REFRESH_INTERVAL = "retryable after fundamentals refresh interval"
PERMANENT_OR_UNSUPPORTED = "permanent or unsupported"
MANUAL_REVIEW_REQUIRED = "manual review required"

STAGES = (
    "SECURITY_RESOLVED",
    "PRICE_UPDATE_COMPLETE",
    "SEC_INGESTION_COMPLETE",
    "NORMALIZATION_COMPLETE",
    "READINESS_VERIFIED",
)

EPS_FIELDS = {"diluted_eps", "basic_eps"}
SHARE_FIELDS = {"shares_outstanding", "weighted_average_diluted_shares", "weighted_average_basic_shares"}


@dataclass(frozen=True)
class ReadinessRow:
    original_symbol: str
    normalized_symbol: str
    security_resolution_status: str
    eligibility_status: str
    security_type: Optional[str]
    exchange: Optional[str]
    is_active: Optional[bool]
    earliest_price_date: Optional[str]
    latest_price_date: Optional[str]
    price_row_count: int
    price_ready: bool
    price_readiness_reason: str
    sec_filing_count: int
    earliest_accepted_at: Optional[str]
    latest_accepted_at: Optional[str]
    normalized_fundamental_field_count: int
    required_graham_fields_available: str
    graham_evaluable: bool
    graham_evaluability_reason: str
    technical_evaluable: bool
    technical_evaluability_reason: str
    combined_evaluable: bool
    final_readiness_category: str


@dataclass(frozen=True)
class Reconciliation:
    requested_count: int
    evaluated_count: int
    explicit_exclusion_count: int
    explicit_missing_data_count: int
    explicit_invalid_count: int
    unexplained_count: int
    invariant_holds: bool


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _minus_years(value: str, years: int) -> str:
    parsed = date.fromisoformat(value)
    try:
        return parsed.replace(year=parsed.year - years).isoformat()
    except ValueError:
        return parsed.replace(year=parsed.year - years, day=28).isoformat()


def _expected_trade_date(value: str) -> str:
    parsed = date.fromisoformat(value)
    today = date.today()
    if parsed >= today:
        parsed = today.fromordinal(today.toordinal() - 1)
    while parsed.weekday() >= 5:
        parsed = date.fromordinal(parsed.toordinal() - 1)
    return parsed.isoformat()


def _next_trading_day(value: str) -> str:
    parsed = date.fromisoformat(value) + timedelta(days=1)
    while parsed.weekday() >= 5:
        parsed += timedelta(days=1)
    return parsed.isoformat()


def _after_fundamentals_refresh_interval(value: str) -> str:
    return (date.fromisoformat(value) + timedelta(days=7)).isoformat()


def _classify_provider_failure(stage: str, message: str, category: str = "") -> str:
    text = f"{category} {message}".lower()
    if "unsupported" in text or "unmapped" in text or "404" in text or "not found" in text or "possibly delisted" in text:
        return PERMANENT_OR_UNSUPPORTED
    if "no_data" in text or "no data" in text or "no price data" in text or "empty" in text:
        return RETRYABLE_NEXT_TRADING_DAY if stage == "PRICE_UPDATE_COMPLETE" else RETRYABLE_AFTER_FUNDAMENTALS_REFRESH_INTERVAL
    if "sec_user_agent" in text or "configuration" in text:
        return MANUAL_REVIEW_REQUIRED
    if "rate" in text or "timeout" in text or "429" in text or "500" in text or "502" in text or "503" in text or "504" in text:
        return RETRYABLE_NOW
    return MANUAL_REVIEW_REQUIRED


def _cooldown_until_for(classification: str, as_of: str) -> Optional[str]:
    if classification == RETRYABLE_NEXT_TRADING_DAY:
        return _next_trading_day(as_of)
    if classification == RETRYABLE_AFTER_FUNDAMENTALS_REFRESH_INTERVAL:
        return _after_fundamentals_refresh_interval(as_of)
    if classification == PERMANENT_OR_UNSUPPORTED:
        return "9999-12-31"
    return None


def _failure_type_for(message: str, default: str) -> str:
    text = (message or "").lower()
    if "404" in text or "not found" in text:
        return "http_404"
    if "no_data" in text or "no data" in text or "no price data" in text:
        return "no_data"
    if "possibly delisted" in text:
        return "provider_no_data"
    return default


def read_input_symbols(path: str) -> List[str]:
    symbols = []
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        symbols.append(value)
    return symbols


def normalize_requested_symbols(symbols: Iterable[str]) -> List[Tuple[str, str]]:
    seen = set()
    rows: List[Tuple[str, str]] = []
    for symbol in symbols:
        normalized = normalize_ticker_value(symbol)
        if not normalized:
            continue
        if normalized in seen:
            continue
        rows.append((symbol.strip(), normalized))
        seen.add(normalized)
    return rows


def _security_rows(tickers: Sequence[str], database_path: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    if not tickers:
        return {}
    placeholders = ", ".join("?" for _ in tickers)
    with get_connection(database_path) as connection:
        rows = connection.execute(
            f"SELECT * FROM security_universe WHERE normalized_ticker IN ({placeholders})",
            list(tickers),
        ).fetchall()
        result = {row["normalized_ticker"]: dict(row) for row in rows}
        missing = [ticker for ticker in tickers if ticker not in result]
        if missing:
            security_placeholders = ", ".join("?" for _ in missing)
            securities = connection.execute(
                f"""
                SELECT s.ticker, s.ticker AS normalized_ticker, s.company_name, m.cik, s.exchange, s.security_type,
                       s.is_active, 'eligible' AS eligibility_status, '' AS eligibility_reasons
                FROM securities AS s
                LEFT JOIN sec_ticker_map AS m ON m.ticker = s.ticker
                WHERE s.ticker IN ({security_placeholders})
                """,
                missing,
            ).fetchall()
            for row in securities:
                result[row["normalized_ticker"]] = dict(row)
        still_missing = [ticker for ticker in tickers if ticker not in result]
        if still_missing:
            map_placeholders = ", ".join("?" for _ in still_missing)
            mapped = connection.execute(
                f"""
                SELECT ticker, ticker AS normalized_ticker, title AS company_name, cik, NULL AS exchange,
                       'SEC reporting common equity' AS security_type, 1 AS is_active,
                       'eligible' AS eligibility_status, '' AS eligibility_reasons
                FROM sec_ticker_map
                WHERE ticker IN ({map_placeholders})
                """,
                still_missing,
            ).fetchall()
            for row in mapped:
                result[row["normalized_ticker"]] = dict(row)
    return result


def _price_rows(tickers: Sequence[str], as_of: str, database_path: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    if not tickers:
        return {}
    placeholders = ", ".join("?" for _ in tickers)
    with get_connection(database_path) as connection:
        rows = connection.execute(
            f"""
            SELECT ticker, COUNT(*) AS row_count, MIN(trade_date) AS earliest_price_date, MAX(trade_date) AS latest_price_date
            FROM daily_prices
            WHERE ticker IN ({placeholders}) AND trade_date <= ?
            GROUP BY ticker
            """,
            list(tickers) + [as_of],
        ).fetchall()
    return {row["ticker"]: dict(row) for row in rows}


def _fundamental_rows(tickers: Sequence[str], as_of: str, database_path: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    result = {
        ticker: {
            "sec_filing_count": 0,
            "earliest_accepted_at": None,
            "latest_accepted_at": None,
            "fields": set(),
        }
        for ticker in tickers
    }
    if not tickers:
        return result
    placeholders = ", ".join("?" for _ in tickers)
    with get_connection(database_path) as connection:
        filings = connection.execute(
            f"""
            SELECT ticker, COUNT(*) AS filing_count, MIN(COALESCE(accepted_at, filing_date)) AS earliest_accepted_at,
                   MAX(COALESCE(accepted_at, filing_date)) AS latest_accepted_at
            FROM fundamental_filings
            WHERE ticker IN ({placeholders}) AND COALESCE(accepted_at, filing_date) <= ?
            GROUP BY ticker
            """,
            list(tickers) + [as_of],
        ).fetchall()
        facts = connection.execute(
            f"""
            SELECT ticker, standardized_field
            FROM fundamental_facts
            WHERE ticker IN ({placeholders}) AND standardized_field IS NOT NULL AND COALESCE(accepted_at, filed_date) <= ?
            GROUP BY ticker, standardized_field
            ORDER BY ticker, standardized_field
            """,
            list(tickers) + [as_of],
        ).fetchall()
    for row in filings:
        result[row["ticker"]].update(
            {
                "sec_filing_count": int(row["filing_count"] or 0),
                "earliest_accepted_at": row["earliest_accepted_at"],
                "latest_accepted_at": row["latest_accepted_at"],
            }
        )
    for row in facts:
        result[row["ticker"]]["fields"].add(row["standardized_field"])
    return result


def _price_ready(price: Dict[str, Any], as_of: str, price_years: int) -> Tuple[bool, str]:
    if not price or not price.get("row_count"):
        return False, "no stored daily prices"
    minimum_start = _minus_years(as_of, price_years)
    if not price.get("earliest_price_date") or price["earliest_price_date"] > minimum_start:
        return False, f"stored prices start after required {minimum_start}"
    expected_latest = _expected_trade_date(as_of)
    if not price.get("latest_price_date") or price["latest_price_date"] < expected_latest:
        return False, f"latest stored price is before expected {expected_latest}"
    if price["latest_price_date"] > as_of:
        return False, f"latest stored price is after as-of {as_of}"
    return True, "stored price history covers requested window"


def _technical_ready(price: Dict[str, Any]) -> Tuple[bool, str]:
    count = int((price or {}).get("row_count") or 0)
    if count >= 21:
        return True, "at least 21 stored price rows"
    if count == 0:
        return False, "no stored daily prices"
    return False, "fewer than 21 stored price rows"


def _graham_ready(fundamentals: Dict[str, Any]) -> Tuple[bool, str, List[str]]:
    fields = set(fundamentals.get("fields") or set())
    if int(fundamentals.get("sec_filing_count") or 0) == 0:
        return False, "no SEC filings available", []
    if not fields:
        return False, "SEC filings are not normalized into supported fields", []
    missing = []
    if not fields.intersection(EPS_FIELDS):
        missing.append("diluted_eps_or_basic_eps")
    if not fields.intersection(SHARE_FIELDS):
        missing.append("shares_outstanding_or_weighted_average_shares")
    if missing:
        return False, "missing Graham evaluability fields: " + ";".join(sorted(set(missing))), sorted(set(missing))
    return True, "Graham strategy can evaluate stored price, EPS, shares, and filing facts", []


def build_readiness_report(
    symbols: Sequence[str],
    as_of: str,
    price_years: int = 6,
    database_path: Optional[str] = None,
) -> Dict[str, Any]:
    requested = normalize_requested_symbols(symbols)
    tickers = [normalized for _, normalized in requested]
    securities = _security_rows(tickers, database_path=database_path)
    prices = _price_rows(tickers, as_of, database_path=database_path)
    fundamentals = _fundamental_rows(tickers, as_of, database_path=database_path)
    rows: List[ReadinessRow] = []
    for original, ticker in requested:
        security = securities.get(ticker)
        price = prices.get(ticker, {})
        fundamental = fundamentals.get(ticker, {})
        fields = sorted(fundamental.get("fields") or [])
        price_ok, price_reason = _price_ready(price, as_of, price_years)
        tech_ok, tech_reason = _technical_ready(price)
        graham_ok, graham_reason, missing = _graham_ready(fundamental)
        if security is None:
            category = UNRESOLVED_TICKER
            resolution = "unresolved"
            eligibility = "unknown"
        elif security.get("eligibility_status") != "eligible":
            category = UNSUPPORTED_SECURITY if security.get("eligibility_reasons") else INELIGIBLE_SECURITY
            resolution = "resolved"
            eligibility = security.get("eligibility_status") or "unknown"
        elif not price.get("row_count"):
            category = PRICE_MISSING
            resolution = "resolved"
            eligibility = "eligible"
        elif not price_ok:
            category = PRICE_HISTORY_INSUFFICIENT
            resolution = "resolved"
            eligibility = "eligible"
        elif not fundamental.get("sec_filing_count"):
            category = FUNDAMENTALS_MISSING
            resolution = "resolved"
            eligibility = "eligible"
        elif not fields:
            category = FUNDAMENTALS_NOT_NORMALIZED
            resolution = "resolved"
            eligibility = "eligible"
        elif missing:
            category = REQUIRED_GRAHAM_FIELDS_MISSING
            resolution = "resolved"
            eligibility = "eligible"
        else:
            category = READY
            resolution = "resolved"
            eligibility = "eligible"
        rows.append(
            ReadinessRow(
                original,
                ticker,
                resolution,
                eligibility,
                security.get("security_type") if security else None,
                security.get("exchange") if security else None,
                bool(security.get("is_active")) if security and security.get("is_active") is not None else None,
                price.get("earliest_price_date"),
                price.get("latest_price_date"),
                int(price.get("row_count") or 0),
                price_ok,
                price_reason,
                int(fundamental.get("sec_filing_count") or 0),
                fundamental.get("earliest_accepted_at"),
                fundamental.get("latest_accepted_at"),
                len(fields),
                ";".join(fields),
                graham_ok and price_ok,
                graham_reason,
                tech_ok,
                tech_reason,
                graham_ok and tech_ok and price_ok,
                category,
            )
        )
    row_dicts = [asdict(row) for row in sorted(rows, key=lambda item: item.normalized_symbol)]
    summary = summarize_readiness(row_dicts)
    return {
        "as_of": as_of,
        "price_years": price_years,
        "requested_count": len(symbols),
        "unique_normalized_count": len(requested),
        "rows": row_dicts,
        "summary": summary,
        "reconciliation": asdict(reconcile_readiness(row_dicts)),
    }


def summarize_readiness(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    categories: Dict[str, int] = {}
    for row in rows:
        category = row["final_readiness_category"]
        categories[category] = categories.get(category, 0) + 1
    return {
        "requested_unique": len(rows),
        "ready": sum(1 for row in rows if row["final_readiness_category"] == READY),
        "price_ready": sum(1 for row in rows if row["price_ready"]),
        "fundamental_ready": sum(1 for row in rows if row["graham_evaluable"]),
        "graham_evaluable": sum(1 for row in rows if row["graham_evaluable"]),
        "technical_evaluable": sum(1 for row in rows if row["technical_evaluable"]),
        "combined_evaluable": sum(1 for row in rows if row["combined_evaluable"]),
        "categories": dict(sorted(categories.items())),
    }


def reconcile_readiness(rows: Sequence[Dict[str, Any]]) -> Reconciliation:
    evaluated = sum(1 for row in rows if row["combined_evaluable"])
    explicit_exclusion = sum(1 for row in rows if row["final_readiness_category"] in {UNSUPPORTED_SECURITY, INELIGIBLE_SECURITY})
    explicit_invalid = sum(1 for row in rows if row["final_readiness_category"] == UNRESOLVED_TICKER)
    explicit_missing = sum(
        1
        for row in rows
        if row["final_readiness_category"]
        in {PRICE_MISSING, PRICE_HISTORY_INSUFFICIENT, FUNDAMENTALS_MISSING, FUNDAMENTALS_NOT_NORMALIZED, REQUIRED_GRAHAM_FIELDS_MISSING}
    )
    requested = len(rows)
    unexplained = requested - evaluated - explicit_exclusion - explicit_missing - explicit_invalid
    return Reconciliation(requested, evaluated, explicit_exclusion, explicit_missing, explicit_invalid, unexplained, unexplained == 0)


def export_readiness_report(payload: Dict[str, Any], export_dir: str) -> Dict[str, str]:
    directory = Path(export_dir)
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / f"data-readiness-{payload['as_of']}.json"
    csv_path = directory / f"data-readiness-{payload['as_of']}.csv"
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    rows = payload["rows"]
    if rows:
        with csv_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
    else:
        csv_path.write_text("", encoding="utf-8")
    return {"json": str(json_path), "csv": str(csv_path)}


def _stage_item(ticker: str, stage: str, status: str, reason: str = "", attempts: int = 0) -> Dict[str, Any]:
    return {
        "ticker": ticker,
        "stage": stage,
        "status": status,
        "reason": reason,
        "attempt_count": attempts,
        "timestamp": _now_iso(),
    }


def prepare_universe_data(
    symbols: Sequence[str],
    price_years: int,
    fundamental_years: int,
    as_of: Optional[str] = None,
    refresh_normalization: bool = False,
    resume: bool = False,
    export_dir: Optional[str] = None,
    price_batch_size: Optional[int] = None,
    fundamental_batch_size: Optional[int] = None,
    skip_fundamentals: bool = False,
    price_worker: Callable[[str, Optional[str], Optional[str]], Dict[str, Any]] = update_ticker_prices,
    fundamental_worker: Callable[[str, Optional[int]], Dict[str, Any]] = update_fundamentals_for_ticker,
    database_path: Optional[str] = None,
    force_provider_refresh: bool = False,
) -> Dict[str, Any]:
    effective_as_of = as_of or date.today().isoformat()
    started = _now_iso()
    before = build_readiness_report(symbols, effective_as_of, price_years, database_path=database_path)
    stage_items: List[Dict[str, Any]] = []
    failures: List[Dict[str, Any]] = []
    price_update_results: Dict[str, Any] = {
        "updated": 0,
        "already_current": 0,
        "no_data": 0,
        "failed": 0,
        "rows_downloaded": 0,
        "rows_stored": 0,
        "requests": 0,
        "cooldown_skipped": 0,
        "results": [],
    }
    sec_update_results: Dict[str, Any] = {
        "unique_ciks_considered": 0,
        "unique_ciks_requested": 0,
        "unique_ciks_refreshed": 0,
        "success_records_written": 0,
        "success_freshness_skipped": 0,
        "success_missing_status": 0,
        "success_status_stale": 0,
        "normalization_incomplete_after_success": 0,
        "requests": 0,
        "duplicate_cik_skipped": 0,
        "cooldown_skipped": 0,
        "cooldown_failures_recorded": 0,
        "diagnostics": [],
    }
    requested = normalize_requested_symbols(symbols)
    before_rows = {row["normalized_symbol"]: row for row in before["rows"]}
    price_candidates: List[str] = []
    for row in before["rows"]:
        ticker = row["normalized_symbol"]
        if row["eligibility_status"] == "excluded":
            stage_items.append(_stage_item(ticker, "SECURITY_RESOLVED", "failed", row["final_readiness_category"]))
            failures.append(_failure(ticker, "SECURITY_RESOLVED", "unsupported_ticker", "ReadinessError", row["final_readiness_category"], 1, False))
            continue
        stage_items.append(_stage_item(ticker, "SECURITY_RESOLVED", "skipped" if resume else "succeeded", "already resolved"))
        if row["price_ready"]:
            stage_items.append(_stage_item(ticker, "PRICE_UPDATE_COMPLETE", "skipped", "already price-ready"))
        elif row.get("latest_price_date") and row["latest_price_date"] >= _expected_trade_date(effective_as_of):
            stage_items.append(_stage_item(ticker, "PRICE_UPDATE_COMPLETE", "skipped", "latest completed trading-day bar already stored"))
        else:
            cooldown = None if force_provider_refresh else repositories.get_provider_cooldown(
                "yfinance",
                "ticker",
                ticker,
                effective_as_of,
                database_path=database_path,
            )
            if cooldown:
                price_update_results["cooldown_skipped"] += 1
                stage_items.append(_stage_item(ticker, "PRICE_UPDATE_COMPLETE", "skipped", f"cooldown: {cooldown['retry_classification']}"))
                failures.append(
                    _failure(
                        ticker,
                        "PRICE_UPDATE_COMPLETE",
                        "provider_cooldown",
                        "ProviderCooldown",
                        cooldown.get("error_message") or cooldown["failure_type"],
                        0,
                        cooldown["retry_classification"] in {RETRYABLE_NOW, RETRYABLE_NEXT_TRADING_DAY},
                        cooldown["retry_classification"],
                    )
                )
            else:
                price_candidates.append(ticker)
    if price_candidates and price_worker is update_ticker_prices:
        start = _minus_years(effective_as_of, price_years)
        batch = update_price_universe(price_candidates, start_date=start, end_date=effective_as_of, batch_size=price_batch_size)
        price_update_results.update(
            {
                "updated": batch.get("updated", 0),
                "already_current": batch.get("already_current", 0),
                "no_data": batch.get("no_data", 0),
                "failed": batch.get("failed", 0),
                "rows_downloaded": batch.get("rows_downloaded", 0),
                "rows_stored": batch.get("rows_stored", 0),
                "requests": sum(1 for item in batch.get("results", []) if item.get("status") not in {"already_current"}),
                "results": batch.get("results", []),
            }
        )
        for output in batch["results"]:
            ticker = output["ticker"]
            status = output.get("status", "updated")
            if status in {"failed", "no_data"}:
                message = output.get("error") or status
                classification = _classify_provider_failure("PRICE_UPDATE_COMPLETE", message, status)
                repositories.upsert_provider_cooldown(
                    "yfinance",
                    "ticker",
                    ticker,
                    _failure_type_for(message, status),
                    classification,
                    message,
                    _cooldown_until_for(classification, effective_as_of),
                    ticker=ticker,
                    database_path=database_path,
                )
                stage_items.append(_stage_item(ticker, "PRICE_UPDATE_COMPLETE", "failed", message, 1))
                failures.append(_failure(ticker, "PRICE_UPDATE_COMPLETE", "api_or_no_data", "PriceUpdateError", message, 1, True, classification))
            else:
                stage_items.append(_stage_item(ticker, "PRICE_UPDATE_COMPLETE", "succeeded", status, 1))
    elif price_candidates:
        for ticker in price_candidates:
            try:
                start = _minus_years(effective_as_of, price_years)
                output = price_worker(ticker, start, effective_as_of)
                price_update_results["results"].append(output)
                status = output.get("status", "updated")
                if status in price_update_results and status not in {"failed", "no_data"}:
                    price_update_results[status] += 1
                if status != "already_current":
                    price_update_results["requests"] += 1
                price_update_results["rows_downloaded"] += int(output.get("rows_downloaded", 0) or 0)
                price_update_results["rows_stored"] += int(output.get("rows_stored", 0) or 0)
                if status in {"failed", "no_data"}:
                    price_update_results[status] += 1
                    classification = _classify_provider_failure("PRICE_UPDATE_COMPLETE", output.get("error") or status, status)
                    repositories.upsert_provider_cooldown(
                        "yfinance",
                        "ticker",
                        ticker,
                        _failure_type_for(output.get("error") or status, status),
                        classification,
                        output.get("error") or status,
                        _cooldown_until_for(classification, effective_as_of),
                        ticker=ticker,
                        database_path=database_path,
                    )
                    raise RuntimeError(output.get("error") or status)
                stage_items.append(_stage_item(ticker, "PRICE_UPDATE_COMPLETE", "succeeded", status, 1))
            except Exception as exc:
                if not price_update_results["results"] or price_update_results["results"][-1].get("ticker") != ticker:
                    price_update_results["failed"] += 1
                classification = _classify_provider_failure("PRICE_UPDATE_COMPLETE", str(exc))
                stage_items.append(_stage_item(ticker, "PRICE_UPDATE_COMPLETE", "failed", type(exc).__name__, 1))
                failures.append(_failure(ticker, "PRICE_UPDATE_COMPLETE", "api_or_no_data", type(exc).__name__, str(exc), 1, True, classification))
                continue
    mid = build_readiness_report(symbols, effective_as_of, price_years, database_path=database_path)
    mid_rows = {row["normalized_symbol"]: row for row in mid["rows"]}
    ciks_by_ticker = repositories.get_ciks_for_tickers([ticker for _, ticker in requested], database_path=database_path)
    requested_by_cik: Dict[str, str] = {}
    considered_ciks = set()
    requested_ciks = set()
    refreshed_ciks = set()
    for _, ticker in requested:
        row = before_rows.get(ticker, {})
        if row.get("eligibility_status") == "excluded":
            continue
        current = mid_rows.get(ticker, row)
        if skip_fundamentals:
            stage_items.append(_stage_item(ticker, "SEC_INGESTION_COMPLETE", "skipped", "fundamentals skipped by caller"))
            stage_items.append(_stage_item(ticker, "NORMALIZATION_COMPLETE", "skipped", "fundamentals skipped by caller"))
        elif row["sec_filing_count"] and row["normalized_fundamental_field_count"] and not refresh_normalization:
            stage_items.append(_stage_item(ticker, "SEC_INGESTION_COMPLETE", "skipped", "already has SEC filings"))
            stage_items.append(_stage_item(ticker, "NORMALIZATION_COMPLETE", "skipped", "already normalized"))
        else:
            if not settings.SEC_USER_AGENT:
                reason = "SEC_USER_AGENT is required for SEC fundamentals ingestion"
                stage_items.append(_stage_item(ticker, "SEC_INGESTION_COMPLETE", "failed", reason))
                failures.append(_failure(ticker, "SEC_INGESTION_COMPLETE", "configuration", "MissingSECUserAgent", reason, 1, False, MANUAL_REVIEW_REQUIRED))
                continue
            raw_cik = ciks_by_ticker.get(ticker)
            cik = normalize_cik(raw_cik) if raw_cik else None
            if cik:
                considered_ciks.add(cik)
                status = repositories.get_provider_refresh_status("sec", "cik", cik, database_path=database_path)
                freshness = repositories.provider_refresh_is_fresh(status, settings.SEC_PROVIDER_REFRESH_INTERVAL_HOURS)
                diagnostic = {
                    "ticker": ticker,
                    "associated_tickers": [symbol for symbol, mapped_cik in ciks_by_ticker.items() if mapped_cik and normalize_cik(mapped_cik) == cik],
                    "normalized_cik": cik,
                    "reason_refresh_was_considered": current.get("final_readiness_category") or row.get("final_readiness_category"),
                    "existing_last_success_timestamp": freshness["last_success_at"],
                    "freshness_cutoff": freshness["freshness_cutoff"],
                    "provider_status_lookup_key": f"sec:cik:{cik}",
                    "provider_status_write_key": f"sec:cik:{cik}",
                    "normalization_result": "normalized" if current.get("normalized_fundamental_field_count") else "incomplete_or_missing",
                    "provider_success_fresh": freshness["fresh"],
                    "provider_success_stale_reason": freshness["stale_reason"],
                }
                cooldown = None if force_provider_refresh else repositories.get_provider_cooldown(
                    "sec",
                    "cik",
                    cik,
                    effective_as_of,
                    database_path=database_path,
                )
                if cooldown:
                    sec_update_results["cooldown_skipped"] += 1
                    diagnostic["decision"] = "skipped_provider_cooldown"
                    sec_update_results["diagnostics"].append(diagnostic)
                    stage_items.append(_stage_item(ticker, "SEC_INGESTION_COMPLETE", "skipped", f"cooldown: {cooldown['retry_classification']}"))
                    stage_items.append(_stage_item(ticker, "NORMALIZATION_COMPLETE", "skipped", "SEC cooldown active"))
                    failures.append(
                        _failure(
                            ticker,
                            "SEC_INGESTION_COMPLETE",
                            "provider_cooldown",
                            "ProviderCooldown",
                            cooldown.get("error_message") or cooldown["failure_type"],
                            0,
                            cooldown["retry_classification"] in {RETRYABLE_NOW, RETRYABLE_AFTER_FUNDAMENTALS_REFRESH_INTERVAL},
                            cooldown["retry_classification"],
                        )
                    )
                    stage_items.append(_stage_item(ticker, "READINESS_VERIFIED", "succeeded" if current else "failed", "verified after cooldown skip"))
                    continue
                if cik in requested_by_cik:
                    sec_update_results["duplicate_cik_skipped"] += 1
                    diagnostic["decision"] = "skipped_duplicate_cik"
                    sec_update_results["diagnostics"].append(diagnostic)
                    stage_items.append(_stage_item(ticker, "SEC_INGESTION_COMPLETE", "skipped", f"duplicate CIK already requested via {requested_by_cik[cik]}"))
                    stage_items.append(_stage_item(ticker, "NORMALIZATION_COMPLETE", "skipped", "duplicate CIK request avoided"))
                    stage_items.append(_stage_item(ticker, "READINESS_VERIFIED", "succeeded" if current else "failed", "verified after duplicate CIK skip"))
                    continue
                if freshness["fresh"] and not force_provider_refresh:
                    sec_update_results["success_freshness_skipped"] += 1
                    if not current.get("graham_evaluable"):
                        sec_update_results["normalization_incomplete_after_success"] += 1
                    diagnostic["decision"] = "skipped_provider_success_fresh"
                    sec_update_results["diagnostics"].append(diagnostic)
                    stage_items.append(_stage_item(ticker, "SEC_INGESTION_COMPLETE", "skipped", "provider success is fresh"))
                    stage_items.append(_stage_item(ticker, "NORMALIZATION_COMPLETE", "skipped", "provider success fresh; Graham evaluability handled separately"))
                    stage_items.append(_stage_item(ticker, "READINESS_VERIFIED", "succeeded" if current else "failed", "verified after provider success freshness skip"))
                    continue
                if not status:
                    sec_update_results["success_missing_status"] += 1
                elif not freshness["fresh"]:
                    sec_update_results["success_status_stale"] += 1
                requested_by_cik[cik] = ticker
            try:
                sec_update_results["requests"] += 1
                if cik:
                    requested_ciks.add(cik)
                    diagnostic["decision"] = "requested_provider_refresh"
                    sec_update_results["diagnostics"].append(diagnostic)
                output = fundamental_worker(ticker, fundamental_years)
                status = output.get("status", "updated")
                if status in {"failed", "unmapped"}:
                    raise RuntimeError(output.get("error") or status)
                if cik and output.get("provider_success_record_written"):
                    sec_update_results["success_records_written"] += 1
                    refreshed_ciks.add(cik)
                    after_success_row = build_readiness_report([ticker], effective_as_of, price_years, database_path=database_path)["rows"][0]
                    if not after_success_row.get("graham_evaluable"):
                        sec_update_results["normalization_incomplete_after_success"] += 1
                stage_items.append(_stage_item(ticker, "SEC_INGESTION_COMPLETE", "succeeded", status, 1))
                stage_items.append(_stage_item(ticker, "NORMALIZATION_COMPLETE", "succeeded", "normalization stored with SEC facts", 1))
            except Exception as exc:
                classification = _classify_provider_failure("SEC_INGESTION_COMPLETE", str(exc))
                if cik:
                    repositories.upsert_provider_cooldown(
                        "sec",
                        "cik",
                        cik,
                        _failure_type_for(str(exc), type(exc).__name__),
                        classification,
                        str(exc),
                        _cooldown_until_for(classification, effective_as_of),
                        ticker=ticker,
                        database_path=database_path,
                    )
                    sec_update_results["cooldown_failures_recorded"] += 1
                stage_items.append(_stage_item(ticker, "SEC_INGESTION_COMPLETE", "failed", type(exc).__name__, 1))
                failures.append(_failure(ticker, "SEC_INGESTION_COMPLETE", "api_or_no_data", type(exc).__name__, str(exc), 1, True, classification))
                continue
        stage_items.append(_stage_item(ticker, "READINESS_VERIFIED", "succeeded" if current else "failed", "verified after attempted stages"))
    sec_update_results["unique_ciks_considered"] = len(considered_ciks)
    sec_update_results["unique_ciks_requested"] = len(requested_ciks)
    sec_update_results["unique_ciks_refreshed"] = len(refreshed_ciks)
    after = build_readiness_report(symbols, effective_as_of, price_years, database_path=database_path)
    summary = _preparation_summary(started, _now_iso(), before, after, stage_items, failures)
    summary["database_path"] = str(settings.DATABASE_PATH)
    summary["requested_tickers"] = before["requested_count"]
    summary["resolved_tickers"] = sum(1 for row in after["rows"] if row["security_resolution_status"] == "resolved")
    summary["price_update_attempted"] = summary["attempted_count_by_stage"].get("PRICE_UPDATE_COMPLETE", 0)
    summary["price_update_succeeded"] = summary["succeeded_count_by_stage"].get("PRICE_UPDATE_COMPLETE", 0)
    summary["price_update_results"] = price_update_results
    summary["stored_price_tickers"] = sum(1 for row in after["rows"] if row["price_row_count"] > 0)
    summary["stored_price_rows"] = sum(int(row["price_row_count"]) for row in after["rows"])
    summary["sec_update_attempted"] = summary["attempted_count_by_stage"].get("SEC_INGESTION_COMPLETE", 0)
    summary["sec_update_succeeded"] = summary["succeeded_count_by_stage"].get("SEC_INGESTION_COMPLETE", 0)
    summary["sec_update_results"] = sec_update_results
    summary["normalized_fundamental_tickers"] = sum(1 for row in after["rows"] if row["normalized_fundamental_field_count"] > 0)
    summary["graham_evaluable_tickers"] = after["summary"]["graham_evaluable"]
    summary["technical_evaluable_tickers"] = after["summary"]["technical_evaluable"]
    summary["combined_evaluable_tickers"] = after["summary"]["combined_evaluable"]
    by_reason: Dict[str, int] = {}
    for failure in failures:
        reason = failure["safe_error_message"] or failure["failure_category"]
        by_reason[reason] = by_reason.get(reason, 0) + 1
    for category, count in after["summary"]["categories"].items():
        if category != READY:
            by_reason[category] = by_reason.get(category, 0) + count
    summary["failures_by_reason"] = dict(sorted(by_reason.items()))
    by_retry_classification: Dict[str, int] = {}
    for failure in failures:
        classification = failure.get("retry_classification") or MANUAL_REVIEW_REQUIRED
        by_retry_classification[classification] = by_retry_classification.get(classification, 0) + 1
    summary["failures_by_retry_classification"] = dict(sorted(by_retry_classification.items()))
    payload = {"summary": summary, "before": before["summary"], "after": after["summary"], "rows": after["rows"], "stage_items": stage_items, "failures": failures}
    if export_dir:
        export_preparation_report(payload, export_dir)
    return payload


def _failure(
    ticker: str,
    stage: str,
    category: str,
    exc_type: str,
    message: str,
    attempts: int,
    retryable: bool,
    retry_classification: Optional[str] = None,
) -> Dict[str, Any]:
    now = _now_iso()
    classification = retry_classification or _classify_provider_failure(stage, message, category)
    return {
        "ticker": ticker,
        "stage": stage,
        "failure_category": category,
        "exception_type": exc_type,
        "safe_error_message": (message or "")[:300],
        "attempt_count": attempts,
        "first_failure_timestamp": now,
        "latest_failure_timestamp": now,
        "retryable": retryable,
        "retry_classification": classification,
    }


def _preparation_summary(started: str, ended: str, before: Dict[str, Any], after: Dict[str, Any], stage_items: Sequence[Dict[str, Any]], failures: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    by_stage: Dict[str, Dict[str, int]] = {stage: {"attempted": 0, "succeeded": 0, "skipped": 0, "failed": 0} for stage in STAGES}
    for item in stage_items:
        bucket = by_stage.setdefault(item["stage"], {"attempted": 0, "succeeded": 0, "skipped": 0, "failed": 0})
        if item["status"] != "skipped":
            bucket["attempted"] += 1
        if item["status"] in bucket:
            bucket[item["status"]] += 1
    return {
        "run_identifier": "phase5b-" + started.replace(":", "").replace("+", "Z"),
        "start_timestamp": started,
        "end_timestamp": ended,
        "requested_ticker_count": before["unique_normalized_count"],
        "already_complete_count": before["summary"]["ready"],
        "attempted_count_by_stage": {stage: values["attempted"] for stage, values in by_stage.items()},
        "succeeded_count_by_stage": {stage: values["succeeded"] for stage, values in by_stage.items()},
        "skipped_count_by_stage": {stage: values["skipped"] for stage, values in by_stage.items()},
        "failed_count_by_stage": {stage: values["failed"] for stage, values in by_stage.items()},
        "retry_count": sum(item.get("attempt_count", 0) - 1 for item in stage_items if item.get("attempt_count", 0) > 1),
        "api_no_data_failures": sum(1 for item in failures if item["failure_category"] == "api_or_no_data"),
        "unsupported_ticker_failures": sum(1 for item in failures if item["failure_category"] == "unsupported_ticker"),
        "normalization_failures": sum(1 for item in failures if item["stage"] == "NORMALIZATION_COMPLETE"),
        "remaining_not_ready_count": after["summary"]["requested_unique"] - after["summary"]["ready"],
        "final_ready_count": after["summary"]["ready"],
    }


def export_preparation_report(payload: Dict[str, Any], export_dir: str) -> Dict[str, str]:
    directory = Path(export_dir)
    directory.mkdir(parents=True, exist_ok=True)
    run_id = payload["summary"]["run_identifier"]
    json_path = directory / f"{run_id}-preparation.json"
    failure_path = directory / f"{run_id}-failures.csv"
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    failures = payload["failures"]
    if failures:
        with failure_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(failures[0].keys()))
            writer.writeheader()
            writer.writerows(failures)
    else:
        failure_path.write_text("", encoding="utf-8")
    return {"json": str(json_path), "failures_csv": str(failure_path)}


def database_audit_for_tickers(symbols: Sequence[str], as_of: str, price_years: int = 6, database_path: Optional[str] = None) -> Dict[str, Any]:
    readiness = build_readiness_report(symbols, as_of, price_years, database_path=database_path)
    status = repositories.security_universe_status(database_path=database_path)
    return {
        "raw_universe_rows": status["total_securities"],
        "unique_normalized_symbols": status["total_securities"],
        "eligible_common_stocks": status["eligible_graham_securities"],
        "requested_ticker_count": readiness["requested_count"],
        "resolved_ticker_count": sum(1 for row in readiness["rows"] if row["security_resolution_status"] == "resolved"),
        "securities_with_stored_prices": sum(1 for row in readiness["rows"] if row["price_row_count"] > 0),
        "securities_with_sufficient_price_history": readiness["summary"]["price_ready"],
        "securities_with_sec_filing_data": sum(1 for row in readiness["rows"] if row["sec_filing_count"] > 0),
        "securities_with_normalized_fundamentals": sum(1 for row in readiness["rows"] if row["normalized_fundamental_field_count"] > 0),
        "graham_evaluable_count": readiness["summary"]["graham_evaluable"],
        "technical_evaluable_count": readiness["summary"]["technical_evaluable"],
        "combined_evaluated_count": readiness["summary"]["combined_evaluable"],
        "excluded_count_by_reason": readiness["summary"]["categories"],
        "missing_data_count_by_reason": readiness["summary"]["categories"],
        "failed_update_count_by_reason": {},
        "unexplained_or_unaccounted_for_rows": readiness["reconciliation"]["unexplained_count"],
        "reconciliation": readiness["reconciliation"],
    }
