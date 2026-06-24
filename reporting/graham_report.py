"""Reporting helpers for Graham evaluations."""

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from strategies.graham_models import GrahamEvaluation


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
    missing = []
    for label, value in (
        ("price", evaluation.inputs.market_price),
        ("EPS", evaluation.inputs.eps),
        ("shares", evaluation.inputs.shares_outstanding),
        ("equity", evaluation.inputs.shareholders_equity),
        ("current assets", evaluation.inputs.current_assets),
        ("current liabilities", evaluation.inputs.current_liabilities),
        ("debt", evaluation.inputs.total_debt if evaluation.inputs.total_debt is not None else evaluation.inputs.long_term_debt),
    ):
        if value is None:
            missing.append(f"{label} unavailable")
    identity = evaluation.source_metadata.get("_identity", {}) if evaluation.source_metadata else {}
    stability = identity.get("earnings_stability")
    return {
        "ticker": evaluation.ticker,
        "price_available": evaluation.inputs.market_price is not None,
        "eps_available": evaluation.inputs.eps is not None,
        "eps_method": evaluation.inputs.eps_method.value,
        "shares_available": evaluation.inputs.shares_outstanding is not None,
        "shares_method": identity.get("shares_method") or "unavailable",
        "equity_available": evaluation.inputs.shareholders_equity is not None,
        "current_assets_available": evaluation.inputs.current_assets is not None,
        "current_liabilities_available": evaluation.inputs.current_liabilities is not None,
        "debt_available": evaluation.inputs.total_debt is not None or evaluation.inputs.long_term_debt is not None,
        "five_year_earnings_history_count": getattr(stability, "total_earnings_years", 0) if stability else 0,
        "data_quality_score": evaluation.metrics.data_quality_score,
        "graham_ready": not missing and evaluation.qualification_status.value == "QUALIFIED",
        "primary_missing_reason": missing[0] if missing else (evaluation.disqualification_reasons[0] if evaluation.disqualification_reasons else ""),
        "warning_count": len(evaluation.warnings),
        "qualification_status": evaluation.qualification_status.value,
        "disqualification_reasons": row["disqualification_reasons"],
    }


def export_graham_evaluations(evaluations: Iterable[GrahamEvaluation], export_dir: str, filename: Optional[str] = None) -> List[Path]:
    """Export Graham evaluations to JSON."""
    directory = Path(export_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (filename or "graham-evaluations.json")
    data = [graham_evaluation_to_dict(evaluation) for evaluation in evaluations]
    path.write_text(json.dumps(data, indent=2, sort_keys=True, default=str), encoding="utf-8")
    return [path]
