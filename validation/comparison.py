"""Fair strategy validation and comparison helpers."""

from dataclasses import asdict
from typing import Any, Callable, Dict, List, Optional, Sequence

from backtesting.engine import run_backtest
from backtesting.models import BacktestConfig
from configurations.models import CombinedStrategyConfig, GrahamStrategyConfig, TechnicalCapitulationConfig, UniverseConfig
from strategies.combined_graham_technical import CombinedGrahamTechnicalStrategy
from strategies.graham_value import GrahamValueStrategy
from strategies.moving_average_reversion import MovingAverageReversionStrategy
from validation.diagnostics import StrategyValidationResult, aggregate_results, result_from_backtest
from validation.periods import ValidationPeriod, validate_development_holdout


def strategy_for_name(strategy: str, combined_config: Optional[CombinedStrategyConfig] = None, strategy_data: Optional[Any] = None) -> Any:
    if strategy == "graham":
        return GrahamValueStrategy(strategy_config=(combined_config.graham if combined_config else GrahamStrategyConfig()), universe_config=UniverseConfig(), reevaluation_frequency="daily", strategy_data=strategy_data)
    if strategy == "technical":
        return MovingAverageReversionStrategy()
    if strategy == "combined":
        config = combined_config or CombinedStrategyConfig()
        graham = GrahamValueStrategy(strategy_config=config.graham, universe_config=UniverseConfig(), reevaluation_frequency="daily", strategy_data=strategy_data)
        return CombinedGrahamTechnicalStrategy(config=config, graham_strategy=graham)
    raise ValueError("strategy must be graham, technical, or combined")


def config_for_strategy(strategy: str, tickers: Sequence[str], period: ValidationPeriod, starting_capital: float, maximum_positions: int, position_size_pct: float, slippage_pct: float, commission: float) -> BacktestConfig:
    names = {"graham": "graham_value_v1", "technical": "moving_average_reversion", "combined": "combined_graham_technical_v1"}
    return BacktestConfig(names[strategy], list(tickers), period.start_date, period.end_date, starting_capital, maximum_positions, position_size_pct, slippage_pct, commission, None)


def run_validation_period(strategy: str, tickers: Sequence[str], period: ValidationPeriod, benchmark: Optional[str] = None, starting_capital: float = 100000.0, maximum_positions: int = 10, position_size_pct: float = 0.10, slippage_pct: float = 0.001, commission: float = 0.0, combined_config: Optional[CombinedStrategyConfig] = None, strategy_data: Optional[Any] = None) -> Dict[str, Any]:
    backtest_config = config_for_strategy(strategy, tickers, period, starting_capital, maximum_positions, position_size_pct, slippage_pct, commission)
    default_strategy_data = __import__("data.strategy_data").strategy_data
    result = run_backtest(backtest_config, strategy_for_name(strategy, combined_config, strategy_data), strategy_data=strategy_data if strategy_data is not None else default_strategy_data, benchmark_ticker=benchmark, persist=False)
    validation = result_from_backtest(strategy, "default", period, len(tickers), len(tickers), result)
    return {"period": asdict(period), "result": asdict(validation), "metrics": result.metrics}


def validate_development_holdout_run(strategy: str, tickers: Sequence[str], development: ValidationPeriod, holdout: ValidationPeriod, **kwargs: Any) -> Dict[str, Any]:
    validate_development_holdout(development, holdout)
    return {
        "strategy": strategy,
        "development": run_validation_period(strategy, tickers, development, **kwargs),
        "holdout": run_validation_period(strategy, tickers, holdout, **kwargs),
        "warnings": ["holdout was evaluated separately; do not tune thresholds on holdout performance"],
    }


def validate_across_periods(strategy: str, tickers: Sequence[str], periods: Sequence[ValidationPeriod], **kwargs: Any) -> Dict[str, Any]:
    rows = [run_validation_period(strategy, tickers, period, **kwargs) for period in periods]
    summary = aggregate_results(strategy, [StrategyValidationResult(**row["result"]) for row in rows])
    return {"strategy": strategy, "period_results": rows, "summary": asdict(summary)}


def compare_strategies_fair(tickers: Sequence[str], period: ValidationPeriod, **kwargs: Any) -> Dict[str, Any]:
    rows = []
    for strategy in ("graham", "technical", "combined"):
        try:
            rows.append(run_validation_period(strategy, tickers, period, **kwargs))
        except ValueError as exc:
            rows.append({"strategy": strategy, "requested_tickers": len(tickers), "evaluated_tickers": 0, "skipped_tickers": len(tickers), "error": str(exc)})
    return {
        "requested_tickers": len(tickers),
        "resolved_tickers": len(tickers),
        "coverage_warning": "",
        "strategies": rows,
    }
