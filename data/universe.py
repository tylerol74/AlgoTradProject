"""Universe construction, sampling, batch tracking, and coverage helpers."""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from database import repositories


SUPPORTED_EXCHANGES = {"NYSE", "NASDAQ", "NYSE AMERICAN", "NYSE ARCA"}
FINANCIAL_TERMS = ("bank", "bancorp", "financial", "insurance", "insurer", "capital trust")
REIT_TERMS = ("reit", "real estate investment trust")
ETF_TERMS = (" etf", "exchange traded", "fund", "trust")


@dataclass(frozen=True)
class UniverseBuildResult:
    source: str
    rows_seen: int
    rows_upserted: int
    eligible_count: int
    dry_run: bool
    warnings: List[str]


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize_ticker_value(ticker: str) -> str:
    return (ticker or "").strip().upper().replace("/", ".")


def _valid_ticker(ticker: str) -> bool:
    return bool(ticker) and ticker.replace(".", "").replace("-", "").isalnum()


def classify_security(row: Dict[str, Any]) -> Dict[str, Any]:
    """Classify one SEC ticker-map row without ticker-specific exceptions."""
    raw_ticker = row.get("ticker") or row.get("symbol") or ""
    ticker = normalize_ticker_value(raw_ticker)
    title = row.get("title") or row.get("company_name") or ""
    lower = title.lower()
    exchange = (row.get("exchange") or "NASDAQ").upper()
    security_type = row.get("security_type") or "SEC reporting common equity"
    reasons: List[str] = []
    is_invalid = not _valid_ticker(ticker)
    is_preferred = ".P" in ticker or ticker.endswith(".PR") or "preferred" in lower
    is_warrant = len(ticker) >= 5 and (ticker.endswith("W") or ticker.endswith("WS") or ticker.endswith("WT") or ticker.endswith("WW")) or "warrant" in lower
    is_right = len(ticker) >= 5 and ticker.endswith("R") or "right" in lower
    is_unit = len(ticker) >= 5 and ticker.endswith("U") or " unit" in lower
    is_otc = len(ticker) == 5 and ticker.endswith(("F", "Y"))
    is_adr = (len(ticker) == 5 and ticker.endswith("Y")) or " adr" in lower or "american deposit" in lower
    is_reit = any(term in lower for term in REIT_TERMS)
    is_financial = any(term in lower for term in FINANCIAL_TERMS)
    is_etn = " etn" in lower or "exchange traded note" in lower
    is_etf = any(term in lower for term in ETF_TERMS) and "common" not in lower
    is_closed_end = "closed end" in lower or "closed-end" in lower
    is_common = not any((is_invalid, is_preferred, is_warrant, is_right, is_unit, is_etf, is_etn, is_closed_end))
    if is_invalid:
        reasons.append("invalid_ticker")
    if exchange not in SUPPORTED_EXCHANGES:
        reasons.append("exchange")
    for flag, reason in (
        (is_preferred, "preferred"),
        (is_warrant, "warrant"),
        (is_right, "right"),
        (is_unit, "unit"),
        (is_etf, "etf"),
        (is_etn, "etn"),
        (is_reit, "reit"),
        (is_financial, "financial"),
        (is_otc, "otc"),
        (is_adr, "adr"),
        (is_closed_end, "closed_end_fund"),
    ):
        if flag:
            reasons.append(reason)
    if not row.get("cik"):
        reasons.append("missing_cik")
    eligible = is_common and not reasons
    now = _now_iso()
    return {
        "ticker": ticker,
        "normalized_ticker": ticker,
        "company_name": title,
        "cik": str(row.get("cik")) if row.get("cik") is not None else None,
        "exchange": exchange,
        "security_type": security_type,
        "sector": row.get("sector"),
        "industry": row.get("industry"),
        "is_active": True,
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
        "source_updated_at": row.get("updated_at"),
        "first_seen_at": now,
        "last_seen_at": now,
        "delisted_at": None,
        "eligibility_status": "eligible" if eligible else "excluded",
        "eligibility_reasons": ";".join(sorted(set(reasons))),
        "metadata_json": {"raw_title": title},
    }


def build_universe_from_sec_map(dry_run: bool = False, database_path: Optional[str] = None) -> UniverseBuildResult:
    rows = repositories.get_sec_ticker_map_rows(database_path=database_path)
    classified = [classify_security(row) for row in rows]
    if not dry_run:
        repositories.upsert_security_universe(classified, database_path=database_path)
    return UniverseBuildResult(
        source="SEC company_tickers",
        rows_seen=len(rows),
        rows_upserted=0 if dry_run else len(classified),
        eligible_count=sum(1 for row in classified if row["eligibility_status"] == "eligible"),
        dry_run=dry_run,
        warnings=[],
    )


def read_ticker_file(path: str) -> List[str]:
    return [line.strip().upper() for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip() and not line.strip().startswith("#")]


def normalize_ticker_list(tickers: Iterable[str]) -> List[str]:
    seen = set()
    result = []
    for ticker in tickers:
        normalized = normalize_ticker_value(ticker)
        if normalized and normalized not in seen:
            result.append(normalized)
            seen.add(normalized)
    return result


def deterministic_sample(tickers: Sequence[str], count: int, seed: int) -> List[str]:
    normalized = normalize_ticker_list(tickers)
    if len(normalized) < count:
        raise ValueError(f"requested {count} tickers but only {len(normalized)} are available")
    return sorted(normalized, key=lambda ticker: (hashlib.sha256(f"{seed}:{ticker}".encode()).hexdigest(), ticker))[:count]


def universe_tickers(eligible_only: bool = True, limit: Optional[int] = None, offset: int = 0, database_path: Optional[str] = None) -> List[str]:
    if eligible_only:
        return repositories.get_active_common_stock_tickers(limit=limit, offset=offset, database_path=database_path)
    rows = repositories.list_security_universe(limit=limit, offset=offset, database_path=database_path)
    return [row["normalized_ticker"] for row in rows]


def run_tracked_batch(
    run_type: str,
    tickers: Sequence[str],
    worker: Callable[[str], Dict[str, Any]],
    dry_run: bool = False,
    max_retries: int = 0,
    resume_run: Optional[int] = None,
    database_path: Optional[str] = None,
) -> Dict[str, Any]:
    selected = normalize_ticker_list(tickers)
    run_id = resume_run or repositories.create_ingestion_run(run_type, len(selected), {"dry_run": dry_run}, database_path=database_path)
    results = []
    for ticker in selected:
        if dry_run:
            item = {"ticker": ticker, "status": "succeeded", "skipped_count": 1, "started_at": _now_iso(), "completed_at": _now_iso()}
            repositories.add_ingestion_run_item(run_id, item, database_path=database_path)
            results.append(item)
            continue
        attempts = 0
        while True:
            attempts += 1
            try:
                output = worker(ticker)
                status = "succeeded" if output.get("status") not in {"failed"} else "failed"
                item = {
                    "ticker": ticker,
                    "status": status,
                    "inserted_count": int(output.get("rows_stored", output.get("facts_stored", 0)) or 0),
                    "updated_count": int(output.get("updated_count", 0) or 0),
                    "unchanged_count": int(output.get("unchanged_count", 0) or 0),
                    "skipped_count": int(output.get("skipped_count", 0) or 0),
                    "retry_count": attempts - 1,
                    "error_type": None if status == "succeeded" else "worker_failed",
                    "error_message": output.get("error"),
                    "started_at": _now_iso(),
                    "completed_at": _now_iso(),
                }
                break
            except Exception as exc:
                if attempts > max_retries + 1:
                    item = {"ticker": ticker, "status": "failed", "retry_count": attempts - 1, "error_type": type(exc).__name__, "error_message": str(exc)[:300], "started_at": _now_iso(), "completed_at": _now_iso()}
                    break
        repositories.add_ingestion_run_item(run_id, item, database_path=database_path)
        results.append(item)
    repositories.complete_ingestion_run(run_id, "completed", database_path=database_path)
    return {
        "run_id": run_id,
        "requested_count": len(selected),
        "succeeded_count": sum(1 for item in results if item["status"] == "succeeded"),
        "failed_count": sum(1 for item in results if item["status"] == "failed"),
        "skipped_count": sum(int(item.get("skipped_count", 0)) for item in results),
        "results": results,
    }


def coverage_freshness(tickers: Sequence[str], as_of: Optional[str] = None, database_path: Optional[str] = None) -> Dict[str, Any]:
    selected = normalize_ticker_list(tickers)
    price = repositories.price_freshness(selected, database_path=database_path)
    fundamentals = repositories.fundamentals_freshness(selected, database_path=database_path)
    return {
        "requested_ticker_count": len(tickers),
        "resolved_ticker_count": len(selected),
        "price_coverage": {"count": sum(1 for value in price.values() if value), "percentage": (sum(1 for value in price.values() if value) / len(selected) * 100.0) if selected else 0.0},
        "fundamentals_coverage": {"count": sum(1 for value in fundamentals.values() if value.get("latest_accepted_at")), "percentage": (sum(1 for value in fundamentals.values() if value.get("latest_accepted_at")) / len(selected) * 100.0) if selected else 0.0},
        "price_freshness": price,
        "fundamentals_freshness": fundamentals,
        "as_of": as_of,
    }


def universe_status(database_path: Optional[str] = None) -> Dict[str, Any]:
    return repositories.security_universe_status(database_path=database_path)


def write_json(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    return path
