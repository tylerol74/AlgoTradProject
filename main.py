"""Command-line entry point for AlgoTradProject."""

import argparse
import json
import logging
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
    update_parser.add_argument("--tickers", nargs="+", default=None, help="Ticker symbols to update")
    update_parser.add_argument("--start-date", default=None, help="Inclusive start date, YYYY-MM-DD")
    update_parser.add_argument("--end-date", default=None, help="Inclusive end date, YYYY-MM-DD")
    update_parser.add_argument("--batch-size", type=int, default=None, help="Progress batch size")

    show_parser = subparsers.add_parser("show-prices", help="Show stored prices without downloading")
    show_parser.add_argument("ticker", help="Ticker symbol to show")
    show_parser.add_argument("--start-date", default=None, help="Inclusive start date, YYYY-MM-DD")
    show_parser.add_argument("--end-date", default=None, help="Inclusive end date, YYYY-MM-DD")
    show_parser.add_argument("--limit", type=int, default=10, help="Number of most recent rows to print")

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

    subparsers.add_parser("db-status", help="Show SQLite database status")
    parser.set_defaults(command="init-db")
    return parser


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    return build_parser().parse_args(argv)


def _print_update_summary(summary: dict) -> None:
    for item in summary["results"]:
        error = f" error={item['error']}" if item.get("error") else ""
        print(
            f"{item['ticker']}: {item['status']} "
            f"downloaded={item['rows_downloaded']} stored={item['rows_stored']} "
            f"start={item['start_date']} end={item['end_date']}{error}"
        )
    print(
        "Total: "
        f"updated={summary['updated']} already_current={summary['already_current']} "
        f"no_data={summary['no_data']} failed={summary['failed']} "
        f"downloaded={summary['rows_downloaded']} stored={summary['rows_stored']}"
    )


def _show_prices(ticker: str, start_date: Optional[str], end_date: Optional[str], limit: int) -> None:
    rows = get_price_history(ticker, start_date=start_date, end_date=end_date)
    rows_to_print = rows[-limit:]
    if not rows_to_print:
        print(f"No stored prices found for {ticker.upper()}.")
        return
    for row in rows_to_print:
        print(
            f"{row['trade_date']} {row['ticker']} "
            f"O={row['open']:.2f} H={row['high']:.2f} L={row['low']:.2f} "
            f"C={row['close']:.2f} AdjC={row['adjusted_close']:.2f} V={row['volume']}"
        )


def _print_db_status() -> None:
    status = get_database_status()
    print(f"Securities: {status['securities']}")
    print(f"Daily price rows: {status['daily_price_rows']}")
    print(f"Earliest stored date: {status['earliest_date']}")
    print(f"Latest stored date: {status['latest_date']}")
    print("Rows by ticker:")
    if not status["rows_by_ticker"]:
        print("  (none)")
        return
    for ticker, row_count in status["rows_by_ticker"].items():
        print(f"  {ticker}: {row_count}")


def _print_backtest_result(result) -> None:
    metrics = result.metrics
    benchmark = metrics.get("benchmark") or {}
    benchmark_return = benchmark.get("return_pct")
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
    print(f"Benchmark return: {benchmark_return if benchmark_return is not None else 'N/A'}")
    print(f"Backtest ID: {result.backtest_id if result.backtest_id is not None else 'not persisted'}")


def _run_backtest_command(args: argparse.Namespace) -> None:
    config = BacktestConfig(
        strategy_name="moving_average_reversion",
        tickers=args.tickers,
        start_date=args.start_date,
        end_date=args.end_date,
        starting_capital=args.starting_capital,
        maximum_positions=args.maximum_positions,
        position_size_pct=args.position_size_pct,
        slippage_pct=args.slippage_pct,
        commission_per_trade=args.commission,
        maximum_holding_days=args.maximum_holding_days,
    )
    strategy = MovingAverageReversionStrategy(
        moving_average_window=args.ma_window,
        entry_discount_pct=args.entry_distance_pct,
        stop_loss_pct=args.stop_loss_pct,
        maximum_holding_days=args.maximum_holding_days,
    )
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
        print(
            f"  {trade['ticker']} {trade['entry_date']}->{trade['exit_date']} "
            f"qty={trade['quantity']} pnl={trade['pnl']:.2f} return={trade['return_pct']:.4%}"
        )
    print(f"Snapshots: {len(snapshots)}")
    if snapshots:
        first = snapshots[0]
        last = snapshots[-1]
        print(f"Snapshot range: {first['snapshot_date']} to {last['snapshot_date']}")
        print(f"Ending snapshot value: {last['total_value']:.2f}")


def main(argv: Optional[List[str]] = None) -> None:
    """Run the command-line interface."""
    configure_logging()
    args = parse_args(argv)

    if args.command == "init-db":
        initialize_database()
        print("Database initialized.")
    elif args.command == "update-prices":
        initialize_database()
        tickers = args.tickers or DEFAULT_TEST_TICKERS
        summary = update_price_universe(
            tickers,
            start_date=args.start_date,
            end_date=args.end_date,
            batch_size=args.batch_size,
        )
        _print_update_summary(summary)
    elif args.command == "show-prices":
        _show_prices(args.ticker, args.start_date, args.end_date, args.limit)
    elif args.command == "run-backtest":
        initialize_database()
        _run_backtest_command(args)
    elif args.command == "show-backtest":
        _show_backtest(args.backtest_id)
    elif args.command == "db-status":
        _print_db_status()


if __name__ == "__main__":
    main()
