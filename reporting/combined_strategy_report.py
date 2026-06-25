"""Reporting helpers for combined strategy evaluations."""

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from strategies.combined_graham_technical import CombinedStrategyEvaluation


def combined_evaluation_to_dict(evaluation: CombinedStrategyEvaluation) -> Dict[str, Any]:
    return asdict(evaluation)


def combined_summary_row(evaluation: CombinedStrategyEvaluation) -> Dict[str, Any]:
    technical = evaluation.technical_evaluation
    graham = evaluation.graham_evaluation
    data_issue = ""
    if graham.inputs.market_price is None:
        data_issue = "price unavailable"
    elif graham.inputs.eps is None:
        data_issue = "EPS unavailable"
    elif graham.inputs.shares_outstanding is None:
        data_issue = "shares unavailable"
    return {
        "ticker": evaluation.ticker,
        "price": graham.inputs.market_price,
        "data_ready": not data_issue,
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
        "primary_data_issue": data_issue,
        "primary_graham_failure": graham.disqualification_reasons[0] if graham.disqualification_reasons else "",
        "primary_technical_failure": technical.disqualification_reasons[0] if technical.disqualification_reasons else "",
        "primary_combined_failure": evaluation.disqualification_reasons[0] if evaluation.disqualification_reasons else "",
        "warning_count": len(evaluation.warnings),
    }


def combined_coverage_summary(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(rows)
    return {
        "requested_tickers": total,
        "resolved_tickers": total,
        "price_ready_tickers": sum(1 for row in rows if row["price"] is not None),
        "fundamental_ready_tickers": sum(1 for row in rows if row["data_ready"]),
        "graham_evaluable_tickers": total,
        "technical_evaluable_tickers": sum(1 for row in rows if row["five_day_return"] is not None),
        "combined_evaluable_tickers": sum(1 for row in rows if row["data_ready"] and row["five_day_return"] is not None),
        "graham_qualified_tickers": sum(1 for row in rows if row["graham_qualified"]),
        "technical_qualified_tickers": sum(1 for row in rows if row["technical_qualified"]),
        "combined_qualified_tickers": sum(1 for row in rows if row["combined_qualified"]),
        "missing_data_tickers": sum(1 for row in rows if row["primary_data_issue"]),
        "excluded_tickers": 0,
    }


def export_combined_evaluations(evaluations: Iterable[CombinedStrategyEvaluation], export_dir: str, filename: Optional[str] = None) -> List[Path]:
    directory = Path(export_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (filename or "combined-strategy-evaluations.json")
    path.write_text(json.dumps([combined_evaluation_to_dict(item) for item in evaluations], indent=2, sort_keys=True, default=str), encoding="utf-8")
    return [path]
