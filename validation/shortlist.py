"""Opportunity shortlist ranking."""

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass(frozen=True)
class OpportunityRankingConfig:
    graham_weight: float = 0.20
    margin_of_safety_weight: float = 0.20
    panic_weight: float = 0.20
    liquidity_weight: float = 0.10
    data_quality_weight: float = 0.15
    combined_score_weight: float = 0.15


@dataclass(frozen=True)
class OpportunityRankingResult:
    ticker: str
    ranking_score: float
    component_scores: Dict[str, float]
    raw_values: Dict[str, Any]
    qualified: bool
    hard_disqualified: bool
    rank: int
    explanation: str


def _scale(value: Any, maximum: float = 100.0) -> float:
    if value is None:
        return 0.0
    return max(0.0, min(100.0, float(value) / maximum * 100.0))


def rank_rows(rows: List[Dict[str, Any]], config: OpportunityRankingConfig = OpportunityRankingConfig()) -> List[OpportunityRankingResult]:
    ranked: List[OpportunityRankingResult] = []
    for row in rows:
        hard = bool(row.get("primary_data_issue")) or bool(row.get("primary_failure_reason"))
        components = {
            "graham": _scale(row.get("graham_score")),
            "margin_of_safety": _scale((row.get("margin_of_safety") or 0.0) * 100.0),
            "panic": _scale(row.get("panic_score"), 15.0),
            "liquidity": _scale(row.get("relative_volume"), 3.0),
            "data_quality": _scale(row.get("data_quality_score")),
            "combined_score": _scale(row.get("combined_score")),
        }
        score = (
            components["graham"] * config.graham_weight
            + components["margin_of_safety"] * config.margin_of_safety_weight
            + components["panic"] * config.panic_weight
            + components["liquidity"] * config.liquidity_weight
            + components["data_quality"] * config.data_quality_weight
            + components["combined_score"] * config.combined_score_weight
        )
        if hard:
            score = min(score, 0.0)
        ranked.append(OpportunityRankingResult(row["ticker"], round(score, 6), components, row, bool(row.get("qualified") or row.get("combined_qualified") or row.get("graham_qualified") or row.get("technical_qualified")), hard, 0, "hard disqualified" if hard else "weighted transparent component score"))
    ordered = sorted(ranked, key=lambda item: (-item.ranking_score, item.ticker))
    return [OpportunityRankingResult(item.ticker, item.ranking_score, item.component_scores, item.raw_values, item.qualified, item.hard_disqualified, index + 1, item.explanation) for index, item in enumerate(ordered)]


def shortlist_summary(requested: int, resolved: int, evaluated: int, ranked: List[OpportunityRankingResult]) -> Dict[str, Any]:
    reasons: Dict[str, int] = {}
    for item in ranked:
        reason = item.raw_values.get("primary_failure_reason") or item.raw_values.get("primary_combined_failure") or item.raw_values.get("primary_data_issue") or ""
        if reason:
            reasons[reason] = reasons.get(reason, 0) + 1
    return {
        "requested": requested,
        "resolved": resolved,
        "evaluated": evaluated,
        "data_ready": sum(1 for item in ranked if not item.raw_values.get("primary_data_issue")),
        "qualified": sum(1 for item in ranked if item.qualified),
        "returned": len(ranked),
        "excluded": sum(1 for item in ranked if item.hard_disqualified),
        "missing_data": sum(1 for item in ranked if item.raw_values.get("primary_data_issue")),
        "top_disqualification_reasons": reasons,
    }
