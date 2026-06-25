"""Nearby-value sensitivity testing without optimization."""

from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Sequence

from configurations.models import CombinedStrategyConfig, GrahamStrategyConfig, TechnicalCapitulationConfig
from validation.comparison import run_validation_period
from validation.periods import ValidationPeriod


@dataclass(frozen=True)
class SensitivityResult:
    parameter_name: str
    baseline_value: Any
    tested_value: Any
    change_pct: Any
    result_metrics: Dict[str, Any]
    difference_from_baseline: Dict[str, Any]
    stability_classification: str


DEFAULT_VALUES = {
    "minimum_margin_of_safety": [0.25, 0.35],
    "minimum_graham_score": [65.0, 75.0],
    "minimum_data_quality_score": [55.0, 65.0],
    "minimum_five_day_decline": [0.08, 0.12],
    "minimum_relative_volume": [1.3, 1.7],
    "maximum_rsi": [30.0, 40.0],
    "minimum_panic_score": [6.0, 8.0],
    "confirmation_window_days": [5, 15],
    "minimum_combined_score": [65.0, 75.0],
}


def _with_parameter(config: CombinedStrategyConfig, name: str, value: Any) -> CombinedStrategyConfig:
    graham = config.graham
    technical = config.technical
    kwargs = {
        "combination_mode": config.combination_mode,
        "graham_weight": config.graham_weight,
        "technical_weight": config.technical_weight,
        "minimum_combined_score": config.minimum_combined_score,
        "require_graham_first": config.require_graham_first,
        "graham_signal_validity_days": config.graham_signal_validity_days,
        "technical_signal_validity_days": config.technical_signal_validity_days,
    }
    if hasattr(graham, name):
        graham = GrahamStrategyConfig(**{**asdict(graham), name: value})
    elif hasattr(technical, name):
        technical = TechnicalCapitulationConfig(**{**asdict(technical), name: value})
    elif name in kwargs:
        kwargs[name] = value
    else:
        raise ValueError(f"unsupported sensitivity parameter: {name}")
    return CombinedStrategyConfig(graham=graham, technical=technical, **kwargs)


def _classify(base: Dict[str, Any], test: Dict[str, Any]) -> str:
    base_trades = base.get("completed_trade_count") or 0
    test_trades = test.get("completed_trade_count") or 0
    if base_trades < 5 and test_trades < 5:
        return "insufficient evidence"
    trade_delta = abs(test_trades - base_trades) / float(max(base_trades, 1))
    return_delta = abs((test.get("total_return_pct") or 0.0) - (base.get("total_return_pct") or 0.0))
    if trade_delta >= 0.75 or return_delta >= 0.20:
        return "highly sensitive"
    if trade_delta >= 0.30 or return_delta >= 0.10:
        return "moderately sensitive"
    return "stable"


def run_sensitivity(strategy: str, tickers: Sequence[str], period: ValidationPeriod, parameter: str, values: Sequence[Any], baseline_config: CombinedStrategyConfig = CombinedStrategyConfig(), **kwargs: Any) -> Dict[str, Any]:
    baseline = run_validation_period(strategy, tickers, period, combined_config=baseline_config, **kwargs)
    baseline_metrics = baseline["metrics"]
    baseline_value = getattr(baseline_config, parameter, None)
    if baseline_value is None and hasattr(baseline_config.graham, parameter):
        baseline_value = getattr(baseline_config.graham, parameter)
    if baseline_value is None and hasattr(baseline_config.technical, parameter):
        baseline_value = getattr(baseline_config.technical, parameter)
    rows: List[SensitivityResult] = []
    for value in values:
        test_config = _with_parameter(baseline_config, parameter, value)
        result = run_validation_period(strategy, tickers, period, combined_config=test_config, **kwargs)
        metrics = result["metrics"]
        rows.append(
            SensitivityResult(
                parameter,
                baseline_value,
                value,
                None if not baseline_value else (float(value) - float(baseline_value)) / abs(float(baseline_value)),
                metrics,
                {
                    "total_return_delta": (metrics.get("total_return_pct") or 0.0) - (baseline_metrics.get("total_return_pct") or 0.0),
                    "trade_count_delta": (metrics.get("completed_trade_count") or 0) - (baseline_metrics.get("completed_trade_count") or 0),
                },
                _classify(baseline_metrics, metrics),
            )
        )
    return {"baseline": baseline, "results": [asdict(row) for row in rows], "optimization_performed": False}
