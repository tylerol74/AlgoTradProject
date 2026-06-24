"""Dataclass configuration models for strategy workflows."""

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass(frozen=True)
class GrahamStrategyConfig:
    minimum_margin_of_safety: float = 0.30
    minimum_graham_score: float = 70.0
    minimum_data_quality_score: float = 60.0
    minimum_profitable_years: int = 4
    exclude_financials: bool = True
    exclude_reits: bool = True


@dataclass(frozen=True)
class UniverseConfig:
    minimum_price: float = 3.00
    minimum_market_cap: float = 300_000_000.0
    minimum_average_dollar_volume: float = 2_000_000.0
    tickers: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class PortfolioConfig:
    starting_capital: float = 100_000.0
    maximum_positions: int = 10
    position_size_pct: float = 0.10


@dataclass(frozen=True)
class ExecutionConfig:
    slippage_pct: float = 0.001
    commission: float = 0.0
    execution_timing: str = "next_open"


@dataclass(frozen=True)
class BacktestSettings:
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    benchmark: str = "AAPL"


@dataclass(frozen=True)
class SavedStrategyConfig:
    name: str
    description: str = ""
    strategy_type: str = "graham_value_v1"
    strategy: GrahamStrategyConfig = field(default_factory=GrahamStrategyConfig)
    universe: UniverseConfig = field(default_factory=UniverseConfig)
    portfolio: PortfolioConfig = field(default_factory=PortfolioConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    backtest: BacktestSettings = field(default_factory=BacktestSettings)
    config_version: int = 1
