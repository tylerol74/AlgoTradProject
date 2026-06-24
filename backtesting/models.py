"""Standardized dataclasses shared by strategies and backtests."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class SignalAction(str, Enum):
    """Supported strategy signal actions."""

    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class OrderSide(str, Enum):
    """Supported executable order sides."""

    BUY = "BUY"
    SELL = "SELL"


@dataclass(frozen=True)
class Signal:
    """A strategy recommendation for a ticker on a date."""

    ticker: str
    signal_date: str
    strategy: str
    action: SignalAction
    score: float
    reason: str


@dataclass(frozen=True)
class Order:
    """A pending order scheduled from a signal for a future execution date."""

    ticker: str
    side: OrderSide
    quantity: int
    signal_date: str
    execution_date: str
    strategy: str
    score: float
    reason: str


@dataclass(frozen=True)
class Position:
    """An open position snapshot."""

    ticker: str
    strategy: str
    quantity: float
    entry_date: str
    entry_price: float
    current_price: float
    signal_date: str = ""
    entry_commission: float = 0.0
    signal_score: float = 0.0
    reason: str = ""


@dataclass(frozen=True)
class Trade:
    """A completed trade record."""

    ticker: str
    strategy: str
    signal_date: str
    entry_date: str
    entry_price: float
    exit_date: str
    exit_price: float
    quantity: float
    gross_pnl: float
    net_pnl: float
    return_pct: float
    exit_reason: str
    entry_commission: float = 0.0
    exit_commission: float = 0.0


@dataclass(frozen=True)
class PortfolioSnapshot:
    """End-of-day portfolio value snapshot."""

    snapshot_date: str
    cash: float
    holdings_value: float
    total_value: float
    drawdown: float


@dataclass(frozen=True)
class BacktestConfig:
    """Configuration inputs for the backtest engine."""

    strategy_name: str
    tickers: List[str]
    start_date: str
    end_date: str
    starting_capital: float
    maximum_positions: int
    position_size_pct: float
    slippage_pct: float
    commission_per_trade: float
    maximum_holding_days: Optional[int]


@dataclass(frozen=True)
class BacktestResult:
    """Structured result from a completed backtest."""

    backtest_id: Optional[int]
    config: BacktestConfig
    trades: List[Trade]
    portfolio_snapshots: List[PortfolioSnapshot]
    metrics: Dict[str, Any] = field(default_factory=dict)
