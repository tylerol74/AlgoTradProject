"""Comparison tools for saved backtest runs."""

from typing import Any, Dict, List, Optional

from reporting.backtest_report import create_backtest_report

SUPPORTED_SORT_FIELDS = {
    "backtest_id",
    "total_return",
    "maximum_drawdown",
    "ending_portfolio_value",
    "win_rate",
    "profit_factor",
    "completed_trades",
    "average_trade_return",
    "average_holding_period",
    "total_commissions",
    "capital_utilization",
    "return_to_drawdown_ratio",
}


def _tickers(report) -> List[str]:
    config = report.parameters.get("config", {})
    return [str(ticker).upper() for ticker in config.get("tickers", [])]


def _benchmark(report) -> Optional[str]:
    benchmark = report.parameters.get("benchmark") or report.performance.get("benchmark")
    config = report.parameters.get("config", {})
    return config.get("benchmark_ticker") or (benchmark.get("ticker") if isinstance(benchmark, dict) else None)


def _comparison_row(report) -> Dict[str, Any]:
    perf = report.performance
    total_return = perf.get("total_return")
    max_dd = perf.get("maximum_drawdown")
    ratio = None if not max_dd else total_return / abs(max_dd)
    return {
        "backtest_id": report.backtest_id,
        "strategy": report.run["strategy"],
        "date_range": f"{report.run['start_date']} to {report.run['end_date']}",
        "start_date": report.run["start_date"],
        "end_date": report.run["end_date"],
        "tickers": _tickers(report),
        "starting_capital": report.run["starting_capital"],
        "ending_portfolio_value": report.run["ending_capital"],
        "total_return": total_return,
        "benchmark_return": perf.get("benchmark_return"),
        "excess_return": perf.get("excess_return"),
        "maximum_drawdown": max_dd,
        "completed_trades": perf.get("completed_trades"),
        "win_rate": perf.get("win_rate"),
        "profit_factor": perf.get("profit_factor"),
        "average_trade_return": perf.get("average_trade_return"),
        "average_holding_period": perf.get("average_holding_period"),
        "total_commissions": perf.get("total_commissions"),
        "capital_utilization": perf.get("capital_utilization"),
        "return_to_drawdown_ratio": ratio,
        "benchmark": _benchmark(report),
    }


def compare_backtests(backtest_ids: List[int], sort_by: Optional[str] = None, descending: bool = False) -> Dict[str, Any]:
    """Compare two or more saved backtests without opaque composite scores."""
    if len(backtest_ids) < 2:
        raise ValueError("at least two backtest IDs are required")
    reports = [create_backtest_report(backtest_id) for backtest_id in backtest_ids]
    rows = [_comparison_row(report) for report in reports]
    warnings = []
    if len({(row["start_date"], row["end_date"]) for row in rows}) > 1:
        warnings.append("runs have different date ranges")
    if len({tuple(row["tickers"]) for row in rows}) > 1:
        warnings.append("runs have different ticker universes")
    if len({row["starting_capital"] for row in rows}) > 1:
        warnings.append("runs have different starting capital")
    if len({row.get("benchmark") for row in rows}) > 1:
        warnings.append("runs have different benchmarks")
    if sort_by:
        if sort_by not in SUPPORTED_SORT_FIELDS:
            raise ValueError(f"unsupported sort field: {sort_by}")
        rows.sort(key=lambda row: (row.get(sort_by) is None, row.get(sort_by), row["backtest_id"]), reverse=descending)
    else:
        rows.sort(key=lambda row: (-(row.get("total_return") or 0.0), -(row.get("maximum_drawdown") or 0.0), row["backtest_id"]))
    return {"rows": rows, "warnings": warnings}
