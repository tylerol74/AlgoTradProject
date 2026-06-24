"""Dataclasses for deterministic development/validation experiments."""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

DEFAULT_MAX_PARAMETER_SETS = 20


@dataclass(frozen=True)
class ParameterSet:
    """Explicit strategy and portfolio parameters for one experiment run."""

    name: str
    ma_window: int
    entry_distance_pct: float
    stop_loss_pct: float
    maximum_holding_days: int
    position_size_pct: float
    maximum_positions: int
    slippage_pct: float
    commission: float


@dataclass(frozen=True)
class ExperimentConfig:
    """Development/validation experiment definition."""

    name: str
    strategy_name: str
    tickers: List[str]
    development_start: str
    development_end: str
    validation_start: str
    validation_end: str
    starting_capital: float
    benchmark_ticker: Optional[str]
    parameter_sets: List[ParameterSet]
    persist_runs: bool = True

    def validate(self, max_parameter_sets: int = DEFAULT_MAX_PARAMETER_SETS) -> None:
        """Validate experiment structure without running backtests."""
        if not self.tickers:
            raise ValueError("tickers cannot be empty")
        normalized = [ticker.strip().upper() for ticker in self.tickers]
        if len(normalized) != len(set(normalized)):
            raise ValueError("duplicate tickers are not allowed")
        if self.development_end >= self.validation_start:
            raise ValueError("development and validation ranges must not overlap")
        if self.development_start > self.development_end or self.validation_start > self.validation_end:
            raise ValueError("invalid date range")
        if len(self.parameter_sets) > max_parameter_sets:
            raise ValueError("too many parameter sets")
        names = [parameter_set.name for parameter_set in self.parameter_sets]
        if len(names) != len(set(names)):
            raise ValueError("parameter-set names must be unique")
        if not self.parameter_sets:
            raise ValueError("at least one parameter set is required")


@dataclass(frozen=True)
class ExperimentRunResult:
    """Development and validation results for one parameter set."""

    parameter_set_name: str
    development_backtest_id: Optional[int]
    validation_backtest_id: Optional[int]
    development_metrics: Dict[str, Any]
    validation_metrics: Dict[str, Any]
    warnings: List[str]


def parameter_set_from_dict(payload: Dict[str, Any]) -> ParameterSet:
    """Create a ParameterSet from JSON-decoded data."""
    return ParameterSet(
        name=payload["name"],
        ma_window=int(payload["ma_window"]),
        entry_distance_pct=float(payload["entry_distance_pct"]),
        stop_loss_pct=float(payload["stop_loss_pct"]),
        maximum_holding_days=int(payload["maximum_holding_days"]),
        position_size_pct=float(payload["position_size_pct"]),
        maximum_positions=int(payload["maximum_positions"]),
        slippage_pct=float(payload["slippage_pct"]),
        commission=float(payload["commission"]),
    )


def experiment_config_from_dict(payload: Dict[str, Any]) -> ExperimentConfig:
    """Create an ExperimentConfig from JSON-decoded data."""
    config = ExperimentConfig(
        name=payload["name"],
        strategy_name=payload["strategy_name"],
        tickers=list(payload["tickers"]),
        development_start=payload["development_start"],
        development_end=payload["development_end"],
        validation_start=payload["validation_start"],
        validation_end=payload["validation_end"],
        starting_capital=float(payload["starting_capital"]),
        benchmark_ticker=payload.get("benchmark_ticker"),
        persist_runs=bool(payload.get("persist_runs", True)),
        parameter_sets=[parameter_set_from_dict(item) for item in payload["parameter_sets"]],
    )
    config.validate()
    return config
