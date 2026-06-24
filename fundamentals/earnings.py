"""Point-in-time EPS and earnings-stability helpers."""

import math
import statistics
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from fundamentals.normalization import classify_period
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


def select_eps(diluted_rows: Sequence[Dict[str, Any]], basic_rows: Sequence[Dict[str, Any]]) -> EPSSelection:
    """Select EPS deterministically using annual fallback when TTM is unavailable."""
    diluted = _annual_rows(diluted_rows)
    if diluted:
        row = diluted[-1]
        return EPSSelection(
            row["value"],
            EPSMethod.ANNUAL_DILUTED,
            [row.get("period_end")],
            [row],
            [row.get("accession_number")],
            ["annual EPS fallback used instead of TTM diluted EPS"],
        )
    basic = _annual_rows(basic_rows)
    if basic:
        row = basic[-1]
        return EPSSelection(
            row["value"],
            EPSMethod.ANNUAL_BASIC,
            [row.get("period_end")],
            [row],
            [row.get("accession_number")],
            ["basic EPS fallback used", "annual EPS fallback used instead of TTM EPS"],
        )
    return EPSSelection(None, EPSMethod.UNAVAILABLE, warnings=["reliable EPS unavailable"])


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
