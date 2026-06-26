"""Service layer for SEC fundamentals updates and point-in-time queries."""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from config import settings
from data.sec_client import SECClient
from data.sec_ticker_map import CIKMappingError, get_cik_for_ticker, normalize_cik, normalize_ticker
from database.repositories import upsert_provider_refresh_success, upsert_security
from fundamentals.concepts import EXPECTED_UNITS, SUPPORTED_FORMS, concept_precedence, standardized_field_for_concept
from fundamentals.normalization import (
    classify_period,
    normalize_accession,
    validate_accession_number,
    validate_iso_date,
    validate_numeric,
    validate_supported_form,
)
from fundamentals.repository import (
    count_fundamental_facts,
    count_fundamental_filings,
    get_facts_as_of,
    get_filings_for_ticker,
    get_fundamental_history as repository_get_fundamental_history,
    upsert_filing,
    upsert_fundamental_facts,
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _recent_filings(submissions: Dict[str, Any]) -> List[Dict[str, Any]]:
    recent = (submissions.get("filings") or {}).get("recent") or {}
    accession_numbers = recent.get("accessionNumber") or []
    filings = []
    for index, accession in enumerate(accession_numbers):
        def at(name: str) -> Any:
            values = recent.get(name) or []
            return values[index] if index < len(values) else None
        form = at("form")
        if form not in SUPPORTED_FORMS:
            continue
        accession_number = normalize_accession(accession)
        filings.append(
            {
                "accession_number": validate_accession_number(accession_number),
                "form_type": validate_supported_form(form),
                "filing_date": validate_iso_date(at("filingDate"), "filing_date"),
                "accepted_at": validate_iso_date(at("acceptanceDateTime"), "accepted_at") if at("acceptanceDateTime") else None,
                "report_period": validate_iso_date(at("reportDate"), "report_period") if at("reportDate") else None,
                "fiscal_year": int(at("fy")) if at("fy") not in (None, "") else None,
                "fiscal_period": at("fp"),
                "is_amendment": 1 if str(form).endswith("/A") else 0,
            }
        )
    return filings


def _iter_company_facts(company_facts: Dict[str, Any]) -> List[Tuple[str, str, Dict[str, Any]]]:
    facts = []
    for taxonomy, concepts in (company_facts.get("facts") or {}).items():
        for concept, payload in concepts.items():
            for unit, rows in (payload.get("units") or {}).items():
                for row in rows:
                    item = dict(row)
                    item["unit"] = unit
                    facts.append((taxonomy, concept, item))
    return facts


def _source_url(cik: str, accession_number: str) -> str:
    nodash = accession_number.replace("-", "")
    return f"{settings.SEC_BASE_URL}/Archives/edgar/data/{int(cik)}/{nodash}/{accession_number}-index.html"


def _build_fact_rows(
    ticker: str,
    filing_ids: Dict[str, int],
    filings_by_accession: Dict[str, Dict[str, Any]],
    company_facts: Dict[str, Any],
    downloaded_at: str,
) -> Tuple[List[Dict[str, Any]], int, int]:
    facts = []
    unsupported = 0
    seen = 0
    for taxonomy, concept, sec_fact in _iter_company_facts(company_facts):
        seen += 1
        standardized_field = standardized_field_for_concept(concept)
        if standardized_field is None:
            unsupported += 1
            continue
        accession = normalize_accession(sec_fact.get("accn") or "")
        filing_id = filing_ids.get(accession)
        filing = filings_by_accession.get(accession)
        if filing_id is None or filing is None:
            continue
        try:
            unit = sec_fact.get("unit")
            expected_unit = EXPECTED_UNITS.get(standardized_field)
            if expected_unit and unit != expected_unit:
                continue
            start = validate_iso_date(sec_fact.get("start"), "period_start") if sec_fact.get("start") else None
            end = validate_iso_date(sec_fact.get("end"), "period_end") if sec_fact.get("end") else filing.get("report_period")
            classify_period(start, end)
            value = validate_numeric(sec_fact.get("val"))
        except (TypeError, ValueError):
            continue
        facts.append(
            {
                "filing_id": filing_id,
                "ticker": ticker,
                "taxonomy": taxonomy,
                "concept": concept,
                "standardized_field": standardized_field,
                "unit": unit,
                "value": value,
                "period_start": start,
                "period_end": end,
                "frame": sec_fact.get("frame"),
                "form_type": filing["form_type"],
                "filed_date": sec_fact.get("filed") or filing["filing_date"],
                "accepted_at": filing.get("accepted_at"),
                "fiscal_year": filing.get("fiscal_year"),
                "fiscal_period": filing.get("fiscal_period"),
                "accession_number": accession,
                "source_name": "SEC EDGAR companyfacts",
                "downloaded_at": downloaded_at,
            }
        )
    return facts, seen, unsupported


def update_fundamentals_for_ticker(ticker: str, years: Optional[int] = None, client: Optional[SECClient] = None, database_path=None) -> Dict[str, Any]:
    """Download, standardize, and store SEC fundamentals for one ticker."""
    normalized = normalize_ticker(ticker)
    summary: Dict[str, Any] = {
        "ticker": normalized,
        "cik": None,
        "status": "failed",
        "filings_seen": 0,
        "filings_stored": 0,
        "facts_seen": 0,
        "facts_stored": 0,
        "unsupported_facts": 0,
        "provider_success_record_written": False,
        "provider_key": None,
        "http_status": None,
        "warnings": [],
        "error": None,
    }
    try:
        sec_client = client or SECClient()
        cik = get_cik_for_ticker(normalized, database_path=database_path, client=sec_client)
        cik = normalize_cik(cik)
        summary["cik"] = cik
        summary["provider_key"] = cik
        submissions = sec_client.get_submissions(cik)
        company_facts = sec_client.get_company_facts(cik)
        downloaded_at = _utc_now_iso()
        upsert_provider_refresh_success(
            "sec",
            "cik",
            cik,
            http_status=200,
            ticker=normalized,
            metadata={
                "submissions_returned": bool(submissions),
                "companyfacts_returned": bool(company_facts),
            },
            retrieved_at=downloaded_at,
            database_path=database_path,
        )
        summary["provider_success_record_written"] = True
        summary["http_status"] = 200
        if not submissions:
            summary["status"] = "no_supported_filings"
            summary["warnings"].append("empty submissions response")
            return summary
        filings = _recent_filings(submissions)
        if years:
            minimum_year = datetime.now(timezone.utc).year - years
            filings = [filing for filing in filings if not filing.get("fiscal_year") or filing["fiscal_year"] >= minimum_year]
        summary["filings_seen"] = len(filings)
        if not filings:
            summary["status"] = "no_supported_filings"
            return summary
        upsert_security(normalized, company_name=submissions.get("name"), security_type="Common Stock", database_path=database_path)
        filing_ids = {}
        filings_by_accession = {}
        for filing in filings:
            filing_row = dict(filing)
            filing_row.update(
                {
                    "ticker": normalized,
                    "cik": cik,
                    "source_url": _source_url(cik, filing["accession_number"]),
                    "downloaded_at": downloaded_at,
                }
            )
            filing_id = upsert_filing(filing_row, database_path=database_path)
            filing_ids[filing["accession_number"]] = filing_id
            filings_by_accession[filing["accession_number"]] = filing_row
        facts, facts_seen, unsupported = _build_fact_rows(normalized, filing_ids, filings_by_accession, company_facts or {}, downloaded_at)
        summary["facts_seen"] = facts_seen
        summary["unsupported_facts"] = unsupported
        summary["facts_stored"] = upsert_fundamental_facts(facts, database_path=database_path)
        summary["filings_stored"] = len(filing_ids)
        summary["status"] = "updated" if facts or filing_ids else "already_current"
    except CIKMappingError as exc:
        summary["status"] = "unmapped"
        summary["error"] = str(exc)
    except Exception as exc:
        summary["status"] = "failed"
        summary["error"] = str(exc)
    return summary


def update_fundamentals_universe(tickers: Sequence[str], years: Optional[int] = None, client: Optional[SECClient] = None, database_path=None) -> Dict[str, Any]:
    """Update many tickers without one failure stopping the run."""
    results = [update_fundamentals_for_ticker(ticker, years=years, client=client, database_path=database_path) for ticker in tickers]
    return {
        "results": results,
        "updated": sum(1 for item in results if item["status"] == "updated"),
        "unmapped": sum(1 for item in results if item["status"] == "unmapped"),
        "failed": sum(1 for item in results if item["status"] == "failed"),
        "filings_stored": sum(item["filings_stored"] for item in results),
        "facts_stored": sum(item["facts_stored"] for item in results),
    }


def get_available_fundamental_fields(ticker: str, database_path=None) -> List[str]:
    """Return standardized fields available for a ticker."""
    rows = get_facts_as_of(ticker, "9999-12-31T23:59:59", database_path=database_path)
    return sorted({row["standardized_field"] for row in rows if row.get("standardized_field")})


def get_filing_history(ticker: str, start_date: Optional[str] = None, end_date: Optional[str] = None, form_types: Optional[Sequence[str]] = None, database_path=None) -> List[Dict[str, Any]]:
    """Return supported filing history."""
    return get_filings_for_ticker(ticker, start_date=start_date, end_date=end_date, form_types=form_types, database_path=database_path)


def _fact_sort_key(row: Dict[str, Any]) -> Tuple[Any, ...]:
    return (
        row.get("period_end") or "",
        row.get("accepted_at") or row.get("filed_date") or "",
        int(row.get("is_amendment") or 0),
        -concept_precedence(row.get("standardized_field") or "", row.get("concept") or ""),
        row.get("accession_number") or "",
        row.get("fact_id") or 0,
    )


def get_fundamentals_as_of(ticker: str, as_of_date: str, period_type: str = "latest", database_path=None) -> Dict[str, Any]:
    """Return latest known standardized facts publicly available as of a date."""
    rows = get_facts_as_of(ticker, as_of_date, database_path=database_path)
    selected: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        field = row["standardized_field"]
        existing = selected.get(field)
        if existing is None or _fact_sort_key(row) > _fact_sort_key(existing):
            selected[field] = row
    return {
        "ticker": normalize_ticker(ticker),
        "as_of_date": as_of_date,
        "period_type": period_type,
        "fields": {
            field: {
                "value": row["value"],
                "unit": row["unit"],
                "report_period": row.get("period_end") or row.get("report_period"),
                "period_start": row.get("period_start"),
                "form_type": row.get("form_type"),
                "filing_date": row.get("filed_date"),
                "accepted_at": row.get("accepted_at"),
                "accession_number": row.get("accession_number"),
                "is_amendment": bool(row.get("is_amendment")),
                "accepted_at_fallback_used": row.get("accepted_at") is None,
                "concept": row.get("concept"),
                "taxonomy": row.get("taxonomy"),
                "fiscal_year": row.get("fiscal_year"),
                "fiscal_period": row.get("fiscal_period"),
                "selection_method": "latest available point-in-time fact",
            }
            for field, row in sorted(selected.items())
        },
    }


def get_fundamental_history(ticker: str, standardized_field: str, start_date: Optional[str] = None, end_date: Optional[str] = None, as_of_date: Optional[str] = None, database_path=None) -> List[Dict[str, Any]]:
    """Return historical rows for one field."""
    return repository_get_fundamental_history(ticker, standardized_field, start_date=start_date, end_date=end_date, as_of_date=as_of_date, database_path=database_path)


def get_latest_known_filing(ticker: str, as_of_date: str, database_path=None) -> Optional[Dict[str, Any]]:
    """Return the latest filing public as of a date."""
    filings = get_filings_for_ticker(ticker, database_path=database_path)
    visible = [filing for filing in filings if (filing.get("accepted_at") or filing["filing_date"]) <= as_of_date]
    if not visible:
        return None
    return sorted(visible, key=lambda row: (row.get("report_period") or "", row.get("accepted_at") or row["filing_date"], row["accession_number"]))[-1]


def count_by_ticker(ticker: str, database_path=None) -> Dict[str, int]:
    """Return filing/fact counts for one ticker."""
    return {
        "filings": count_fundamental_filings(ticker, database_path=database_path),
        "facts": count_fundamental_facts(ticker, database_path=database_path),
    }
