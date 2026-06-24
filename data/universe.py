"""Security universe construction, filtering, and batch helpers."""

import csv
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from config.settings import SEC_USER_AGENT
from data.sec_client import SECClient
from data.sec_ticker_map import load_sec_ticker_map, normalize_ticker
from database.repositories import (
    add_ingestion_run_item,
    complete_ingestion_run,
    create_ingestion_run,
    fundamentals_freshness,
    get_ingestion_run_items,
    get_sec_ticker_map_rows,
    list_security_universe,
    price_freshness,
    security_universe_status,
    upsert_security_universe,
)


SUPPORTED_EXCHANGES = {"NYSE", "NASDAQ", "NYSE AMERICAN", "NYSE ARCA"}
FINANCIAL_TERMS = ("bank", "bancorp", "financial", "insurance", "insurer", "reinsurance", "mortgage")
REIT_TERMS = ("reit", "real estate investment trust")
ETF_TERMS = ("etf", "exchange traded fund")
ETN_TERMS = ("etn", "exchange traded note")
ADR_TERMS = (" adr", "american depositary", "depositary shares")
PREFERRED_TERMS = ("preferred", "preference")
WARRANT_TERMS = ("warrant",)
RIGHT_TERMS = (" right", "rights")
UNIT_TERMS = (" unit", " units")
CLOSED_END_TERMS = ("closed-end", "closed end")


@dataclass(frozen=True)
class UniverseBuildResult:
    rows_seen: int
    rows_upserted: int
    eligible_count: int
    source: str
    dry_run: bool


def _valid_ticker(ticker: str) -> bool:
    allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.")
    return bool(ticker) and all(char in allowed for char in ticker) and any(char.isalpha() for char in ticker)


def _contains(text: str, terms: Sequence[str]) -> bool:
    lower = f" {text.lower()} "
    return any(term in lower for term in terms)


def classify_security(row: Dict[str, Any]) -> Dict[str, Any]:
    """Classify a raw universe row and return explicit eligibility reasons."""
    ticker = normalize_ticker(str(row.get("ticker") or row.get("symbol") or ""))
    title = str(row.get("title") or row.get("company_name") or row.get("name") or "")
    exchange = (row.get("exchange") or "NASDAQ").upper()
    raw_type = str(row.get("security_type") or row.get("raw_security_type") or "SEC reporting common equity")
    text = f"{ticker} {title} {raw_type}"
    is_preferred_ticker = ".P" in ticker or ticker.endswith(".PR")
    is_warrant_ticker = len(ticker) >= 5 and ticker.endswith(("W", "WS", "WT", "WW"))
    is_unit_ticker = len(ticker) >= 5 and ticker.endswith("U")
    is_right_ticker = len(ticker) >= 5 and ticker.endswith("R")
    is_otc_ticker = len(ticker) == 5 and ticker.endswith(("F", "Y"))
    is_adr = _contains(text, ADR_TERMS) or (len(ticker) == 5 and ticker.endswith("Y"))
    is_etf = _contains(text, ETF_TERMS)
    is_etn = _contains(text, ETN_TERMS)
    is_reit = _contains(text, REIT_TERMS)
    is_financial = _contains(text, FINANCIAL_TERMS)
    is_warrant = _contains(text, WARRANT_TERMS) or is_warrant_ticker
    is_right = _contains(text, RIGHT_TERMS) or is_right_ticker
    is_unit = _contains(text, UNIT_TERMS) or is_unit_ticker
    is_preferred = _contains(text, PREFERRED_TERMS) or is_preferred_ticker
    is_otc = "OTC" in exchange or "PINK" in exchange or is_otc_ticker
    is_closed_end = _contains(text, CLOSED_END_TERMS)
    is_common = not any((is_adr, is_etf, is_etn, is_warrant, is_right, is_unit, is_preferred, is_closed_end))
    reasons: List[str] = []
    if not _valid_ticker(ticker):
        reasons.append("invalid_ticker")
    if not row.get("cik"):
        reasons.append("missing_cik")
    if not exchange:
        reasons.append("missing_exchange")
    elif exchange not in SUPPORTED_EXCHANGES:
        reasons.append("unsupported_exchange")
    if not is_common:
        reasons.append("not_common_stock")
    for flag, reason in (
        (is_adr, "adr"),
        (is_etf, "etf"),
        (is_etn, "etn"),
        (is_reit, "reit"),
        (is_financial, "financial"),
        (is_warrant, "warrant"),
        (is_right, "right"),
        (is_unit, "unit"),
        (is_preferred, "preferred"),
        (is_otc, "otc"),
        (is_closed_end, "closed_end_fund"),
    ):
        if flag:
            reasons.append(reason)
    eligible = not reasons
    return {
        "ticker": ticker,
        "normalized_ticker": ticker,
        "company_name": title,
        "cik": row.get("cik"),
        "exchange": exchange,
        "security_type": "Common Stock" if is_common else raw_type,
        "sector": row.get("sector"),
        "industry": row.get("industry"),
        "is_active": row.get("is_active", True),
        "is_common_stock": is_common,
        "is_adr": is_adr,
        "is_etf": is_etf,
        "is_etn": is_etn,
        "is_reit": is_reit,
        "is_financial": is_financial,
        "is_warrant": is_warrant,
        "is_right": is_right,
        "is_unit": is_unit,
        "is_preferred": is_preferred,
        "is_otc": is_otc,
        "source": row.get("source") or "SEC company_tickers",
        "source_updated_at": row.get("source_updated_at"),
        "eligibility_status": "eligible" if eligible else "excluded",
        "eligibility_reasons": sorted(set(reasons)),
        "metadata": {"raw_security_type": raw_type, "raw_exchange": row.get("exchange")},
    }


def build_universe_from_sec_map(client: Optional[SECClient] = None, refresh: bool = False, dry_run: bool = False, database_path=None) -> UniverseBuildResult:
    """Build a central universe from the SEC ticker map."""
    if refresh and not SEC_USER_AGENT and client is None:
        raise RuntimeError("SEC_USER_AGENT is required to refresh the SEC ticker map")
    rows = load_sec_ticker_map(client=client, database_path=database_path) if refresh or client is not None else _local_sec_rows(database_path)
    classified = [classify_security({**row, "source": "SEC company_tickers"}) for row in rows]
    if dry_run:
        upserted = 0
    else:
        upserted = upsert_security_universe(classified, database_path=database_path)
    return UniverseBuildResult(
        rows_seen=len(rows),
        rows_upserted=upserted,
        eligible_count=sum(1 for row in classified if row["eligibility_status"] == "eligible"),
        source="SEC company_tickers",
        dry_run=dry_run,
    )


def _local_sec_rows(database_path=None) -> List[Dict[str, Any]]:
    rows = get_sec_ticker_map_rows(database_path=database_path)
    return [{"ticker": row["ticker"], "cik": row["cik"], "title": row["title"], "source_updated_at": row["updated_at"]} for row in rows]


def read_ticker_file(path: str) -> Tuple[List[str], List[str]]:
    """Read TXT or CSV tickers and return normalized unique tickers plus invalid values."""
    file_path = Path(path)
    raw: List[str] = []
    if file_path.suffix.lower() == ".csv":
        with file_path.open(newline="", encoding="utf-8") as handle:
            for row in csv.reader(handle):
                raw.extend(cell.strip() for cell in row if cell.strip())
    else:
        raw = [line.strip() for line in file_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return normalize_ticker_list(raw)


def normalize_ticker_list(values: Sequence[str]) -> Tuple[List[str], List[str]]:
    """Normalize tickers, remove duplicates, and return invalid values."""
    seen = set()
    tickers: List[str] = []
    invalid: List[str] = []
    for value in values:
        try:
            ticker = normalize_ticker(str(value))
        except ValueError:
            invalid.append(str(value))
            continue
        if not _valid_ticker(ticker):
            invalid.append(str(value))
            continue
        if ticker not in seen:
            tickers.append(ticker)
            seen.add(ticker)
    return tickers, invalid


def deterministic_sample(tickers: Sequence[str], count: int, seed: Optional[int] = None) -> List[str]:
    """Return a deterministic sample without relying on process-global randomness."""
    unique = sorted(dict.fromkeys(tickers))
    if count > len(unique):
        raise ValueError(f"requested {count} tickers but only {len(unique)} are available")
    if seed is None:
        return unique[:count]
    scored = sorted(((_sample_score(seed, ticker), ticker) for ticker in unique))
    return [ticker for _, ticker in scored[:count]]


def _sample_score(seed: int, ticker: str) -> str:
    return hashlib.sha256(f"{seed}:{ticker}".encode("utf-8")).hexdigest()


def universe_tickers(eligible_only: bool = True, limit: Optional[int] = None, offset: int = 0, database_path=None) -> List[str]:
    """Return universe tickers in deterministic order."""
    rows = list_security_universe(eligible_only=eligible_only, limit=limit, offset=offset, database_path=database_path)
    return [row["normalized_ticker"] for row in rows]


def run_tracked_batch(
    run_type: str,
    tickers: Sequence[str],
    worker: Callable[[str], Dict[str, Any]],
    configuration: Dict[str, Any],
    dry_run: bool = False,
    max_retries: int = 0,
    resume_run: Optional[int] = None,
    database_path=None,
) -> Dict[str, Any]:
    """Run a tracked per-ticker batch with bounded retry and failure isolation."""
    if resume_run is not None:
        previous = get_ingestion_run_items(resume_run, statuses=["failed", "partial"], database_path=database_path)
        tickers = [item["ticker"] for item in previous]
    run_id = create_ingestion_run(run_type, len(tickers), configuration, database_path=database_path)
    results = []
    for ticker in tickers:
        started = _utc_now()
        if dry_run:
            item = {"ticker": ticker, "status": "skipped", "skipped_count": 1, "started_at": started, "completed_at": _utc_now()}
            add_ingestion_run_item(run_id, item, database_path=database_path)
            results.append(item)
            continue
        attempt = 0
        while True:
            try:
                result = worker(ticker)
                status = _status_from_result(result)
                item = {
                    "ticker": ticker,
                    "status": status,
                    "inserted_count": int(result.get("inserted_count", result.get("rows_stored", result.get("facts_stored", 0))) or 0),
                    "updated_count": int(result.get("updated_count", 0) or 0),
                    "unchanged_count": int(result.get("unchanged_count", 0) or 0),
                    "skipped_count": int(result.get("skipped_count", 0) or 0),
                    "retry_count": attempt,
                    "error_type": result.get("error_type"),
                    "error_message": _safe_error(result.get("error")),
                    "started_at": started,
                    "completed_at": _utc_now(),
                    "result": result,
                }
                break
            except Exception as exc:
                transient = _is_transient_error(exc)
                if not transient or attempt >= max_retries:
                    item = {
                        "ticker": ticker,
                        "status": "failed",
                        "retry_count": attempt,
                        "error_type": type(exc).__name__,
                        "error_message": _safe_error(str(exc)),
                        "started_at": started,
                        "completed_at": _utc_now(),
                    }
                    break
                attempt += 1
        add_ingestion_run_item(run_id, item, database_path=database_path)
        results.append(item)
    complete_ingestion_run(run_id, "completed", database_path=database_path)
    return {
        "run_id": run_id,
        "run_type": run_type,
        "requested_count": len(tickers),
        "succeeded_count": sum(1 for item in results if item["status"] == "succeeded"),
        "partial_count": sum(1 for item in results if item["status"] == "partial"),
        "failed_count": sum(1 for item in results if item["status"] == "failed"),
        "skipped_count": sum(1 for item in results if item["status"] == "skipped"),
        "results": results,
    }


def coverage_freshness(tickers: Sequence[str], as_of: str, database_path=None) -> Dict[str, Any]:
    """Return conservative price/fundamental freshness metadata."""
    prices = price_freshness(tickers, database_path=database_path)
    fundamentals = fundamentals_freshness(tickers, database_path=database_path)
    return {
        ticker: {
            "latest_price_date": prices.get(ticker),
            "price_stale": prices.get(ticker) is None or prices.get(ticker) < as_of,
            "latest_accepted_at": fundamentals.get(ticker, {}).get("latest_accepted_at"),
            "latest_report_period": fundamentals.get(ticker, {}).get("latest_report_period"),
            "fundamentals_stale": fundamentals.get(ticker, {}).get("latest_accepted_at") is None,
        }
        for ticker in tickers
    }


def universe_status(database_path=None) -> Dict[str, Any]:
    """Return universe status."""
    return security_universe_status(database_path=database_path)


def _status_from_result(result: Dict[str, Any]) -> str:
    status = str(result.get("status") or "")
    if status in ("updated", "already_current", "succeeded"):
        return "succeeded"
    if status in ("no_data", "partial", "unsupported", "unmapped"):
        return "partial"
    if status in ("skipped",):
        return "skipped"
    return "failed" if result.get("error") else "succeeded"


def _is_transient_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(term in text for term in ("timeout", "temporarily", "429", "500", "502", "503", "504", "connection"))


def _safe_error(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).replace("\n", " ").strip()
    return text[:300]


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
