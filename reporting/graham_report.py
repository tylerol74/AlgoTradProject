"""Reporting helpers for Graham evaluations."""

import json
import statistics
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from strategies.graham_models import GrahamEvaluation


def classify_warning(warning: str) -> str:
    """Classify a warning into informational, caution, or critical severity."""
    text = warning.lower()
    if any(term in text for term in ("future", "invalid shares", "conflicting", "unusable", "missing required source", "no accepted timestamp")):
        return "critical"
    if any(term in text for term in ("incomplete", "long-term-debt-only", "filing-date fallback", "preferred equity", "goodwill", "intangible", "debt unavailable", "split-adjustment")):
        return "caution"
    return "informational"


def warning_counts(warnings: Iterable[str]) -> Dict[str, int]:
    """Count warnings by severity."""
    counts = Counter(classify_warning(warning) for warning in warnings)
    return {
        "informational": counts.get("informational", 0),
        "caution": counts.get("caution", 0),
        "critical": counts.get("critical", 0),
    }


def graham_evaluation_to_dict(evaluation: GrahamEvaluation) -> Dict[str, Any]:
    """Convert a Graham evaluation to JSON-friendly dictionaries."""
    payload = asdict(evaluation)
    for key in ("eps_method",):
        if hasattr(payload["inputs"].get(key), "value"):
            payload["inputs"][key] = payload["inputs"][key].value
    return payload


def graham_summary_row(evaluation: GrahamEvaluation) -> Dict[str, Any]:
    """Return compact screen/report row."""
    identity = evaluation.source_metadata.get("_identity", {}) if evaluation.source_metadata else {}
    eps_selection = identity.get("eps_selection")
    shares_source = identity.get("shares_source") or {}
    return {
        "ticker": evaluation.ticker,
        "evaluation_date": evaluation.evaluation_date,
        "price": evaluation.inputs.market_price,
        "eps": evaluation.inputs.eps,
        "eps_method": evaluation.inputs.eps_method.value,
        "eps_source_periods": "; ".join(getattr(eps_selection, "source_periods", []) or []),
        "eps_accession_numbers": "; ".join(getattr(eps_selection, "accession_numbers", []) or []),
        "eps_rejection_reasons": "; ".join(getattr(eps_selection, "rejection_reasons", []) or []),
        "eps_derived_periods": len(getattr(eps_selection, "derived_periods", []) or []),
        "eps_direct_periods": len(getattr(eps_selection, "direct_periods", []) or []),
        "eps_accepted_timestamps": "; ".join([row.get("accepted_at") or "" for row in getattr(eps_selection, "source_filings", []) or []]),
        "eps_forms": "; ".join([row.get("form_type") or "" for row in getattr(eps_selection, "source_filings", []) or []]),
        "eps_amendment_flags": "; ".join([str(bool(row.get("is_amendment"))) for row in getattr(eps_selection, "source_filings", []) or []]),
        "shares": evaluation.inputs.shares_outstanding,
        "shares_method": identity.get("shares_method"),
        "shares_period": shares_source.get("period_end") or shares_source.get("report_period"),
        "shares_accepted_at": shares_source.get("accepted_at"),
        "shares_accession_number": shares_source.get("accession_number"),
        "shares_form": shares_source.get("form_type"),
        "shares_amendment": bool(shares_source.get("is_amendment")) if shares_source else None,
        "book_value_per_share": evaluation.metrics.book_value_per_share,
        "book_value_source": (evaluation.source_metadata.get("shareholders_equity") or {}).get("accession_number") if evaluation.source_metadata else None,
        "graham_number": evaluation.metrics.graham_number,
        "margin_of_safety": evaluation.metrics.margin_of_safety,
        "graham_quality_score": evaluation.metrics.graham_quality_score,
        "graham_score_breakdown": evaluation.metrics.category_scores,
        "data_quality_score": evaluation.metrics.data_quality_score,
        "data_quality_classification": evaluation.metrics.data_quality_classification.value,
        "classification": evaluation.classification.value,
        "qualification_status": evaluation.qualification_status.value,
        "signal_type": evaluation.signal_type.value,
        "disqualification_reasons": "; ".join(evaluation.disqualification_reasons),
        "warnings": "; ".join(evaluation.warnings),
    }


def graham_audit_row(evaluation: GrahamEvaluation) -> Dict[str, Any]:
    """Return one deterministic data coverage audit row."""
    row = graham_summary_row(evaluation)
    data_issues = []
    identity = evaluation.source_metadata.get("_identity", {}) if evaluation.source_metadata else {}
    stability = identity.get("earnings_stability")
    debt_selection = identity.get("debt_selection")
    earnings_years = getattr(stability, "total_earnings_years", 0) if stability else 0
    for label, value in (
        ("price", evaluation.inputs.market_price),
        ("EPS", evaluation.inputs.eps),
        ("shares", evaluation.inputs.shares_outstanding),
        ("equity", evaluation.inputs.shareholders_equity),
        ("current assets", evaluation.inputs.current_assets),
        ("current liabilities", evaluation.inputs.current_liabilities),
    ):
        if value is None:
            data_issues.append(f"{label} unavailable")
    if earnings_years < 5:
        data_issues.append("earnings history incomplete")
    if not evaluation.source_metadata or len(evaluation.source_metadata) <= 1:
        data_issues.append("source metadata insufficient")
    severity_counts = warning_counts(evaluation.warnings)
    debt_value = evaluation.inputs.total_debt if evaluation.inputs.total_debt is not None else evaluation.inputs.long_term_debt
    debt_method = getattr(debt_selection, "method", None) or ("long_term_debt_only" if evaluation.inputs.long_term_debt is not None else "unavailable")
    if debt_value is None:
        data_issues.append("debt unavailable")
    data_ready = not data_issues
    strategy_qualified = evaluation.qualification_status.value == "QUALIFIED"
    return {
        "ticker": evaluation.ticker,
        "data_ready": data_ready,
        "strategy_qualified": strategy_qualified,
        "price_available": evaluation.inputs.market_price is not None,
        "price": evaluation.inputs.market_price,
        "eps_available": evaluation.inputs.eps is not None,
        "eps_method": evaluation.inputs.eps_method.value,
        "shares_available": evaluation.inputs.shares_outstanding is not None,
        "shares_method": identity.get("shares_method") or "unavailable",
        "equity_available": evaluation.inputs.shareholders_equity is not None,
        "current_assets_available": evaluation.inputs.current_assets is not None,
        "current_liabilities_available": evaluation.inputs.current_liabilities is not None,
        "debt_available": debt_value is not None,
        "debt_method": debt_method,
        "market_cap": evaluation.inputs.market_cap,
        "average_dollar_volume": evaluation.inputs.average_dollar_volume_20d,
        "five_year_earnings_history_count": earnings_years,
        "earnings_years": earnings_years,
        "data_quality_score": evaluation.metrics.data_quality_score,
        "graham_score": evaluation.metrics.graham_quality_score,
        "margin_of_safety": evaluation.metrics.margin_of_safety,
        "primary_data_issue": data_issues[0] if data_issues else "",
        "primary_disqualification_reason": evaluation.disqualification_reasons[0] if evaluation.disqualification_reasons else "",
        "primary_missing_reason": data_issues[0] if data_issues else "",
        "graham_ready": strategy_qualified,
        "warning_count": len(evaluation.warnings),
        "informational_warning_count": severity_counts["informational"],
        "caution_warning_count": severity_counts["caution"],
        "critical_warning_count": severity_counts["critical"],
        "warnings": list(evaluation.warnings),
        "qualification_status": evaluation.qualification_status.value,
        "disqualification_reasons": row["disqualification_reasons"],
    }


def graham_audit_summary(rows: List[Dict[str, Any]], invalid_tickers: Optional[List[str]] = None, total_requested: Optional[int] = None) -> Dict[str, Any]:
    """Summarize Graham audit coverage and outcomes."""
    invalid = invalid_tickers or []
    total = total_requested if total_requested is not None else len(rows) + len(invalid)
    valid = len(rows)

    def coverage(field: str) -> Dict[str, Any]:
        count = sum(1 for row in rows if row.get(field))
        return {"count": count, "percentage": (count / valid * 100.0) if valid else 0.0}

    scores = [float(row["data_quality_score"]) for row in rows if row.get("data_quality_score") is not None]
    issue_counts = Counter(row.get("primary_data_issue") for row in rows if row.get("primary_data_issue"))
    reason_counts = Counter(row.get("primary_disqualification_reason") for row in rows if row.get("primary_disqualification_reason"))
    return {
        "total_requested": total,
        "valid_tickers": valid,
        "invalid_tickers": len(invalid),
        "invalid_ticker_values": invalid,
        "price_coverage": coverage("price_available"),
        "eps_coverage": coverage("eps_available"),
        "shares_coverage": coverage("shares_available"),
        "equity_coverage": coverage("equity_available"),
        "current_assets_coverage": coverage("current_assets_available"),
        "current_liabilities_coverage": coverage("current_liabilities_available"),
        "debt_coverage": coverage("debt_available"),
        "five_year_history_coverage": {"count": sum(1 for row in rows if row.get("earnings_years", 0) >= 5), "percentage": (sum(1 for row in rows if row.get("earnings_years", 0) >= 5) / valid * 100.0) if valid else 0.0},
        "data_ready": coverage("data_ready"),
        "strategy_qualified": coverage("strategy_qualified"),
        "average_data_quality_score": statistics.mean(scores) if scores else None,
        "median_data_quality_score": statistics.median(scores) if scores else None,
        "top_data_issues": dict(issue_counts.most_common(5)),
        "top_disqualification_reasons": dict(reason_counts.most_common(5)),
        "warning_totals": {
            "informational": sum(int(row.get("informational_warning_count", 0)) for row in rows),
            "caution": sum(int(row.get("caution_warning_count", 0)) for row in rows),
            "critical": sum(int(row.get("critical_warning_count", 0)) for row in rows),
        },
    }


def graham_missing_data_plan(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return non-mutating update suggestions for missing audit data."""
    plan = []
    for row in rows:
        missing = []
        if not row.get("price_available"):
            missing.append("price")
        for field, label in (
            ("eps_available", "EPS"),
            ("shares_available", "shares"),
            ("equity_available", "equity"),
            ("current_assets_available", "current_assets"),
            ("current_liabilities_available", "current_liabilities"),
            ("debt_available", "debt"),
        ):
            if not row.get(field):
                missing.append(label)
        needs_prices = "price" in missing
        needs_fundamentals = any(item != "price" for item in missing)
        if not needs_prices and not needs_fundamentals:
            suggestion = "No update needed"
        elif needs_prices and needs_fundamentals:
            suggestion = f"python main.py update-prices --tickers {row['ticker']} ; python main.py update-fundamentals --tickers {row['ticker']}"
        elif needs_prices:
            suggestion = f"python main.py update-prices --tickers {row['ticker']}"
        else:
            suggestion = f"python main.py update-fundamentals --tickers {row['ticker']}"
        plan.append(
            {
                "ticker": row["ticker"],
                "needs_prices": needs_prices,
                "needs_fundamentals": needs_fundamentals,
                "missing_fields": missing,
                "suggested_command": suggestion,
            }
        )
    return plan


def graham_audit_payload(
    rows: List[Dict[str, Any]],
    summary: Dict[str, Any],
    configuration: Dict[str, Any],
    as_of_date: str,
    universe_source: str,
) -> Dict[str, Any]:
    """Return JSON/export payload for a Graham data audit."""
    return {
        "rows": rows,
        "summary": summary,
        "configuration": configuration,
        "audit_timestamp": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "as_of_date": as_of_date,
        "universe_source": universe_source,
    }


def export_graham_evaluations(evaluations: Iterable[GrahamEvaluation], export_dir: str, filename: Optional[str] = None) -> List[Path]:
    """Export Graham evaluations to JSON."""
    directory = Path(export_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (filename or "graham-evaluations.json")
    data = [graham_evaluation_to_dict(evaluation) for evaluation in evaluations]
    path.write_text(json.dumps(data, indent=2, sort_keys=True, default=str), encoding="utf-8")
    return [path]
