"""Point-in-time assembly of Graham strategy inputs."""

from typing import Any, Dict, List, Optional

from data.sec_ticker_map import CIKMappingError, get_cik_for_ticker, normalize_ticker
from database.repositories import get_security
from fundamentals.earnings import earnings_stability, select_eps
from fundamentals.service import get_fundamental_history, get_fundamentals_as_of
from strategies.graham_models import EPSMethod, GrahamInputs


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


def _select_shares(fields: Dict[str, Dict[str, Any]], eps_selection) -> (Optional[float], str, List[str]):
    warnings: List[str] = []
    if fields.get("shares_outstanding"):
        return float(fields["shares_outstanding"]["value"]), "shares_outstanding", warnings
    if fields.get("weighted_average_diluted_shares"):
        warnings.append("shares fallback used: weighted_average_diluted_shares")
        return float(fields["weighted_average_diluted_shares"]["value"]), "weighted_average_diluted_shares", warnings
    if fields.get("weighted_average_basic_shares"):
        warnings.append("shares fallback used: weighted_average_basic_shares")
        return float(fields["weighted_average_basic_shares"]["value"]), "weighted_average_basic_shares", warnings
    warnings.append("shares outstanding unavailable")
    return None, "unavailable", warnings


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
    shares, shares_method, share_warnings = _select_shares(fields, eps_selection)
    warnings.extend(share_warnings)
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
