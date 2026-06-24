"""Backtest performance metrics."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from backtesting.models import PortfolioSnapshot, Trade


def calculate_metrics(
    starting_capital: float,
    ending_value: float,
    trades: List[Trade],
    snapshots: List[PortfolioSnapshot],
    total_commissions: float = 0.0,
) -> Dict[str, Any]:
    """Calculate structured backtest performance metrics."""
    if starting_capital <= 0:
        raise ValueError("starting_capital must be positive")
    returns = [trade.return_pct for trade in trades]
    winning = [trade for trade in trades if trade.net_pnl > 0]
    losing = [trade for trade in trades if trade.net_pnl < 0]
    breakeven = [trade for trade in trades if trade.net_pnl == 0]
    gross_profit = sum(trade.net_pnl for trade in winning)
    gross_loss = sum(trade.net_pnl for trade in losing)
    profit_factor: Optional[float]
    if gross_loss == 0:
        profit_factor = None
    else:
        profit_factor = gross_profit / abs(gross_loss)
    holding_periods = [
        (datetime.strptime(trade.exit_date, "%Y-%m-%d").date() - datetime.strptime(trade.entry_date, "%Y-%m-%d").date()).days
        for trade in trades
    ]
    invested_days = sum(1 for snapshot in snapshots if snapshot.holdings_value > 0)
    return {
        "starting_capital": starting_capital,
        "ending_portfolio_value": ending_value,
        "total_return_pct": (ending_value - starting_capital) / starting_capital,
        "completed_trade_count": len(trades),
        "winning_trades": len(winning),
        "losing_trades": len(losing),
        "breakeven_trades": len(breakeven),
        "win_rate": len(winning) / len(trades) if trades else 0.0,
        "average_trade_return": sum(returns) / len(returns) if returns else 0.0,
        "average_winning_trade_return": sum(t.return_pct for t in winning) / len(winning) if winning else 0.0,
        "average_losing_trade_return": sum(t.return_pct for t in losing) / len(losing) if losing else 0.0,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": profit_factor,
        "maximum_drawdown": min((snapshot.drawdown for snapshot in snapshots), default=0.0),
        "average_holding_period_days": sum(holding_periods) / len(holding_periods) if holding_periods else 0.0,
        "total_commissions": total_commissions,
        "capital_invested_pct_time": invested_days / len(snapshots) if snapshots else 0.0,
    }


def calculate_benchmark_return(history: List[dict], start_date: str, end_date: str) -> Dict[str, Any]:
    """Calculate buy-and-hold benchmark return from stored close prices."""
    rows = [row for row in history if start_date <= row["trade_date"] <= end_date]
    if not rows:
        return {"return_pct": None, "reason": "benchmark data missing"}
    first = rows[0]
    last = rows[-1]
    if first["close"] <= 0:
        return {"return_pct": None, "reason": "benchmark start close invalid"}
    return {
        "return_pct": (last["close"] - first["close"]) / first["close"],
        "start_date": first["trade_date"],
        "end_date": last["trade_date"],
    }
