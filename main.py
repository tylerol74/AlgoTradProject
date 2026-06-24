"""Command-line entry point for AlgoTradProject."""

import argparse
import json
import logging
from pathlib import Path
from typing import List, Optional

from backtesting.engine import run_backtest
from backtesting.models import BacktestConfig
from config.settings import DEFAULT_TEST_TICKERS, LOG_LEVEL
from data.market_data import update_price_universe
from database.repositories import (
    get_backtest_run,
    get_backtest_trades,
    get_database_status,
    get_portfolio_snapshots,
    get_price_history,
)
from database.schema import initialize_database
from experiments.models import experiment_config_from_dict
from experiments.runner import experiment_summary, run_experiment
from fundamentals.repository import fundamentals_status
from fundamentals.service import (
    get_filing_history,
    get_fundamentals_as_of,
    update_fundamentals_universe,
)
from reporting.backtest_report import create_backtest_report
from reporting.comparison import compare_backtests
from reporting.exports import export_backtest_report, export_comparison, export_experiment
from strategies.moving_average_reversion import MovingAverageReversionStrategy


def configure_logging() -> None:
    """Configure application logging."""
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(description="AlgoTradProject command-line tools")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("init-db", help="Initialize the SQLite database")

    update_parser = subparsers.add_parser("update-prices", help="Download and store missing daily prices")
    update_parser.add_argument("--tickers", nargs="+", default=None)
    update_parser.add_argument("--start-date", default=None)
    update_parser.add_argument("--end-date", default=None)
    update_parser.add_argument("--batch-size", type=int, default=None)

    show_parser = subparsers.add_parser("show-prices", help="Show stored prices without downloading")
    show_parser.add_argument("ticker")
    show_parser.add_argument("--start-date", default=None)
    show_parser.add_argument("--end-date", default=None)
    show_parser.add_argument("--limit", type=int, default=10)

    run_parser = subparsers.add_parser("run-backtest", help="Run the moving-average reversion backtest")
    run_parser.add_argument("--tickers", nargs="+", default=DEFAULT_TEST_TICKERS)
    run_parser.add_argument("--start-date", required=True)
    run_parser.add_argument("--end-date", required=True)
    run_parser.add_argument("--starting-capital", type=float, default=100000.0)
    run_parser.add_argument("--maximum-positions", type=int, default=5)
    run_parser.add_argument("--position-size-pct", type=float, default=0.20)
    run_parser.add_argument("--slippage-pct", type=float, default=0.001)
    run_parser.add_argument("--commission", type=float, default=0.0)
    run_parser.add_argument("--maximum-holding-days", type=int, default=60)
    run_parser.add_argument("--ma-window", type=int, default=20)
    run_parser.add_argument("--entry-distance-pct", type=float, default=0.05)
    run_parser.add_argument("--stop-loss-pct", type=float, default=0.10)
    run_parser.add_argument("--benchmark", default=None)
    run_parser.add_argument("--no-persist", action="store_true")

    show_backtest = subparsers.add_parser("show-backtest", help="Show a saved backtest")
    show_backtest.add_argument("backtest_id", type=int)

    report_parser = subparsers.add_parser("report-backtest", help="Analyze a saved backtest")
    report_parser.add_argument("backtest_id", type=int)
    report_parser.add_argument("--trades-limit", type=int, default=20)
    report_parser.add_argument("--show-attribution", action="store_true")
    report_parser.add_argument("--show-exit-reasons", action="store_true")
    report_parser.add_argument("--show-monthly", action="store_true")
    report_parser.add_argument("--export-dir", default=None)

    compare_parser = subparsers.add_parser("compare-backtests", help="Compare saved backtests")
    compare_parser.add_argument("backtest_ids", nargs="+", type=int)
    compare_parser.add_argument("--sort-by", default=None)
    compare_parser.add_argument("--descending", action="store_true")
    compare_parser.add_argument("--export-dir", default=None)

    experiment_parser = subparsers.add_parser("run-experiment", help="Run a JSON-defined development/validation experiment")
    experiment_parser.add_argument("experiment_file")
    experiment_parser.add_argument("--export-dir", default=None)

    fundamentals_parser = subparsers.add_parser("update-fundamentals", help="Download and store SEC fundamentals")
    fundamentals_parser.add_argument("--tickers", nargs="+", default=DEFAULT_TEST_TICKERS)
    fundamentals_parser.add_argument("--years", type=int, default=None)
    fundamentals_parser.add_argument("--start-date", default=None)
    fundamentals_parser.add_argument("--end-date", default=None)
    fundamentals_parser.add_argument("--force-refresh", action="store_true")

    subparsers.add_parser("fundamentals-status", help="Show SEC fundamentals database status")

    filings_parser = subparsers.add_parser("show-filings", help="Show supported SEC filings for a ticker")
    filings_parser.add_argument("ticker")
    filings_parser.add_argument("--start-date", default=None)
    filings_parser.add_argument("--end-date", default=None)
    filings_parser.add_argument("--forms", nargs="+", default=None)

    show_fundamentals_parser = subparsers.add_parser("show-fundamentals", help="Show point-in-time fundamentals")
    show_fundamentals_parser.add_argument("ticker")
    show_fundamentals_parser.add_argument("--as-of", required=True)

    subparsers.add_parser("db-status", help="Show SQLite database status")
    parser.set_defaults(command="init-db")
    return parser


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    return build_parser().parse_args(argv)


def _print_update_summary(summary: dict) -> None:
    for item in summary["results"]:
        error = f" error={item['error']}" if item.get("error") else ""
        print(f"{item['ticker']}: {item['status']} downloaded={item['rows_downloaded']} stored={item['rows_stored']} start={item['start_date']} end={item['end_date']}{error}")
    print(f"Total: updated={summary['updated']} already_current={summary['already_current']} no_data={summary['no_data']} failed={summary['failed']} downloaded={summary['rows_downloaded']} stored={summary['rows_stored']}")


def _show_prices(ticker: str, start_date: Optional[str], end_date: Optional[str], limit: int) -> None:
    rows = get_price_history(ticker, start_date=start_date, end_date=end_date)
    if not rows:
        print(f"No stored prices found for {ticker.upper()}.")
        return
    for row in rows[-limit:]:
        print(f"{row['trade_date']} {row['ticker']} O={row['open']:.2f} H={row['high']:.2f} L={row['low']:.2f} C={row['close']:.2f} AdjC={row['adjusted_close']:.2f} V={row['volume']}")


def _print_db_status() -> None:
    status = get_database_status()
    print(f"Securities: {status['securities']}")
    print(f"Daily price rows: {status['daily_price_rows']}")
    print(f"Earliest stored date: {status['earliest_date']}")
    print(f"Latest stored date: {status['latest_date']}")
    print("Rows by ticker:")
    for ticker, row_count in status["rows_by_ticker"].items():
        print(f"  {ticker}: {row_count}")
    if not status["rows_by_ticker"]:
        print("  (none)")


def _print_backtest_result(result) -> None:
    metrics = result.metrics
    benchmark = metrics.get("benchmark") or {}
    print(f"Strategy: {result.config.strategy_name}")
    print(f"Tickers: {', '.join(result.config.tickers)}")
    print(f"Date range: {result.config.start_date} to {result.config.end_date}")
    print(f"Starting capital: {result.config.starting_capital:.2f}")
    print(f"Ending portfolio value: {metrics['ending_portfolio_value']:.2f}")
    print(f"Total return: {metrics['total_return_pct']:.4%}")
    print(f"Maximum drawdown: {metrics['maximum_drawdown']:.4%}")
    print(f"Completed trades: {metrics['completed_trade_count']}")
    print(f"Win rate: {metrics['win_rate']:.4%}")
    print(f"Profit factor: {metrics['profit_factor']}")
    print(f"Total commissions: {metrics['total_commissions']:.2f}")
    print(f"Benchmark return: {benchmark.get('return_pct') if benchmark.get('return_pct') is not None else 'N/A'}")
    print(f"Backtest ID: {result.backtest_id if result.backtest_id is not None else 'not persisted'}")


def _run_backtest_command(args: argparse.Namespace) -> None:
    config = BacktestConfig("moving_average_reversion", args.tickers, args.start_date, args.end_date, args.starting_capital, args.maximum_positions, args.position_size_pct, args.slippage_pct, args.commission, args.maximum_holding_days)
    strategy = MovingAverageReversionStrategy(args.ma_window, args.entry_distance_pct, args.stop_loss_pct, args.maximum_holding_days)
    try:
        result = run_backtest(config, strategy, benchmark_ticker=args.benchmark, persist=not args.no_persist)
    except ValueError as exc:
        print(f"Backtest failed: {exc}")
        print("If price history is missing, run update-prices first.")
        return
    _print_backtest_result(result)


def _show_backtest(backtest_id: int) -> None:
    run = get_backtest_run(backtest_id)
    if run is None:
        print(f"Backtest {backtest_id} not found.")
        return
    trades = get_backtest_trades(backtest_id)
    snapshots = get_portfolio_snapshots(backtest_id)
    parameters = json.loads(run["parameters_json"]) if run.get("parameters_json") else {}
    print(f"Backtest ID: {run['backtest_id']}")
    print(f"Strategy: {run['strategy']}")
    print(f"Date range: {run['start_date']} to {run['end_date']}")
    print(f"Starting capital: {run['starting_capital']:.2f}")
    print(f"Ending value: {run['ending_capital']:.2f}")
    print(f"Created at: {run['created_at']}")
    print(f"Parameters: {json.dumps(parameters.get('config', parameters), sort_keys=True)}")
    print(f"Completed trades: {len(trades)}")
    for trade in trades[:10]:
        print(f"  {trade['ticker']} {trade['entry_date']}->{trade['exit_date']} qty={trade['quantity']} pnl={trade['pnl']:.2f} return={trade['return_pct']:.4%}")
    print(f"Snapshots: {len(snapshots)}")
    if snapshots:
        print(f"Snapshot range: {snapshots[0]['snapshot_date']} to {snapshots[-1]['snapshot_date']}")
        print(f"Ending snapshot value: {snapshots[-1]['total_value']:.2f}")


def _report_backtest(args: argparse.Namespace) -> None:
    report = create_backtest_report(args.backtest_id)
    print(f"Backtest ID: {report.backtest_id}")
    print(f"Strategy: {report.run['strategy']}")
    print(f"Date range: {report.run['start_date']} to {report.run['end_date']}")
    print(f"Ending value: {report.run['ending_capital']:.2f}")
    print(f"Total return: {report.performance['total_return']:.4%}")
    print(f"Benchmark return: {report.performance['benchmark_return'] if report.performance['benchmark_return'] is not None else 'N/A'}")
    print(f"Excess return: {report.performance['excess_return'] if report.performance['excess_return'] is not None else 'N/A'}")
    print(f"Maximum drawdown: {report.performance['maximum_drawdown']:.4%}")
    print(f"Completed trades: {report.performance['completed_trades']}")
    print(f"Win rate: {report.performance['win_rate']:.4%}")
    print(f"Profit factor: {report.performance['profit_factor']}")
    if report.warnings:
        print("Warnings:")
        for warning in report.warnings:
            print(f"  - {warning}")
    if args.show_attribution:
        print("Ticker attribution:")
        for row in report.ticker_attribution["rows"]:
            print(f"  {row['ticker']}: trades={row['completed_trade_count']} net_pnl={row['net_pnl']:.2f} win_rate={row['win_rate']:.2%}")
    if args.show_exit_reasons:
        print("Exit reasons:")
        for row in report.exit_reasons:
            print(f"  {row['exit_reason']}: trades={row['trade_count']} net_pnl={row['net_pnl']:.2f}")
    if args.show_monthly:
        print("Monthly returns:")
        for row in report.monthly_returns[-12:]:
            print(f"  {row['month']}: {row['monthly_return'] if row['monthly_return'] is not None else 'N/A'}")
    if args.export_dir:
        files = export_backtest_report(report, args.export_dir)
        print("Exported files:")
        for path in files:
            print(f"  {path}")


def _compare_backtests_command(args: argparse.Namespace) -> None:
    comparison = compare_backtests(args.backtest_ids, sort_by=args.sort_by, descending=args.descending)
    print("Backtest comparison:")
    for row in comparison["rows"]:
        print(f"  {row['backtest_id']}: return={row['total_return']:.4%} max_dd={row['maximum_drawdown']:.4%} trades={row['completed_trades']}")
    if comparison["warnings"]:
        print("Warnings:")
        for warning in comparison["warnings"]:
            print(f"  - {warning}")
    if args.export_dir:
        files = export_comparison(comparison, args.export_dir)
        print("Exported files:")
        for path in files:
            print(f"  {path}")


def _run_experiment_command(args: argparse.Namespace) -> None:
    config = experiment_config_from_dict(json.loads(Path(args.experiment_file).read_text(encoding="utf-8")))
    results = run_experiment(config)
    summary = experiment_summary(config, results)
    print(f"Experiment: {config.name}")
    for row in summary["results"]:
        print(f"  {row['parameter_set_name']}: dev_return={row['development_total_return']:.4%} val_return={row['validation_total_return']:.4%} dev_id={row['development_backtest_id']} val_id={row['validation_backtest_id']} warnings={row['warnings']}")
    if args.export_dir:
        files = export_experiment(summary, args.export_dir)
        print("Exported files:")
        for path in files:
            print(f"  {path}")


def _update_fundamentals_command(args: argparse.Namespace) -> None:
    initialize_database()
    summary = update_fundamentals_universe(args.tickers, years=args.years)
    for item in summary["results"]:
        error = f" error={item['error']}" if item.get("error") else ""
        warnings = f" warnings={'; '.join(item['warnings'])}" if item.get("warnings") else ""
        print(
            f"{item['ticker']}: {item['status']} cik={item.get('cik')} "
            f"filings={item['filings_stored']} facts={item['facts_stored']}{warnings}{error}"
        )
    print(
        f"Total: updated={summary['updated']} unmapped={summary['unmapped']} failed={summary['failed']} "
        f"filings={summary['filings_stored']} facts={summary['facts_stored']}"
    )


def _fundamentals_status_command() -> None:
    status = fundamentals_status()
    print(f"Mapped securities: {status['mapped_securities']}")
    print(f"Filing count: {status['filing_count']}")
    print(f"Fact count: {status['fact_count']}")
    print(f"Earliest filing date: {status['earliest_filing_date']}")
    print(f"Latest filing date: {status['latest_filing_date']}")
    print(f"Latest accepted timestamp: {status['latest_accepted_at']}")
    print("Counts by ticker:")
    for ticker, counts in status["by_ticker"].items():
        print(f"  {ticker}: filings={counts['filings']} facts={counts['facts']}")
    if not status["by_ticker"]:
        print("  (none)")


def _show_filings_command(args: argparse.Namespace) -> None:
    rows = get_filing_history(args.ticker, start_date=args.start_date, end_date=args.end_date, form_types=args.forms)
    if not rows:
        print(f"No supported filings found for {args.ticker.upper()}.")
        return
    for row in rows:
        amendment = " amendment" if row["is_amendment"] else ""
        print(
            f"{row['filing_date']} {row['form_type']}{amendment} period={row['report_period']} "
            f"accepted={row['accepted_at']} accession={row['accession_number']}"
        )


def _show_fundamentals_command(args: argparse.Namespace) -> None:
    result = get_fundamentals_as_of(args.ticker, args.as_of)
    if not result["fields"]:
        print(f"No fundamentals found for {args.ticker.upper()} as of {args.as_of}.")
        return
    print(f"{result['ticker']} fundamentals as of {result['as_of_date']}:")
    for field, row in result["fields"].items():
        fallback = " fallback=filing_date" if row["accepted_at_fallback_used"] else ""
        amendment = " amendment" if row["is_amendment"] else ""
        print(
            f"  {field}: value={row['value']} unit={row['unit']} period={row['report_period']} "
            f"form={row['form_type']}{amendment} filed={row['filing_date']} "
            f"accepted={row['accepted_at']} accession={row['accession_number']}{fallback}"
        )


def main(argv: Optional[List[str]] = None) -> None:
    """Run the command-line interface."""
    configure_logging()
    args = parse_args(argv)
    if args.command == "init-db":
        initialize_database()
        print("Database initialized.")
    elif args.command == "update-prices":
        initialize_database()
        _print_update_summary(update_price_universe(args.tickers or DEFAULT_TEST_TICKERS, args.start_date, args.end_date, args.batch_size))
    elif args.command == "show-prices":
        _show_prices(args.ticker, args.start_date, args.end_date, args.limit)
    elif args.command == "run-backtest":
        initialize_database()
        _run_backtest_command(args)
    elif args.command == "show-backtest":
        _show_backtest(args.backtest_id)
    elif args.command == "report-backtest":
        _report_backtest(args)
    elif args.command == "compare-backtests":
        _compare_backtests_command(args)
    elif args.command == "run-experiment":
        _run_experiment_command(args)
    elif args.command == "update-fundamentals":
        _update_fundamentals_command(args)
    elif args.command == "fundamentals-status":
        _fundamentals_status_command()
    elif args.command == "show-filings":
        _show_filings_command(args)
    elif args.command == "show-fundamentals":
        _show_fundamentals_command(args)
    elif args.command == "db-status":
        _print_db_status()


if __name__ == "__main__":
    main()

