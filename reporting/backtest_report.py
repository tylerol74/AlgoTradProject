"""Detailed review tools for one saved backtest."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from database import repositories
from reporting.attribution import analyze_exit_reasons, calculate_ticker_attribution

ACCOUNTING_TOLERANCE = 0.01


@dataclass(frozen=True)
class BacktestReport:
    """Structured report for one saved backtest."""

    backtest_id: int
    run: Dict[str, Any]
    parameters: Dict[str, Any]
    performance: Dict[str, Any]
    trade_statistics: Dict[str, Any]
    data_integrity: Dict[str, Any]
    ticker_attribution: Dict[str, Any]
    exit_reasons: List[Dict[str, Any]]
    daily_returns: List[Dict[str, Any]]
    monthly_returns: List[Dict[str, Any]]
    yearly_returns: List[Dict[str, Any]]
    equity_curve: List[Dict[str, Any]]
    warnings: List[str]


def _holding_days(trade: Dict[str, Any]) -> int:
    return (datetime.strptime(trade["exit_date"], "%Y-%m-%d").date() - datetime.strptime(trade["entry_date"], "%Y-%m-%d").date()).days


def _trade_stats(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not trades:
        return {
            "best_trade": None,
            "worst_trade": None,
            "longest_held_trade": None,
            "shortest_held_trade": None,
            "largest_gross_profit": None,
            "largest_gross_loss": None,
        }
    return {
        "best_trade": max(trades, key=lambda trade: (trade["return_pct"], trade["ticker"])),
        "worst_trade": min(trades, key=lambda trade: (trade["return_pct"], trade["ticker"])),
        "longest_held_trade": max(trades, key=lambda trade: (_holding_days(trade), trade["ticker"])),
        "shortest_held_trade": min(trades, key=lambda trade: (_holding_days(trade), trade["ticker"])),
        "largest_gross_profit": max(trades, key=lambda trade: (trade["pnl"], trade["ticker"])),
        "largest_gross_loss": min(trades, key=lambda trade: (trade["pnl"], trade["ticker"])),
    }


def build_equity_curve(snapshots: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Build equity/drawdown series suitable for later plotting."""
    if not snapshots:
        return {"rows": [], "warnings": ["no snapshots available"]}
    first_value = float(snapshots[0]["total_value"])
    running_peak = first_value
    rows = []
    warnings = []
    for snapshot in snapshots:
        cash = float(snapshot["cash"])
        holdings = float(snapshot["holdings_value"])
        total = float(snapshot["total_value"])
        if abs(cash + holdings - total) > ACCOUNTING_TOLERANCE:
            warnings.append(f"snapshot accounting inconsistency on {snapshot['snapshot_date']}")
        running_peak = max(running_peak, total)
        rows.append({
            "snapshot_date": snapshot["snapshot_date"],
            "total_value": total,
            "normalized_equity": total / first_value if first_value else None,
            "running_peak": running_peak,
            "drawdown": (total - running_peak) / running_peak if running_peak else 0.0,
            "cash_pct": cash / total if total else 0.0,
            "holdings_pct": holdings / total if total else 0.0,
        })
    return {"rows": rows, "warnings": warnings}


def calculate_daily_returns(snapshots: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Calculate returns from consecutive stored snapshots."""
    rows = []
    prior = None
    for snapshot in snapshots:
        total = float(snapshot["total_value"])
        rows.append({
            "snapshot_date": snapshot["snapshot_date"],
            "daily_return": None if prior is None else total / prior - 1.0,
        })
        prior = total
    return rows


def calculate_monthly_returns(snapshots: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Calculate monthly returns from last stored value of each month."""
    month_ends: Dict[str, Dict[str, Any]] = {}
    for snapshot in snapshots:
        month = snapshot["snapshot_date"][:7]
        month_ends[month] = snapshot
    rows = []
    prior_value: Optional[float] = None
    for month in sorted(month_ends):
        value = float(month_ends[month]["total_value"])
        month_snapshots = [snapshot for snapshot in snapshots if snapshot["snapshot_date"].startswith(month)]
        monthly_drawdown = min(float(snapshot["drawdown"]) for snapshot in month_snapshots) if month_snapshots else 0.0
        rows.append({
            "month": month,
            "ending_value": value,
            "monthly_return": None if prior_value is None else value / prior_value - 1.0,
            "monthly_max_drawdown": monthly_drawdown,
        })
        prior_value = value
    return rows


def calculate_yearly_returns(snapshots: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Calculate calendar-year returns from stored snapshots."""
    year_ends: Dict[str, Dict[str, Any]] = {}
    for snapshot in snapshots:
        year_ends[snapshot["snapshot_date"][:4]] = snapshot
    rows = []
    prior_value: Optional[float] = None
    for year in sorted(year_ends):
        value = float(year_ends[year]["total_value"])
        rows.append({"year": year, "ending_value": value, "yearly_return": None if prior_value is None else value / prior_value - 1.0})
        prior_value = value
    return rows


def _rolling_calendar_return(snapshots: List[Dict[str, Any]], days: int) -> List[Dict[str, Any]]:
    rows = []
    parsed = [(datetime.strptime(snapshot["snapshot_date"], "%Y-%m-%d").date(), snapshot) for snapshot in snapshots]
    for index, (current_date, snapshot) in enumerate(parsed):
        prior_candidates = [(date, snap) for date, snap in parsed[:index] if (current_date - date).days >= days]
        if not prior_candidates:
            rows.append({"snapshot_date": snapshot["snapshot_date"], f"rolling_{days}_day_return": None})
            continue
        prior = prior_candidates[-1][1]
        rows.append({"snapshot_date": snapshot["snapshot_date"], f"rolling_{days}_day_return": float(snapshot["total_value"]) / float(prior["total_value"]) - 1.0})
    return rows


def create_backtest_report(backtest_id: int) -> BacktestReport:
    """Load and analyze one saved backtest without mutating saved data."""
    bundle = repositories.get_backtest_bundle(backtest_id)
    run = bundle["run"]
    trades = bundle["trades"]
    snapshots = bundle["snapshots"]
    parameters = bundle["parameters"]
    metrics = parameters.get("metrics", {})
    benchmark = metrics.get("benchmark") or parameters.get("benchmark") or {}
    total_return = metrics.get("total_return_pct")
    benchmark_return = benchmark.get("return_pct") if isinstance(benchmark, dict) else None
    warnings = []
    if not trades:
        warnings.append("no trades loaded")
    if not snapshots:
        warnings.append("no snapshots loaded")
    if snapshots and run.get("ending_capital") is not None and abs(float(snapshots[-1]["total_value"]) - float(run["ending_capital"])) > ACCOUNTING_TOLERANCE:
        warnings.append("final snapshot value differs from run ending capital")
    equity = build_equity_curve(snapshots)
    warnings.extend(equity["warnings"])
    attribution = calculate_ticker_attribution(trades)
    warnings.extend(attribution["warnings"])
    performance = {
        "total_return": total_return,
        "benchmark_return": benchmark_return,
        "excess_return": None if total_return is None or benchmark_return is None else total_return - benchmark_return,
        "maximum_drawdown": metrics.get("maximum_drawdown"),
        "completed_trades": metrics.get("completed_trade_count", len(trades)),
        "win_rate": metrics.get("win_rate"),
        "profit_factor": metrics.get("profit_factor"),
        "average_trade_return": metrics.get("average_trade_return"),
        "average_winner": metrics.get("average_winning_trade_return"),
        "average_loser": metrics.get("average_losing_trade_return"),
        "average_holding_period": metrics.get("average_holding_period_days"),
        "total_commissions": metrics.get("total_commissions"),
        "capital_utilization": metrics.get("capital_invested_pct_time"),
    }
    data_integrity = {
        "trade_count_loaded": len(trades),
        "snapshot_count_loaded": len(snapshots),
        "first_snapshot_date": snapshots[0]["snapshot_date"] if snapshots else None,
        "final_snapshot_date": snapshots[-1]["snapshot_date"] if snapshots else None,
        "warnings": list(warnings),
    }
    return BacktestReport(
        backtest_id=backtest_id,
        run=run,
        parameters=parameters,
        performance=performance,
        trade_statistics=_trade_stats(trades),
        data_integrity=data_integrity,
        ticker_attribution=attribution,
        exit_reasons=analyze_exit_reasons(trades),
        daily_returns=calculate_daily_returns(snapshots),
        monthly_returns=calculate_monthly_returns(snapshots),
        yearly_returns=calculate_yearly_returns(snapshots),
        equity_curve=equity["rows"],
        warnings=warnings,
    )


def report_to_dict(report: BacktestReport) -> Dict[str, Any]:
    """Convert a report dataclass into JSON-friendly dictionaries."""
    return {
        "backtest_id": report.backtest_id,
        "run": report.run,
        "parameters": report.parameters,
        "performance": report.performance,
        "trade_statistics": report.trade_statistics,
        "data_integrity": report.data_integrity,
        "ticker_attribution": report.ticker_attribution,
        "exit_reasons": report.exit_reasons,
        "daily_returns": report.daily_returns,
        "monthly_returns": report.monthly_returns,
        "yearly_returns": report.yearly_returns,
        "equity_curve": report.equity_curve,
        "warnings": report.warnings,
    }
