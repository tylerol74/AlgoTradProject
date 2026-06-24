"""Deterministic development/validation experiment runner."""

from dataclasses import asdict
from typing import Any, Dict, List

from backtesting.engine import run_backtest
from backtesting.models import BacktestConfig
from experiments.models import ExperimentConfig, ExperimentRunResult, ParameterSet
from reporting.attribution import calculate_ticker_attribution
from strategies.moving_average_reversion import MovingAverageReversionStrategy

LOW_VALIDATION_TRADE_THRESHOLD = 3
MATERIAL_RETURN_DIFFERENCE = 0.20


def _backtest_config(config: ExperimentConfig, parameter_set: ParameterSet, start_date: str, end_date: str) -> BacktestConfig:
    return BacktestConfig(
        strategy_name=config.strategy_name,
        tickers=list(config.tickers),
        start_date=start_date,
        end_date=end_date,
        starting_capital=config.starting_capital,
        maximum_positions=parameter_set.maximum_positions,
        position_size_pct=parameter_set.position_size_pct,
        slippage_pct=parameter_set.slippage_pct,
        commission_per_trade=parameter_set.commission,
        maximum_holding_days=parameter_set.maximum_holding_days,
    )


def _strategy(parameter_set: ParameterSet) -> MovingAverageReversionStrategy:
    return MovingAverageReversionStrategy(
        moving_average_window=parameter_set.ma_window,
        entry_discount_pct=parameter_set.entry_distance_pct,
        stop_loss_pct=parameter_set.stop_loss_pct,
        maximum_holding_days=parameter_set.maximum_holding_days,
    )


def _metric_subset(metrics: Dict[str, Any]) -> Dict[str, Any]:
    benchmark = metrics.get("benchmark") or {}
    benchmark_return = benchmark.get("return_pct") if isinstance(benchmark, dict) else None
    total_return = metrics.get("total_return_pct")
    return {
        "total_return": total_return,
        "maximum_drawdown": metrics.get("maximum_drawdown"),
        "trade_count": metrics.get("completed_trade_count"),
        "win_rate": metrics.get("win_rate"),
        "benchmark_return": benchmark_return,
        "excess_return": None if total_return is None or benchmark_return is None else total_return - benchmark_return,
    }


def _warnings(development: Dict[str, Any], validation: Dict[str, Any], validation_trades: List[Any]) -> List[str]:
    warnings = []
    if development["total_return"] is not None and validation["total_return"] is not None:
        if development["total_return"] > 0 and validation["total_return"] < 0:
            warnings.append("development return is positive but validation return is negative")
        if abs(development["total_return"] - validation["total_return"]) >= MATERIAL_RETURN_DIFFERENCE:
            warnings.append("development and validation returns differ materially")
    if development["excess_return"] is not None and validation["excess_return"] is not None:
        if development["excess_return"] > 0 and validation["excess_return"] < 0:
            warnings.append("development excess return is positive but validation excess return is negative")
    if (validation.get("trade_count") or 0) < LOW_VALIDATION_TRADE_THRESHOLD:
        warnings.append("validation trade count is low")
    attribution = calculate_ticker_attribution([trade.__dict__ if hasattr(trade, "__dict__") else trade for trade in validation_trades])
    warnings.extend(f"validation {warning}" for warning in attribution["warnings"])
    return warnings


def run_experiment(config: ExperimentConfig) -> List[ExperimentRunResult]:
    """Run each explicit parameter set on development and validation periods."""
    config.validate()
    results: List[ExperimentRunResult] = []
    for parameter_set in config.parameter_sets:
        development_result = run_backtest(
            _backtest_config(config, parameter_set, config.development_start, config.development_end),
            _strategy(parameter_set),
            benchmark_ticker=config.benchmark_ticker,
            persist=config.persist_runs,
        )
        validation_result = run_backtest(
            _backtest_config(config, parameter_set, config.validation_start, config.validation_end),
            _strategy(parameter_set),
            benchmark_ticker=config.benchmark_ticker,
            persist=config.persist_runs,
        )
        development_metrics = _metric_subset(development_result.metrics)
        validation_metrics = _metric_subset(validation_result.metrics)
        results.append(
            ExperimentRunResult(
                parameter_set_name=parameter_set.name,
                development_backtest_id=development_result.backtest_id,
                validation_backtest_id=validation_result.backtest_id,
                development_metrics=development_metrics,
                validation_metrics=validation_metrics,
                warnings=_warnings(development_metrics, validation_metrics, validation_result.trades),
            )
        )
    return results


def experiment_summary(config: ExperimentConfig, results: List[ExperimentRunResult]) -> Dict[str, Any]:
    """Convert experiment results into export-friendly rows."""
    rows = []
    for result in results:
        rows.append({
            "parameter_set_name": result.parameter_set_name,
            "development_backtest_id": result.development_backtest_id,
            "validation_backtest_id": result.validation_backtest_id,
            "development_total_return": result.development_metrics.get("total_return"),
            "validation_total_return": result.validation_metrics.get("total_return"),
            "development_maximum_drawdown": result.development_metrics.get("maximum_drawdown"),
            "validation_maximum_drawdown": result.validation_metrics.get("maximum_drawdown"),
            "development_trade_count": result.development_metrics.get("trade_count"),
            "validation_trade_count": result.validation_metrics.get("trade_count"),
            "development_win_rate": result.development_metrics.get("win_rate"),
            "validation_win_rate": result.validation_metrics.get("win_rate"),
            "development_benchmark_return": result.development_metrics.get("benchmark_return"),
            "validation_benchmark_return": result.validation_metrics.get("benchmark_return"),
            "development_excess_return": result.development_metrics.get("excess_return"),
            "validation_excess_return": result.validation_metrics.get("excess_return"),
            "warnings": "; ".join(result.warnings),
        })
    return {"name": config.name, "config": asdict(config), "results": rows}
