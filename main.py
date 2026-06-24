"""Command-line entry point for AlgoTradProject."""

import argparse
import logging
from typing import List, Optional

from config.settings import DEFAULT_TEST_TICKERS, LOG_LEVEL
from data.market_data import update_price_universe
from database.repositories import get_database_status, get_price_history
from database.schema import initialize_database


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
    elif args.command == "db-status":
        _print_db_status()


if __name__ == "__main__":
    main()
