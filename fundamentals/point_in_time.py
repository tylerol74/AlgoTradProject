"""Point-in-time assembly of Graham strategy inputs."""

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from data.sec_ticker_map import CIKMappingError, get_cik_for_ticker, normalize_ticker
from database.repositories import get_security
from fundamentals.earnings import earnings_stability, select_eps
from fundamentals.normalization import classify_period
from fundamentals.service import get_fundamental_history, get_fundamentals_as_of
from strategies.graham_models import EPSMethod, GrahamInputs


@dataclass(frozen=True)
class ShareSelection:
    """Selected point-in-time share count and diagnostics."""

    value: Optional[float]
    method: str
    source: Optional[Dict[str, Any]] = None
    warnings: List[str] = field(default_factory=list)
    rejection_reasons: List[str] = field(default_factory=list)


def _latest_price(history: List[Dict[str, Any]], evaluation_date: str) -> Optional[Dict[str, Any]]:
    rows = [row for row in history if row.get("trade_date") <= evaluation_date and row.get("close") is not None]
    return rows[-1] if rows else None


def _average_dollar_volume(history: List[Dict[str, Any]], evaluation_date: str, window: int = 20) -> Optional[float]:
    rows = [row for row in history if row.get("trade_date") <= evaluation_date and row.get("close") is not None and row.get("volume") is not None]
    rows = rows[-window:]
    if len(rows) < window:
        return None
    return sum(float(row["close"]) * float(row["volume"]) for row in rows) / window


def _field(fields: Dict[str, Dict[str, Any]], name: str) -> Optional[float]:
    row = fields.get(name)
    return None if row is None or row.get("value") is None else float(row["value"])


def _valid_positive(value: Any) -> Optional[float]:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric) or numeric <= 0:
        return None
    return numeric


def _availability(row: Dict[str, Any]) -> str:
    return row.get("accepted_at") or row.get("filed_date") or row.get("filing_date") or ""


def _share_sort_key(row: Dict[str, Any]) -> Tuple[Any, ...]:
    return (
        row.get("period_end") or row.get("report_period") or "",
        _availability(row),
        int(row.get("is_amendment") or 0),
        row.get("accession_number") or "",
        row.get("fact_id") or 0,
    )


def _clean_share_rows(rows: Sequence[Dict[str, Any]], label: str) -> Tuple[List[Dict[str, Any]], List[str]]:
    valid: List[Dict[str, Any]] = []
    warnings: List[str] = []
    for row in rows:
        value = _valid_positive(row.get("value"))
        if value is None:
            warnings.append(f"{label} fact rejected because share count is not positive and finite")
            continue
        copied = dict(row)
        copied["value"] = value
        valid.append(copied)
    return sorted(valid, key=_share_sort_key), sorted(set(warnings))


def _period_class(row: Dict[str, Any]) -> str:
    try:
        return classify_period(row.get("period_start"), row.get("period_end"))
    except ValueError:
        return "invalid"


def _matches_eps_period(row: Dict[str, Any], eps_selection: Any) -> bool:
    eps_periods = set(getattr(eps_selection, "source_periods", []) or [])
    if not eps_periods:
        return True
    return row.get("period_end") in eps_periods or row.get("report_period") in eps_periods


def _split_warning(selected: Dict[str, Any], comparison_rows: Sequence[Dict[str, Any]]) -> Optional[str]:
    selected_value = _valid_positive(selected.get("value"))
    if selected_value is None:
        return None
    for row in comparison_rows:
        row_value = _valid_positive(row.get("value"))
        if row_value is None:
            continue
        if row.get("period_end") != selected.get("period_end"):
            continue
        ratio = selected_value / row_value
        if ratio >= 4.0 or ratio <= 0.25:
            return "potential split-adjustment inconsistency between instant and weighted-average shares"
    return None


def select_historical_shares(
    shares_rows: Sequence[Dict[str, Any]],
    diluted_weighted_rows: Sequence[Dict[str, Any]],
    basic_weighted_rows: Sequence[Dict[str, Any]],
    eps_selection: Any,
) -> ShareSelection:
    """Select historical shares without using present-day or future data."""
    instant, instant_warnings = _clean_share_rows(shares_rows, "instant shares")
    diluted, diluted_warnings = _clean_share_rows(diluted_weighted_rows, "weighted-average diluted shares")
    basic, basic_warnings = _clean_share_rows(basic_weighted_rows, "weighted-average basic shares")
    warnings = instant_warnings + diluted_warnings + basic_warnings

    instant = [row for row in instant if _period_class(row) == "instant"]
    if instant:
        selected = instant[-1]
        split = _split_warning(selected, diluted + basic)
        if split:
            warnings.append(split)
        selected = dict(selected)
        selected["selection_method"] = "latest valid instant shares-outstanding fact"
        return ShareSelection(selected["value"], "shares_outstanding", selected, sorted(set(warnings)), [])

    compatible_diluted = [row for row in diluted if _matches_eps_period(row, eps_selection)]
    if compatible_diluted:
        selected = dict(compatible_diluted[-1])
        selected["selection_method"] = "weighted-average diluted shares matching selected EPS period"
        warnings.append("shares fallback used: weighted_average_diluted_shares")
        return ShareSelection(selected["value"], "weighted_average_diluted_shares", selected, sorted(set(warnings)), [])

    compatible_basic = [row for row in basic if _matches_eps_period(row, eps_selection)]
    if compatible_basic:
        selected = dict(compatible_basic[-1])
        selected["selection_method"] = "weighted-average basic shares matching selected EPS period"
        warnings.append("shares fallback used: weighted_average_basic_shares")
        return ShareSelection(selected["value"], "weighted_average_basic_shares", selected, sorted(set(warnings)), [])

    reasons = ["shares outstanding unavailable"]
    if diluted or basic:
        reasons.append("weighted-average shares unavailable for selected EPS period")
    return ShareSelection(None, "unavailable", None, sorted(set(warnings + reasons)), sorted(set(reasons)))


def build_graham_inputs(ticker: str, evaluation_date: str, strategy_data: Any, fundamentals_service: Any = None) -> GrahamInputs:
    """Build point-in-time Graham inputs without SEC or yfinance calls."""
    normalized = normalize_ticker(ticker)
    warnings: List[str] = []
    service = fundamentals_service
    history = strategy_data.get_ticker_history(normalized, end_date=evaluation_date)
    price_row = _latest_price(history, evaluation_date)
    market_price = float(price_row["close"]) if price_row else None
    if market_price is None:
        warnings.append("market price unavailable")
    average_dollar_volume = _average_dollar_volume(history, evaluation_date)
    if average_dollar_volume is None:
        warnings.append("20-day average dollar volume unavailable")

    getter = service.get_fundamentals_as_of if service else get_fundamentals_as_of
    history_getter = service.get_fundamental_history if service else get_fundamental_history
    result = getter(normalized, evaluation_date)
    fields = result.get("fields", {})
    if not fields:
        warnings.append("usable filing unavailable")
    for name, row in fields.items():
        if row.get("accepted_at_fallback_used"):
            warnings.append(f"{name} used filing-date fallback")

    diluted_eps_rows = history_getter(normalized, "diluted_eps", as_of_date=evaluation_date)
    basic_eps_rows = history_getter(normalized, "basic_eps", as_of_date=evaluation_date)
    eps_selection = select_eps(diluted_eps_rows, basic_eps_rows)
    warnings.extend(eps_selection.warnings)
    stability = earnings_stability(diluted_eps_rows or basic_eps_rows)
    if stability.total_earnings_years < 5:
        warnings.append("incomplete five-year earnings history")
    share_rows = history_getter(normalized, "shares_outstanding", as_of_date=evaluation_date)
    diluted_share_rows = history_getter(normalized, "weighted_average_diluted_shares", as_of_date=evaluation_date)
    basic_share_rows = history_getter(normalized, "weighted_average_basic_shares", as_of_date=evaluation_date)
    share_selection = select_historical_shares(share_rows, diluted_share_rows, basic_share_rows, eps_selection)
    shares = share_selection.value
    shares_method = share_selection.method
    warnings.extend(share_selection.warnings)
    market_cap = market_price * shares if market_price is not None and shares is not None else None
    if market_cap is None:
        warnings.append("market cap unavailable")
    try:
        cik = get_cik_for_ticker(normalized, refresh=False)
    except CIKMappingError:
        cik = None
        warnings.append("valid SEC CIK unavailable")
    security = get_security(normalized) or {}
    filing_metadata = dict(fields)
    filing_metadata["_identity"] = {
        "ticker": normalized,
        "cik": cik,
        "company_name": security.get("company_name"),
        "exchange": security.get("exchange"),
        "security_type": security.get("security_type"),
        "shares_method": shares_method,
        "shares_source": share_selection.source,
        "eps_selection": eps_selection,
        "earnings_stability": stability,
    }
    return GrahamInputs(
        ticker=normalized,
        evaluation_date=evaluation_date,
        market_price=market_price,
        average_dollar_volume_20d=average_dollar_volume,
        shares_outstanding=shares,
        market_cap=market_cap,
        eps=eps_selection.value,
        eps_method=eps_selection.method if eps_selection.method else EPSMethod.UNAVAILABLE,
        net_income=_field(fields, "net_income"),
        current_assets=_field(fields, "current_assets"),
        current_liabilities=_field(fields, "current_liabilities"),
        total_assets=_field(fields, "total_assets"),
        total_liabilities=_field(fields, "total_liabilities"),
        long_term_debt=_field(fields, "long_term_debt"),
        total_debt=_field(fields, "total_debt"),
        shareholders_equity=_field(fields, "shareholders_equity"),
        preferred_equity=_field(fields, "preferred_equity"),
        goodwill=_field(fields, "goodwill"),
        intangible_assets=_field(fields, "intangible_assets"),
        operating_income=_field(fields, "operating_income"),
        interest_expense=_field(fields, "interest_expense"),
        operating_cash_flow=_field(fields, "operating_cash_flow"),
        filing_metadata=filing_metadata,
        warnings=sorted(set(warnings)),
    )
