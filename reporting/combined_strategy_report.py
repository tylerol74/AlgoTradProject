"""Reporting helpers for combined Graham and technical strategy evaluations."""

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from strategies.combined_graham_technical import CombinedStrategyEvaluation


def combined_evaluation_to_dict(evaluation: CombinedStrategyEvaluation) -> Dict[str, Any]:
    """Convert a combined evaluation to a JSON-friendly dictionary."""
    return asdict(evaluation)


def combined_summary_row(evaluation: CombinedStrategyEvaluation) -> Dict[str, Any]:
    """Return compact screen/report row for the combined strategy."""
    technical = evaluation.technical_evaluation
    graham = evaluation.graham_evaluation
    reasons = evaluation.disqualification_reasons
    return {
        "ticker": evaluation.ticker,
        "price": graham.inputs.market_price,
        "graham_qualified": graham.qualification_status.value == "QUALIFIED",
        "technical_qualified": technical.qualified,
        "combined_qualified": evaluation.qualified,
        "graham_score": graham.metrics.graham_quality_score,
        "margin_of_safety": graham.metrics.margin_of_safety,
        "five_day_return": technical.metrics.five_day_return,
        "ten_day_return": technical.metrics.ten_day_return,
        "relative_volume": technical.metrics.relative_volume,
        "rsi": technical.metrics.rsi,
        "panic_score": technical.panic_score.total_score,
        "combined_score": evaluation.combined_score,
        "signal_type": evaluation.signal_type.value,
        "primary_failure_reason": reasons[0] if reasons else "",
        "warning_count": len(evaluation.warnings),
    }


def export_combined_evaluations(
    evaluations: Iterable[CombinedStrategyEvaluation],
    export_dir: str,
    filename: Optional[str] = None,
) -> List[Path]:
    """Export combined strategy evaluations as deterministic JSON."""
    directory = Path(export_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (filename or "combined-strategy-evaluations.json")
    payload = [combined_evaluation_to_dict(evaluation) for evaluation in evaluations]
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    return [path]
