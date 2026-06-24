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
    return {
        "ticker": evaluation.ticker,
        "evaluation_date": evaluation.evaluation_date,
        "price": evaluation.inputs.market_price,
        "eps": evaluation.inputs.eps,
        "book_value_per_share": evaluation.metrics.book_value_per_share,
        "graham_number": evaluation.metrics.graham_number,
        "margin_of_safety": evaluation.metrics.margin_of_safety,
        "graham_quality_score": evaluation.metrics.graham_quality_score,
        "data_quality_score": evaluation.metrics.data_quality_score,
        "classification": evaluation.classification.value,
        "qualification_status": evaluation.qualification_status.value,
        "signal_type": evaluation.signal_type.value,
        "disqualification_reasons": "; ".join(evaluation.disqualification_reasons),
        "warnings": "; ".join(evaluation.warnings),
    }


def export_graham_evaluations(evaluations: Iterable[GrahamEvaluation], export_dir: str, filename: Optional[str] = None) -> List[Path]:
    """Export Graham evaluations to JSON."""
    directory = Path(export_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (filename or "graham-evaluations.json")
    data = [graham_evaluation_to_dict(evaluation) for evaluation in evaluations]
    path.write_text(json.dumps(data, indent=2, sort_keys=True, default=str), encoding="utf-8")
    return [path]
