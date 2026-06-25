"""Validation diagnostics and trade-count warnings."""

from dataclasses import dataclass, field
from statistics import median
from typing import Any, Dict, List


@dataclass(frozen=True)
class StrategyValidationResult:
    strategy_name: str
    configuration_name: str
    period: Any
    requested_tickers: int
    evaluated_tickers: int
    completed_trades: int
    total_return: Any
    annualized_return: Any
    benchmark_return: Any
    excess_return: Any
    maximum_drawdown: Any
    volatility: Any
    sharpe_ratio: Any
    win_rate: Any
    average_trade_return: Any
    median_trade_return: Any
    average_holding_days: Any
    exposure: Any
    turnover: Any
    rejected_signals: int = 0
    missing_data_count: int = 0
    warnings: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class RobustnessSummary:
    strategy: str
    periods_tested: int
    configurations_tested: int
    median_return: Any
    median_drawdown: Any
    median_trade_count: Any
    worst_period_return: Any
    best_period_return: Any
    parameter_stability: str
    coverage_stability: str
    warnings: List[str]
    conclusion: str


def trade_count_label(count: int) -> str:
    if count <= 4:
        return "insufficient"
    if count <= 19:
        return "very limited"
    if count <= 49:
        return "limited"
    if count <= 99:
        return "moderate"
    return "stronger sample"


def trade_diagnostics(trades: List[Any]) -> Dict[str, Any]:
    rows = [trade.__dict__ if hasattr(trade, "__dict__") else dict(trade) for trade in trades]
    count = len(rows)
    warnings: List[str] = []
    if count < 5:
        warnings.append("strategy generates too few completed trades")
    by_ticker: Dict[str, int] = {}
    returns = []
    for row in rows:
        by_ticker[row["ticker"]] = by_ticker.get(row["ticker"], 0) + 1
        returns.append(float(row.get("return_pct") or 0.0))
    if by_ticker and max(by_ticker.values()) / float(count) >= 0.5:
        warnings.append("one ticker contributes an outsized share of trades")
    top_returns = sorted([abs(value) for value in returns], reverse=True)[:5]
    total_abs = sum(abs(value) for value in returns)
    top_five_share = sum(top_returns) / total_abs if total_abs else 0.0
    if count and top_five_share >= 0.75:
        warnings.append("results depend heavily on top five trades")
    return {
        "completed_trades": count,
        "sufficiency": trade_count_label(count),
        "top_five_tickers_by_trade_count": sorted(by_ticker.items(), key=lambda item: (-item[1], item[0]))[:5],
        "top_five_trade_return_share": top_five_share,
        "warnings": warnings,
    }


def result_from_backtest(strategy_name: str, configuration_name: str, period: Any, requested_tickers: int, evaluated_tickers: int, result: Any, missing_data_count: int = 0) -> StrategyValidationResult:
    metrics = result.metrics
    trades = [trade.return_pct for trade in result.trades]
    benchmark = metrics.get("benchmark") or {}
    total_return = metrics.get("total_return_pct")
    benchmark_return = benchmark.get("return_pct")
    diagnostics = trade_diagnostics(result.trades)
    return StrategyValidationResult(
        strategy_name,
        configuration_name,
        period,
        requested_tickers,
        evaluated_tickers,
        int(metrics.get("completed_trade_count") or 0),
        total_return,
        metrics.get("annualized_return"),
        benchmark_return,
        None if total_return is None or benchmark_return is None else total_return - benchmark_return,
        metrics.get("maximum_drawdown"),
        metrics.get("volatility"),
        metrics.get("sharpe_ratio"),
        metrics.get("win_rate"),
        metrics.get("average_trade_return"),
        median(trades) if trades else 0.0,
        metrics.get("average_holding_period_days"),
        metrics.get("capital_invested_pct_time"),
        metrics.get("turnover"),
        0,
        missing_data_count,
        diagnostics["warnings"],
    )


def aggregate_results(strategy: str, results: List[StrategyValidationResult], parameter_stability: str = "not tested") -> RobustnessSummary:
    returns = [row.total_return for row in results if row.total_return is not None]
    drawdowns = [row.maximum_drawdown for row in results if row.maximum_drawdown is not None]
    trades = [row.completed_trades for row in results]
    warnings = [warning for row in results for warning in row.warnings]
    conclusion = "insufficient evidence" if not trades or median(trades) < 5 else "review period stability; not proof of future profitability"
    return RobustnessSummary(
        strategy,
        len(results),
        1,
        median(returns) if returns else None,
        median(drawdowns) if drawdowns else None,
        median(trades) if trades else None,
        min(returns) if returns else None,
        max(returns) if returns else None,
        parameter_stability,
        "reported",
        sorted(set(warnings)),
        conclusion,
    )
