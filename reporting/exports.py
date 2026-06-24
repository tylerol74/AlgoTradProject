"""CSV and JSON export helpers for reports, comparisons, and experiments."""

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from database import repositories
from reporting.backtest_report import BacktestReport, report_to_dict


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _sanitize(value: str) -> str:
    return "".join(char if char.isalnum() or char in ("-", "_") else "-" for char in value).strip("-") or "export"


def _prepare_dir(export_dir: Path) -> None:
    export_dir.mkdir(parents=True, exist_ok=True)


def _unique_path(path: Path, overwrite: bool) -> Path:
    if path.exists() and not overwrite:
        raise FileExistsError(f"export already exists: {path}")
    return path


def _write_json(path: Path, payload: Any, overwrite: bool) -> Path:
    target = _unique_path(path, overwrite)
    target.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    return target


def _write_csv(path: Path, rows: List[Dict[str, Any]], fieldnames: List[str], overwrite: bool) -> Path:
    target = _unique_path(path, overwrite)
    with target.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return target


def export_backtest_report(report: BacktestReport, export_dir: str, overwrite: bool = False) -> List[Path]:
    """Export one backtest report to stable JSON and CSV files."""
    directory = Path(export_dir)
    _prepare_dir(directory)
    suffix = _timestamp()
    prefix = f"backtest-{report.backtest_id}-{suffix}"
    payload = report_to_dict(report)
    files = [
        _write_json(directory / f"{prefix}-summary.json", payload, overwrite),
        _write_csv(directory / f"{prefix}-trades.csv", repositories.get_backtest_trades(report.backtest_id), ["ticker", "signal_date", "entry_date", "entry_price", "exit_date", "exit_price", "quantity", "pnl", "return_pct", "exit_reason"], overwrite),
        _write_csv(directory / f"{prefix}-attribution.csv", report.ticker_attribution["rows"], ["ticker", "completed_trade_count", "winning_trades", "losing_trades", "win_rate", "gross_profit", "gross_loss", "net_pnl", "average_return_per_trade", "average_holding_period", "best_trade_return", "worst_trade_return", "contribution_to_total_pnl", "percentage_of_total_trades"], overwrite),
        _write_csv(directory / f"{prefix}-exit-reasons.csv", report.exit_reasons, ["exit_reason", "trade_count", "percentage_of_all_trades", "wins", "losses", "win_rate", "net_pnl", "average_return", "average_holding_period"], overwrite),
        _write_csv(directory / f"{prefix}-monthly-returns.csv", report.monthly_returns, ["month", "ending_value", "monthly_return", "monthly_max_drawdown"], overwrite),
        _write_csv(directory / f"{prefix}-equity-curve.csv", report.equity_curve, ["snapshot_date", "total_value", "normalized_equity", "running_peak", "drawdown", "cash_pct", "holdings_pct"], overwrite),
    ]
    return [path for path in files if path is not None]


def export_trades_csv(trades: List[Dict[str, Any]], backtest_id: int, export_dir: str, overwrite: bool = False) -> Path:
    """Export saved trades for a backtest."""
    directory = Path(export_dir)
    _prepare_dir(directory)
    return _write_csv(directory / f"backtest-{backtest_id}-{_timestamp()}-trades.csv", trades, ["ticker", "signal_date", "entry_date", "entry_price", "exit_date", "exit_price", "quantity", "pnl", "return_pct", "exit_reason"], overwrite)


def export_comparison(comparison: Dict[str, Any], export_dir: str, overwrite: bool = False) -> List[Path]:
    """Export comparison rows to CSV and JSON."""
    directory = Path(export_dir)
    _prepare_dir(directory)
    suffix = _timestamp()
    return [
        _write_json(directory / f"comparison-{suffix}.json", comparison, overwrite),
        _write_csv(directory / f"comparison-{suffix}.csv", comparison["rows"], ["backtest_id", "strategy", "date_range", "tickers", "starting_capital", "ending_portfolio_value", "total_return", "benchmark_return", "excess_return", "maximum_drawdown", "completed_trades", "win_rate", "profit_factor", "average_trade_return", "average_holding_period", "total_commissions", "capital_utilization", "return_to_drawdown_ratio"], overwrite),
    ]


def export_experiment(summary: Dict[str, Any], export_dir: str, overwrite: bool = False) -> List[Path]:
    """Export experiment summary to CSV and JSON."""
    directory = Path(export_dir)
    _prepare_dir(directory)
    name = _sanitize(summary.get("name", "experiment"))
    suffix = _timestamp()
    rows = summary.get("results", [])
    fieldnames = ["parameter_set_name", "development_backtest_id", "validation_backtest_id", "development_total_return", "validation_total_return", "development_maximum_drawdown", "validation_maximum_drawdown", "development_trade_count", "validation_trade_count", "development_win_rate", "validation_win_rate", "development_benchmark_return", "validation_benchmark_return", "development_excess_return", "validation_excess_return", "warnings"]
    return [
        _write_json(directory / f"{name}-{suffix}.json", summary, overwrite),
        _write_csv(directory / f"{name}-{suffix}.csv", rows, fieldnames, overwrite),
    ]

