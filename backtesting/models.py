"""Standardized dataclasses shared by strategies and future backtests."""

from dataclasses import dataclass
from enum import Enum
from typing import List, Optional


class SignalAction(str, Enum):
    """Supported strategy signal actions."""

    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


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
class Position:
    """A read-only position snapshot for exit-signal evaluation."""

    ticker: str
    strategy: str
    quantity: float
    entry_date: str
    entry_price: float
    current_price: float


@dataclass(frozen=True)
class Trade:
    """A completed trade record for future backtest reporting."""

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


@dataclass(frozen=True)
class BacktestConfig:
    """Configuration inputs for a future backtest engine."""

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
