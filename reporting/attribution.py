"""Ticker and exit-reason attribution for completed trades."""

from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List


def _holding_days(trade: Dict[str, Any]) -> int:
    return (datetime.strptime(trade["exit_date"], "%Y-%m-%d").date() - datetime.strptime(trade["entry_date"], "%Y-%m-%d").date()).days


def calculate_ticker_attribution(
    trades: List[Dict[str, Any]],
    positive_concentration_threshold: float = 0.50,
    loss_concentration_threshold: float = 0.50,
) -> Dict[str, Any]:
    """Calculate deterministic ticker-level performance attribution."""
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for trade in trades:
        grouped[trade["ticker"]].append(trade)
    total_trades = len(trades)
    total_net = sum(float(trade.get("pnl", trade.get("net_pnl", 0.0))) for trade in trades)
    positive_total = sum(float(trade.get("pnl", trade.get("net_pnl", 0.0))) for trade in trades if float(trade.get("pnl", trade.get("net_pnl", 0.0))) > 0)
    total_losses = abs(sum(float(trade.get("pnl", trade.get("net_pnl", 0.0))) for trade in trades if float(trade.get("pnl", trade.get("net_pnl", 0.0))) < 0))
    rows = []
    warnings = []
    for ticker, ticker_trades in grouped.items():
        pnls = [float(trade.get("pnl", trade.get("net_pnl", 0.0))) for trade in ticker_trades]
        returns = [float(trade.get("return_pct", 0.0)) for trade in ticker_trades]
        wins = [pnl for pnl in pnls if pnl > 0]
        losses = [pnl for pnl in pnls if pnl < 0]
        net_pnl = sum(pnls)
        gross_profit = sum(wins)
        gross_loss = sum(losses)
        row = {
            "ticker": ticker,
            "completed_trade_count": len(ticker_trades),
            "winning_trades": len(wins),
            "losing_trades": len(losses),
            "win_rate": len(wins) / len(ticker_trades) if ticker_trades else 0.0,
            "gross_profit": gross_profit,
            "gross_loss": gross_loss,
            "net_pnl": net_pnl,
            "average_return_per_trade": sum(returns) / len(returns) if returns else 0.0,
            "average_holding_period": sum(_holding_days(trade) for trade in ticker_trades) / len(ticker_trades),
            "best_trade_return": max(returns) if returns else 0.0,
            "worst_trade_return": min(returns) if returns else 0.0,
            "contribution_to_total_pnl": net_pnl / total_net if total_net != 0 else 0.0,
            "percentage_of_total_trades": len(ticker_trades) / total_trades if total_trades else 0.0,
        }
        if positive_total > 0 and gross_profit / positive_total > positive_concentration_threshold:
            warnings.append(f"{ticker} contributes more than {positive_concentration_threshold:.0%} of positive P&L")
        if total_losses > 0 and abs(gross_loss) / total_losses > loss_concentration_threshold:
            warnings.append(f"{ticker} accounts for more than {loss_concentration_threshold:.0%} of total losses")
        rows.append(row)
    rows.sort(key=lambda item: (-item["net_pnl"], item["ticker"]))
    return {"rows": rows, "warnings": warnings}


def analyze_exit_reasons(trades: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Group completed trades by exit reason."""
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for trade in trades:
        grouped[trade.get("exit_reason") or "UNKNOWN"].append(trade)
    total = len(trades)
    rows = []
    for reason, reason_trades in grouped.items():
        pnls = [float(trade.get("pnl", trade.get("net_pnl", 0.0))) for trade in reason_trades]
        returns = [float(trade.get("return_pct", 0.0)) for trade in reason_trades]
        wins = [pnl for pnl in pnls if pnl > 0]
        losses = [pnl for pnl in pnls if pnl < 0]
        rows.append({
            "exit_reason": reason,
            "trade_count": len(reason_trades),
            "percentage_of_all_trades": len(reason_trades) / total if total else 0.0,
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": len(wins) / len(reason_trades) if reason_trades else 0.0,
            "net_pnl": sum(pnls),
            "average_return": sum(returns) / len(returns) if returns else 0.0,
            "average_holding_period": sum(_holding_days(trade) for trade in reason_trades) / len(reason_trades),
        })
    rows.sort(key=lambda item: (-item["trade_count"], item["exit_reason"]))
    return rows

