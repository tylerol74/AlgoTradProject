"""Data-quality and Graham quality scoring."""

from typing import Any, Dict, List, Optional

from strategies.graham_models import DataQualityClass, GrahamClassification, GrahamInputs, GrahamMetrics


def classify_data_quality(score: float) -> DataQualityClass:
    """Classify a 0-100 data-quality score."""
    if score >= 90:
        return DataQualityClass.HIGH_CONFIDENCE
    if score >= 75:
        return DataQualityClass.GOOD
    if score >= 60:
        return DataQualityClass.USABLE_WITH_WARNINGS
    if score >= 40:
        return DataQualityClass.LOW_CONFIDENCE
    return DataQualityClass.INSUFFICIENT


def data_quality_score(inputs: GrahamInputs, extra_warnings: Optional[List[str]] = None) -> Dict[str, Any]:
    """Return transparent data-quality score, penalties, and warnings."""
    penalties: List[Dict[str, Any]] = []
    warnings = list(inputs.warnings) + list(extra_warnings or [])

    def penalty(code: str, points: float, field: str, explanation: Optional[str] = None, source: Optional[Dict[str, Any]] = None) -> None:
        reason = explanation or code.replace("_", " ")
        penalties.append(
            {
                "code": code,
                "reason": reason,
                "points": points,
                "field": field,
                "explanation": reason,
                "source": source or {},
            }
        )
        warnings.append(reason)

    metadata = inputs.filing_metadata or {}
    if any((item or {}).get("accepted_at_fallback_used") for item in metadata.values() if isinstance(item, dict)):
        penalty("filing_date_fallback", 10, "accepted_at", "filing_date fallback used", metadata)
    if inputs.eps_method.value.startswith("ANNUAL"):
        penalty("annual_eps_fallback", 8, "eps", "annual EPS fallback used instead of TTM EPS", metadata.get("_identity", {}).get("eps_selection") if isinstance(metadata.get("_identity"), dict) else {})
    if inputs.eps_method.value.endswith("BASIC"):
        penalty("basic_eps_fallback", 8, "eps", "basic EPS fallback used")
    if inputs.shares_outstanding is None:
        penalty("shares_unavailable", 15, "shares_outstanding", "shares outstanding unavailable")
    elif metadata.get("_identity", {}).get("shares_method") not in (None, "shares_outstanding"):
        penalty("shares_fallback", 6, "shares_outstanding", "shares-outstanding fallback used", metadata.get("_identity", {}).get("shares_source") or {})
    if inputs.preferred_equity is None:
        penalty("preferred_equity_missing", 4, "preferred_equity", "preferred equity unavailable")
    for field, points in (
        ("current_assets", 6),
        ("current_liabilities", 6),
        ("long_term_debt", 4),
        ("total_debt", 4),
        ("goodwill", 4),
        ("intangible_assets", 4),
    ):
        if getattr(inputs, field) is None:
            penalty(f"{field}_missing", points, field, f"{field} unavailable")
    if "incomplete five-year earnings history" in warnings:
        penalty("incomplete_history", 8, "annual_eps_history", "incomplete five-year earnings history")
    if any("future" in warning.lower() for warning in warnings):
        penalty("possible_future_data", 30, "source_metadata", "possible future data issue")

    score = min(100.0, max(0.0, 100.0 - sum(item["points"] for item in penalties)))
    return {
        "score": score,
        "classification": classify_data_quality(score),
        "penalties": penalties,
        "warnings": sorted(set(warnings)),
    }


def classify_graham_score(score: float) -> GrahamClassification:
    """Classify a Graham quality score."""
    if score >= 90:
        return GrahamClassification.EXCEPTIONAL
    if score >= 80:
        return GrahamClassification.STRONG
    if score >= 70:
        return GrahamClassification.QUALIFIED
    if score >= 60:
        return GrahamClassification.WEAK
    return GrahamClassification.NOT_QUALIFIED


def _score_margin(margin: Optional[float]) -> int:
    if margin is None or margin < 0.20:
        return 0
    if margin < 0.30:
        return 8
    if margin < 0.40:
        return 15
    if margin < 0.50:
        return 20
    return 25


def _score_pe(pe: Optional[float]) -> int:
    if pe is None:
        return 0
    if pe <= 10:
        return 7
    if pe <= 12:
        return 6
    if pe <= 15:
        return 5
    if pe <= 20:
        return 2
    return 0


def _score_pb(pb: Optional[float]) -> int:
    if pb is None:
        return 0
    if pb <= 1:
        return 5
    if pb <= 1.5:
        return 4
    if pb <= 2:
        return 2
    return 0


def graham_quality_score(inputs: GrahamInputs, metrics: GrahamMetrics) -> Dict[str, Any]:
    """Return documented 0-100 Graham quality score with category subtotals."""
    valuation_rules = {
        "margin_of_safety": _score_margin(metrics.margin_of_safety),
        "price_to_earnings": _score_pe(metrics.price_to_earnings),
        "price_to_book": _score_pb(metrics.price_to_book),
        "tangible_value_support": 3 if metrics.tangible_graham_number and inputs.market_price and inputs.market_price < metrics.tangible_graham_number else 0,
    }
    current = metrics.current_ratio
    current_score = 8 if current is not None and current >= 2 else 6 if current is not None and current >= 1.5 else 3 if current is not None and current >= 1 else 0
    nca = metrics.net_current_assets
    debt = inputs.long_term_debt
    debt_nca_score = 7 if nca is not None and debt is not None and nca >= 0 and debt <= nca else 3 if nca is not None and debt is not None and nca > 0 and debt <= 2 * nca else 0
    dte = metrics.debt_to_equity
    dte_score = 6 if dte is not None and dte <= 0.5 else 4 if dte is not None and dte <= 1 else 2 if dte is not None and dte <= 2 else 0
    coverage = metrics.interest_coverage
    coverage_score = 4 if coverage is not None and coverage >= 5 else 3 if coverage is not None and coverage >= 3 else 1 if coverage is not None and coverage >= 1.5 else 0
    financial_rules = {
        "current_ratio": current_score,
        "long_term_debt_to_net_current_assets": debt_nca_score,
        "debt_to_equity": dte_score,
        "interest_coverage": coverage_score,
    }
    positive_years_score = 10 if metrics.positive_earnings_years >= 5 else 7 if metrics.positive_earnings_years == 4 else 3 if metrics.positive_earnings_years == 3 else 0
    growth = metrics.five_year_eps_growth
    growth_score = 6 if growth is not None and growth >= 0.25 else 4 if growth is not None and growth > 0 else 2 if growth == 0 else 0
    cash_flow_score = 4 if inputs.operating_cash_flow is not None and inputs.net_income is not None and inputs.operating_cash_flow > 0 and inputs.operating_cash_flow >= inputs.net_income else 2 if inputs.operating_cash_flow is not None and inputs.operating_cash_flow > 0 else 0
    earnings_rules = {
        "positive_years": positive_years_score,
        "five_year_growth": growth_score,
        "operating_cash_flow_support": cash_flow_score,
    }
    adv = inputs.average_dollar_volume_20d
    liquidity_score = 6 if adv is not None and adv >= 50_000_000 else 5 if adv is not None and adv >= 10_000_000 else 4 if adv is not None and adv >= 5_000_000 else 2 if adv is not None and adv >= 2_000_000 else 0
    market_cap = inputs.market_cap
    cap_score = 4 if market_cap is not None and market_cap >= 10_000_000_000 else 3 if market_cap is not None and market_cap >= 2_000_000_000 else 2 if market_cap is not None and market_cap >= 1_000_000_000 else 1 if market_cap is not None and market_cap >= 300_000_000 else 0
    data_score = 5 if metrics.data_quality_score >= 90 else 4 if metrics.data_quality_score >= 75 else 2 if metrics.data_quality_score >= 60 else 0
    def components(rules: Dict[str, int], observed: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        return {
            name: {
                "points": points,
                "observed": observed.get(name),
                "explanation": f"{name} awarded {points} points",
            }
            for name, points in rules.items()
        }

    tradability_rules = {"average_dollar_volume": liquidity_score, "market_cap": cap_score}
    data_rules = {"data_quality_points": data_score}
    categories = {
        "valuation": {
            "total": sum(valuation_rules.values()),
            "rules": valuation_rules,
            "components": components(
                valuation_rules,
                {
                    "margin_of_safety": metrics.margin_of_safety,
                    "price_to_earnings": metrics.price_to_earnings,
                    "price_to_book": metrics.price_to_book,
                    "tangible_value_support": metrics.tangible_graham_number,
                },
            ),
        },
        "financial_strength": {
            "total": sum(financial_rules.values()),
            "rules": financial_rules,
            "components": components(
                financial_rules,
                {
                    "current_ratio": metrics.current_ratio,
                    "long_term_debt_to_net_current_assets": metrics.net_current_assets,
                    "debt_to_equity": metrics.debt_to_equity,
                    "interest_coverage": metrics.interest_coverage,
                },
            ),
        },
        "earnings_quality": {
            "total": sum(earnings_rules.values()),
            "rules": earnings_rules,
            "components": components(
                earnings_rules,
                {
                    "positive_years": metrics.positive_earnings_years,
                    "five_year_growth": metrics.five_year_eps_growth,
                    "operating_cash_flow_support": inputs.operating_cash_flow,
                },
            ),
        },
        "tradability": {
            "total": liquidity_score + cap_score,
            "rules": tradability_rules,
            "components": components(tradability_rules, {"average_dollar_volume": adv, "market_cap": market_cap}),
        },
        "data_quality": {
            "total": data_score,
            "rules": data_rules,
            "components": components(data_rules, {"data_quality_points": metrics.data_quality_score}),
        },
    }
    score = min(100.0, max(0.0, float(sum(category["total"] for category in categories.values()))))
    return {"score": score, "classification": classify_graham_score(score), "categories": categories}
