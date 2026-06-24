"""Point-in-time EPS and earnings-stability helpers."""

import math
import statistics
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional, Sequence, Tuple

from fundamentals.normalization import classify_period, parse_date
from strategies.graham_models import EPSMethod


@dataclass(frozen=True)
class EPSSelection:
    """Selected EPS value and provenance."""

    value: Optional[float]
    method: EPSMethod
    source_periods: List[str] = field(default_factory=list)
    source_filings: List[Dict[str, Any]] = field(default_factory=list)
    accession_numbers: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    rejection_reasons: List[str] = field(default_factory=list)
    derived_periods: List[Dict[str, Any]] = field(default_factory=list)
    direct_periods: List[Dict[str, Any]] = field(default_factory=list)
    selected_source: str = "unavailable"


@dataclass(frozen=True)
class EarningsStability:
    """Annual earnings-stability metrics."""

    positive_earnings_years: int
    total_earnings_years: int
    two_consecutive_losses: bool
    earliest_annual_eps: Optional[float]
    latest_annual_eps: Optional[float]
    five_year_eps_growth: Optional[float]
    five_year_eps_cagr: Optional[float]
    mean_annual_eps: Optional[float]
    earnings_volatility: Optional[float]
    minimum_annual_eps: Optional[float]
    maximum_annual_eps: Optional[float]
    annual_rows: List[Dict[str, Any]]


def _finite(value: Any) -> Optional[float]:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric):
        return None
    return numeric


def _annual_rows(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    annual = []
    for row in rows:
        try:
            period_class = classify_period(row.get("period_start"), row.get("period_end"))
        except ValueError:
            continue
        if period_class == "annual" or row.get("fiscal_period") == "FY":
            value = _finite(row.get("value"))
            if value is not None:
                copied = dict(row)
                copied["value"] = value
                annual.append(copied)
    annual.sort(key=lambda item: (item.get("period_end") or "", item.get("accepted_at") or item.get("filed_date") or "", item.get("accession_number") or ""))
    deduped: Dict[str, Dict[str, Any]] = {}
    for row in annual:
        deduped[row.get("period_end") or ""] = row
    return list(deduped.values())


def _availability(row: Dict[str, Any]) -> str:
    return row.get("accepted_at") or row.get("filed_date") or row.get("filing_date") or ""


def _row_sort_key(row: Dict[str, Any]) -> Tuple[Any, ...]:
    return (
        row.get("period_end") or "",
        _availability(row),
        int(row.get("is_amendment") or 0),
        row.get("accession_number") or "",
        row.get("fact_id") or 0,
    )


def _period_key(row: Dict[str, Any]) -> Tuple[Any, ...]:
    return (
        row.get("fiscal_year"),
        row.get("fiscal_period"),
        row.get("period_start"),
        row.get("period_end"),
        row.get("concept"),
        row.get("unit"),
    )


def _dedupe_latest(rows: Sequence[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[str]]:
    selected: Dict[Tuple[Any, ...], Dict[str, Any]] = {}
    warnings: List[str] = []
    for row in rows:
        key = _period_key(row)
        existing = selected.get(key)
        if existing is not None:
            warnings.append("duplicate EPS period resolved using latest available filing")
        if existing is None or _row_sort_key(row) > _row_sort_key(existing):
            selected[key] = dict(row)
    return sorted(selected.values(), key=_row_sort_key), warnings


def _is_complete_quarter(row: Dict[str, Any]) -> bool:
    try:
        return classify_period(row.get("period_start"), row.get("period_end")) == "quarter"
    except ValueError:
        return False


def _is_ytd(row: Dict[str, Any]) -> bool:
    fp = str(row.get("fiscal_period") or "").upper()
    dates = _dates(row)
    if fp in ("Q2", "Q3") and dates is not None:
        start, end = dates
        return 110 <= (end - start).days + 1 < 320
    try:
        return classify_period(row.get("period_start"), row.get("period_end")) == "year_to_date"
    except ValueError:
        return False


def _is_annual(row: Dict[str, Any]) -> bool:
    if str(row.get("fiscal_period") or "").upper() in ("Q1", "Q2", "Q3", "Q4"):
        return False
    try:
        return classify_period(row.get("period_start"), row.get("period_end")) == "annual" or row.get("fiscal_period") == "FY"
    except ValueError:
        return False


def _quarter_number(row: Dict[str, Any]) -> Optional[int]:
    fp = str(row.get("fiscal_period") or "").upper()
    if fp in ("Q1", "Q2", "Q3", "Q4"):
        return int(fp[1])
    frame = str(row.get("frame") or "").upper()
    if "Q1" in frame:
        return 1
    if "Q2" in frame:
        return 2
    if "Q3" in frame:
        return 3
    if "Q4" in frame:
        return 4
    return None


def _dates(row: Dict[str, Any]) -> Optional[Tuple[date, date]]:
    try:
        start = parse_date(row.get("period_start"))
        end = parse_date(row.get("period_end"))
    except ValueError:
        return None
    if start is None or end is None or start > end:
        return None
    return start, end


def _compatible(left: Dict[str, Any], right: Dict[str, Any]) -> bool:
    return (
        left.get("concept") == right.get("concept")
        and left.get("unit") == right.get("unit")
        and left.get("fiscal_year") == right.get("fiscal_year")
    )


def _derived_quarter(
    source: Dict[str, Any],
    prior: Dict[str, Any],
    fiscal_period: str,
    reason: str,
) -> Optional[Dict[str, Any]]:
    source_dates = _dates(source)
    prior_dates = _dates(prior)
    if source_dates is None or prior_dates is None:
        return None
    if not _compatible(source, prior):
        return None
    source_start, source_end = source_dates
    prior_start, prior_end = prior_dates
    if source_start != prior_start or prior_end >= source_end:
        return None
    value = _finite(source.get("value"))
    prior_value = _finite(prior.get("value"))
    if value is None or prior_value is None:
        return None
    row = dict(source)
    row["value"] = value - prior_value
    row["period_start"] = prior_end.isoformat()
    # SEC duration facts are date-inclusive, so the day after prior_end is the
    # mathematically clean start of the derived standalone period.
    row["period_start"] = (prior_end.toordinal() + 1)
    row["period_start"] = date.fromordinal(row["period_start"]).isoformat()
    row["period_end"] = source_end.isoformat()
    row["fiscal_period"] = fiscal_period
    row["derived"] = True
    row["derivation"] = reason
    row["source_rows"] = [source, prior]
    return row


def _quarter_index(row: Dict[str, Any]) -> Optional[int]:
    fy = row.get("fiscal_year")
    quarter = _quarter_number(row)
    if fy is None or quarter is None:
        return None
    try:
        return int(fy) * 4 + quarter
    except (TypeError, ValueError):
        return None


def _validate_four_quarters(quarters: Sequence[Dict[str, Any]]) -> List[str]:
    reasons: List[str] = []
    if len(quarters) != 4:
        reasons.append("TTM requires exactly four completed quarterly periods")
        return reasons
    concepts = {row.get("concept") for row in quarters}
    units = {row.get("unit") for row in quarters}
    if len(concepts) != 1:
        reasons.append("TTM rejected because EPS concepts differ")
    if len(units) != 1:
        reasons.append("TTM rejected because EPS units differ")
    period_keys = {(row.get("period_start"), row.get("period_end")) for row in quarters}
    if len(period_keys) != 4:
        reasons.append("TTM rejected because duplicate EPS periods exist")
    parsed = []
    for row in quarters:
        dates = _dates(row)
        if dates is None:
            reasons.append("TTM rejected because a source period has invalid dates")
            continue
        if _is_annual(row) and row.get("fiscal_period") == "FY":
            reasons.append("TTM rejected because an annual period was mixed into quarters")
        parsed.append(dates)
    parsed.sort()
    for index in range(1, len(parsed)):
        previous_start, previous_end = parsed[index - 1]
        current_start, current_end = parsed[index]
        if current_start <= previous_end:
            reasons.append("TTM rejected because quarterly EPS periods overlap")
        if current_end <= current_start:
            reasons.append("TTM rejected because a quarterly EPS period is not completed")
    indices = [_quarter_index(row) for row in quarters]
    if any(index is None for index in indices):
        reasons.append("TTM rejected because fiscal quarter metadata is incomplete")
    elif sorted(indices) != list(range(min(indices), min(indices) + 4)):
        reasons.append("TTM rejected because fiscal quarters are not consecutive")
    return sorted(set(reasons))


def _build_standalone_quarters(rows: Sequence[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[str]]:
    clean_rows = []
    reasons: List[str] = []
    for row in rows:
        value = _finite(row.get("value"))
        if value is None:
            reasons.append("EPS row rejected because value is not finite")
            continue
        copied = dict(row)
        copied["value"] = value
        clean_rows.append(copied)
    rows, warnings = _dedupe_latest(clean_rows)
    reasons.extend(warnings)

    direct = [dict(row, derived=False) for row in rows if _is_complete_quarter(row)]
    by_fy_q: Dict[Tuple[Any, int], Dict[str, Any]] = {}
    for row in direct:
        quarter = _quarter_number(row)
        if quarter is not None:
            by_fy_q[(row.get("fiscal_year"), quarter)] = row

    ytd = {}
    annual = {}
    for row in rows:
        quarter = _quarter_number(row)
        key = (row.get("fiscal_year"), quarter)
        if quarter and _is_ytd(row):
            ytd[key] = row
        if row.get("fiscal_period") == "FY" or _is_annual(row):
            annual[row.get("fiscal_year")] = row

    for (fy, quarter), row in sorted(ytd.items()):
        if quarter == 2 and (fy, 1) in by_fy_q and (fy, 2) not in by_fy_q:
            derived = _derived_quarter(row, by_fy_q[(fy, 1)], "Q2", "Q2 standalone derived from six-month YTD minus Q1")
            if derived:
                by_fy_q[(fy, 2)] = derived
        if quarter == 3:
            prior = ytd.get((fy, 2))
            if prior is not None and (fy, 3) not in by_fy_q:
                derived = _derived_quarter(row, prior, "Q3", "Q3 standalone derived from nine-month YTD minus six-month YTD")
                if derived:
                    by_fy_q[(fy, 3)] = derived
    for fy, row in sorted(annual.items()):
        prior = ytd.get((fy, 3))
        if prior is not None and (fy, 4) not in by_fy_q:
            derived = _derived_quarter(row, prior, "Q4", "Q4 standalone derived from annual EPS minus nine-month YTD")
            if derived:
                by_fy_q[(fy, 4)] = derived

    quarters = sorted(by_fy_q.values(), key=lambda item: (_quarter_index(item) or -1, item.get("period_end") or ""))
    return quarters, sorted(set(reasons))


def _select_ttm(rows: Sequence[Dict[str, Any]], method: EPSMethod) -> EPSSelection:
    quarters, build_reasons = _build_standalone_quarters(rows)
    if any("duplicate EPS period" in reason for reason in build_reasons):
        reasons = build_reasons + ["TTM rejected because duplicate EPS periods exist"]
        return EPSSelection(None, method, rejection_reasons=sorted(set(reasons)), warnings=sorted(set(reasons)))
    if len(quarters) < 4:
        reasons = build_reasons + ["TTM rejected because fewer than four standalone quarters are available"]
        return EPSSelection(None, method, rejection_reasons=sorted(set(reasons)), warnings=sorted(set(reasons)))
    selected = quarters[-4:]
    rejection_reasons = _validate_four_quarters(selected)
    if rejection_reasons:
        reasons = build_reasons + rejection_reasons
        return EPSSelection(None, method, rejection_reasons=sorted(set(reasons)), warnings=sorted(set(reasons)))
    value = sum(float(row["value"]) for row in selected)
    accessions = [row.get("accession_number") for row in selected if row.get("accession_number")]
    source_periods = [row.get("period_end") for row in selected if row.get("period_end")]
    derived = [row for row in selected if row.get("derived")]
    direct = [row for row in selected if not row.get("derived")]
    warnings = build_reasons
    return EPSSelection(
        value,
        method,
        source_periods,
        selected,
        accessions,
        sorted(set(warnings)),
        [],
        derived,
        direct,
        "ttm",
    )


def _select_annual(rows: Sequence[Dict[str, Any]], method: EPSMethod, extra_warnings: Sequence[str], rejection_reasons: Sequence[str]) -> EPSSelection:
    annual = _annual_rows(rows)
    if not annual:
        reasons = list(rejection_reasons) + [f"no {method.value.lower()} annual EPS available"]
        return EPSSelection(None, method, warnings=sorted(set(reasons)), rejection_reasons=sorted(set(reasons)))
    row = annual[-1]
    warnings = list(extra_warnings)
    if method == EPSMethod.ANNUAL_BASIC:
        warnings.append("basic EPS fallback used")
    warnings.append("annual EPS fallback used instead of TTM EPS")
    return EPSSelection(
        row["value"],
        method,
        [row.get("period_end")],
        [row],
        [row.get("accession_number")],
        sorted(set(warnings)),
        sorted(set(rejection_reasons)),
        [],
        [row],
        "annual",
    )


def select_eps(diluted_rows: Sequence[Dict[str, Any]], basic_rows: Sequence[Dict[str, Any]]) -> EPSSelection:
    """Select EPS using safe TTM when provable, then documented annual fallback."""
    diluted_ttm = _select_ttm(diluted_rows, EPSMethod.TTM_DILUTED)
    if diluted_ttm.value is not None:
        return diluted_ttm
    diluted_annual = _select_annual(
        diluted_rows,
        EPSMethod.ANNUAL_DILUTED,
        ["TTM diluted EPS rejected; annual diluted EPS fallback used"],
        diluted_ttm.rejection_reasons,
    )
    if diluted_annual.value is not None:
        return diluted_annual
    basic_ttm = _select_ttm(basic_rows, EPSMethod.TTM_BASIC)
    if basic_ttm.value is not None:
        warnings = list(basic_ttm.warnings) + ["basic EPS fallback used"]
        return EPSSelection(
            basic_ttm.value,
            basic_ttm.method,
            basic_ttm.source_periods,
            basic_ttm.source_filings,
            basic_ttm.accession_numbers,
            sorted(set(warnings)),
            basic_ttm.rejection_reasons,
            basic_ttm.derived_periods,
            basic_ttm.direct_periods,
            basic_ttm.selected_source,
        )
    basic_annual = _select_annual(
        basic_rows,
        EPSMethod.ANNUAL_BASIC,
        ["TTM basic EPS rejected; annual basic EPS fallback used"],
        list(diluted_ttm.rejection_reasons) + list(diluted_annual.rejection_reasons) + list(basic_ttm.rejection_reasons),
    )
    if basic_annual.value is not None:
        return basic_annual
    reasons = sorted(set(list(diluted_ttm.rejection_reasons) + list(basic_ttm.rejection_reasons) + ["reliable EPS unavailable"]))
    return EPSSelection(None, EPSMethod.UNAVAILABLE, warnings=reasons, rejection_reasons=reasons)


def earnings_stability(annual_eps_rows: Sequence[Dict[str, Any]], max_periods: int = 5) -> EarningsStability:
    """Calculate earnings stability from latest completed annual periods."""
    rows = _annual_rows(annual_eps_rows)[-max_periods:]
    values = [row["value"] for row in rows]
    positive = sum(1 for value in values if value > 0)
    two_losses = len(values) >= 2 and values[-1] < 0 and values[-2] < 0
    growth = None
    cagr = None
    if len(values) >= 2 and values[0] > 0 and values[-1] > 0:
        growth = (values[-1] / values[0]) - 1
        years = len(values) - 1
        cagr = (values[-1] / values[0]) ** (1 / years) - 1 if years > 0 else None
    return EarningsStability(
        positive,
        len(values),
        two_losses,
        values[0] if values else None,
        values[-1] if values else None,
        growth,
        cagr,
        statistics.mean(values) if values else None,
        statistics.pstdev(values) if len(values) > 1 else None,
        min(values) if values else None,
        max(values) if values else None,
        rows,
    )
