"""Stored-data readiness and resumable preparation helpers."""

import csv
import json
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from config import settings
from data.market_data import update_ticker_prices
from data.universe import normalize_ticker_value
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

STAGES = (
    "SECURITY_RESOLVED",
    "PRICE_UPDATE_COMPLETE",
    "SEC_INGESTION_COMPLETE",
    "NORMALIZATION_COMPLETE",
    "READINESS_VERIFIED",
)

REQUIRED_GRAHAM_FIELDS = {
    "current_assets",
    "current_liabilities",
    "shareholders_equity",
}
EPS_FIELDS = {"diluted_eps", "basic_eps"}
SHARE_FIELDS = {"shares_outstanding", "weighted_average_diluted_shares", "weighted_average_basic_shares"}
DEBT_FIELDS = {"total_debt", "long_term_debt", "total_liabilities"}


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
    return {row["normalized_ticker"]: dict(row) for row in rows}


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
    if not price.get("latest_price_date") or price["latest_price_date"] > as_of:
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
    missing = sorted(
        field
        for field in REQUIRED_GRAHAM_FIELDS
        if field not in fields
    )
    if not fields.intersection(EPS_FIELDS):
        missing.append("diluted_eps_or_basic_eps")
    if not fields.intersection(SHARE_FIELDS):
        missing.append("shares_outstanding_or_weighted_average_shares")
    if not fields.intersection(DEBT_FIELDS):
        missing.append("debt_or_liabilities")
    if missing:
        return False, "missing required Graham fields: " + ";".join(sorted(set(missing))), sorted(set(missing))
    return True, "required Graham fields available", []


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
    price_worker: Callable[[str, Optional[str], Optional[str]], Dict[str, Any]] = update_ticker_prices,
    fundamental_worker: Callable[[str, Optional[int]], Dict[str, Any]] = update_fundamentals_for_ticker,
    database_path: Optional[str] = None,
) -> Dict[str, Any]:
    effective_as_of = as_of or date.today().isoformat()
    started = _now_iso()
    before = build_readiness_report(symbols, effective_as_of, price_years, database_path=database_path)
    stage_items: List[Dict[str, Any]] = []
    failures: List[Dict[str, Any]] = []
    for row in before["rows"]:
        ticker = row["normalized_symbol"]
        if row["security_resolution_status"] != "resolved":
            stage_items.append(_stage_item(ticker, "SECURITY_RESOLVED", "failed", row["final_readiness_category"]))
            failures.append(_failure(ticker, "SECURITY_RESOLVED", "unsupported_ticker", "ReadinessError", row["final_readiness_category"], 1, False))
            continue
        stage_items.append(_stage_item(ticker, "SECURITY_RESOLVED", "skipped" if resume else "succeeded", "already resolved"))
        if row["price_ready"]:
            stage_items.append(_stage_item(ticker, "PRICE_UPDATE_COMPLETE", "skipped", "already price-ready"))
        else:
            try:
                start = _minus_years(effective_as_of, price_years)
                output = price_worker(ticker, start, effective_as_of)
                status = output.get("status", "updated")
                if status == "failed":
                    raise RuntimeError(output.get("error") or "price update failed")
                stage_items.append(_stage_item(ticker, "PRICE_UPDATE_COMPLETE", "succeeded", status, 1))
            except Exception as exc:
                stage_items.append(_stage_item(ticker, "PRICE_UPDATE_COMPLETE", "failed", type(exc).__name__, 1))
                failures.append(_failure(ticker, "PRICE_UPDATE_COMPLETE", "api_or_no_data", type(exc).__name__, str(exc), 1, True))
                continue
        if row["sec_filing_count"] and row["normalized_fundamental_field_count"] and not refresh_normalization:
            stage_items.append(_stage_item(ticker, "SEC_INGESTION_COMPLETE", "skipped", "already has SEC filings"))
            stage_items.append(_stage_item(ticker, "NORMALIZATION_COMPLETE", "skipped", "already normalized"))
        else:
            if not settings.SEC_USER_AGENT:
                reason = "SEC_USER_AGENT is required for SEC fundamentals ingestion"
                stage_items.append(_stage_item(ticker, "SEC_INGESTION_COMPLETE", "failed", reason))
                failures.append(_failure(ticker, "SEC_INGESTION_COMPLETE", "configuration", "MissingSECUserAgent", reason, 1, False))
                continue
            try:
                output = fundamental_worker(ticker, fundamental_years)
                status = output.get("status", "updated")
                if status in {"failed", "unmapped"}:
                    raise RuntimeError(output.get("error") or status)
                stage_items.append(_stage_item(ticker, "SEC_INGESTION_COMPLETE", "succeeded", status, 1))
                stage_items.append(_stage_item(ticker, "NORMALIZATION_COMPLETE", "succeeded", "normalization stored with SEC facts", 1))
            except Exception as exc:
                stage_items.append(_stage_item(ticker, "SEC_INGESTION_COMPLETE", "failed", type(exc).__name__, 1))
                failures.append(_failure(ticker, "SEC_INGESTION_COMPLETE", "api_or_no_data", type(exc).__name__, str(exc), 1, True))
                continue
        stage_items.append(_stage_item(ticker, "READINESS_VERIFIED", "succeeded", "verified after attempted stages"))
    after = build_readiness_report(symbols, effective_as_of, price_years, database_path=database_path)
    summary = _preparation_summary(started, _now_iso(), before, after, stage_items, failures)
    payload = {"summary": summary, "before": before["summary"], "after": after["summary"], "stage_items": stage_items, "failures": failures}
    if export_dir:
        export_preparation_report(payload, export_dir)
    return payload


def _failure(ticker: str, stage: str, category: str, exc_type: str, message: str, attempts: int, retryable: bool) -> Dict[str, Any]:
    now = _now_iso()
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
