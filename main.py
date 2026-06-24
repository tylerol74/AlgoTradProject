"""Command-line entry point for AlgoTradProject."""

import argparse
import csv
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from backtesting.engine import run_backtest
from backtesting.models import BacktestConfig
from config.settings import DEFAULT_TEST_TICKERS, LOG_LEVEL
from configurations.models import GrahamStrategyConfig, UniverseConfig
from configurations.presets import get_preset, list_presets
from configurations.serialization import config_from_json, config_to_json
from configurations.validation import ConfigurationValidationError
from data.market_data import update_price_universe
from data import strategy_data as repository_strategy_data
from database.repositories import (
    get_active_common_stock_tickers,
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
from reporting.graham_report import (
    export_graham_evaluations,
    graham_audit_payload,
    graham_audit_row,
    graham_audit_summary,
    graham_evaluation_to_dict,
    graham_missing_data_plan,
    graham_summary_row,
)
from strategies.graham_value import GrahamValueStrategy
from strategies.moving_average_reversion import MovingAverageReversionStrategy


def _add_graham_threshold_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--minimum-margin-of-safety", type=float, default=0.30)
    parser.add_argument("--minimum-graham-score", type=float, default=70.0)
    parser.add_argument("--minimum-data-quality-score", type=float, default=60.0)
    parser.add_argument("--minimum-profitable-years", type=int, default=4)
    parser.add_argument("--minimum-price", type=float, default=3.0)
    parser.add_argument("--minimum-market-cap", type=float, default=300_000_000.0)
    parser.add_argument("--minimum-average-dollar-volume", type=float, default=2_000_000.0)
    parser.add_argument("--exclude-financials", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--exclude-reits", action=argparse.BooleanOptionalAction, default=True)


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

    evaluate_graham = subparsers.add_parser("evaluate-graham", help="Evaluate one Graham candidate")
    evaluate_graham.add_argument("ticker")
    evaluate_graham.add_argument("--as-of", required=True)
    _add_graham_threshold_flags(evaluate_graham)
    evaluate_graham.add_argument("--json", action="store_true")
    evaluate_graham.add_argument("--export-dir", default=None)

    screen_graham = subparsers.add_parser("screen-graham", help="Screen multiple Graham candidates")
    screen_graham.add_argument("--tickers", nargs="+", default=DEFAULT_TEST_TICKERS)
    screen_graham.add_argument("--as-of", required=True)
    _add_graham_threshold_flags(screen_graham)
    screen_graham.add_argument("--json", action="store_true")
    screen_graham.add_argument("--export-dir", default=None)

    audit_graham = subparsers.add_parser("audit-graham-data", help="Audit stored Graham data coverage without downloading")
    audit_source = audit_graham.add_mutually_exclusive_group(required=True)
    audit_source.add_argument("--tickers", nargs="+")
    audit_source.add_argument("--ticker-file")
    audit_source.add_argument("--universe", choices=["all-eligible"])
    audit_graham.add_argument("--as-of", required=True)
    _add_graham_threshold_flags(audit_graham)
    audit_graham.add_argument("--json", action="store_true")
    audit_graham.add_argument("--export-dir", default=None)
    audit_graham.add_argument("--verbose", action="store_true")
    audit_graham.add_argument("--qualified-only", action="store_true")
    audit_graham.add_argument("--data-ready-only", action="store_true")
    audit_graham.add_argument("--failures-only", action="store_true")
    audit_graham.add_argument("--sort-by", choices=["ticker", "data_quality_score", "graham_score", "margin_of_safety", "market_cap", "average_dollar_volume"], default=None)
    audit_graham.add_argument("--descending", action="store_true")
    audit_graham.add_argument("--limit", type=int, default=None)
    audit_graham.add_argument("--offset", type=int, default=0)
    audit_graham.add_argument("--include-missing-data", action="store_true", default=True)
    audit_graham.add_argument("--export-missing-data-plan", action="store_true")

    graham_backtest = subparsers.add_parser("run-graham-backtest", help="Run the standalone Graham backtest")
    graham_backtest.add_argument("--tickers", nargs="+", default=DEFAULT_TEST_TICKERS)
    graham_backtest.add_argument("--start-date", required=True)
    graham_backtest.add_argument("--end-date", required=True)
    graham_backtest.add_argument("--starting-capital", type=float, default=100000.0)
    graham_backtest.add_argument("--maximum-positions", type=int, default=10)
    graham_backtest.add_argument("--position-size-pct", type=float, default=0.10)
    graham_backtest.add_argument("--slippage-pct", type=float, default=0.001)
    graham_backtest.add_argument("--commission", type=float, default=0.0)
    _add_graham_threshold_flags(graham_backtest)
    graham_backtest.add_argument("--maximum-holding-days", type=int, default=504)
    graham_backtest.add_argument("--stop-loss-pct", type=float, default=None)
    graham_backtest.add_argument("--reevaluation-frequency", default="weekly")
    graham_backtest.add_argument("--benchmark", default=None)
    graham_backtest.add_argument("--no-persist", action="store_true")

    subparsers.add_parser("list-strategy-presets", help="List built-in strategy presets")

    show_preset = subparsers.add_parser("show-strategy-preset", help="Show one strategy preset as JSON")
    show_preset.add_argument("name")

    export_preset = subparsers.add_parser("export-strategy-preset", help="Export one strategy preset to JSON")
    export_preset.add_argument("name")
    export_preset.add_argument("--output", required=True)

    validate_config = subparsers.add_parser("validate-strategy-config", help="Validate a strategy configuration JSON file")
    validate_config.add_argument("path")

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


def _graham_strategy(args: argparse.Namespace) -> GrahamValueStrategy:
    strategy_config = GrahamStrategyConfig(
        minimum_margin_of_safety=args.minimum_margin_of_safety,
        minimum_graham_score=args.minimum_graham_score,
        minimum_data_quality_score=args.minimum_data_quality_score,
        minimum_profitable_years=args.minimum_profitable_years,
        exclude_financials=args.exclude_financials,
        exclude_reits=args.exclude_reits,
    )
    universe_config = UniverseConfig(
        minimum_price=args.minimum_price,
        minimum_market_cap=args.minimum_market_cap,
        minimum_average_dollar_volume=args.minimum_average_dollar_volume,
    )
    return GrahamValueStrategy(
        strategy_config=strategy_config,
        universe_config=universe_config,
        maximum_holding_days=getattr(args, "maximum_holding_days", 504),
        stop_loss_pct=getattr(args, "stop_loss_pct", None),
        reevaluation_frequency=getattr(args, "reevaluation_frequency", "weekly"),
        strategy_data=repository_strategy_data,
    )


def _evaluate_graham_command(args: argparse.Namespace) -> None:
    strategy = _graham_strategy(args)
    try:
        evaluation = strategy.evaluate(args.ticker, args.as_of)
    except Exception as exc:
        print(f"Graham evaluation failed: {exc}")
        print("If data is missing, run update-prices and update-fundamentals first.")
        return
    if args.json:
        print(json.dumps(graham_evaluation_to_dict(evaluation), indent=2, sort_keys=True, default=str))
    else:
        row = graham_summary_row(evaluation)
        print(f"{row['ticker']} as of {row['evaluation_date']}")
        print(f"Price: {row['price']}")
        print(f"EPS: {row['eps']}")
        print(f"Book value/share: {row['book_value_per_share']}")
        print(f"Graham Number: {row['graham_number']}")
        print(f"Margin of safety: {row['margin_of_safety']}")
        print(f"Graham score: {row['graham_quality_score']}")
        print(f"Data-quality score: {row['data_quality_score']}")
        print(f"Classification: {row['classification']}")
        print(f"Qualification: {row['qualification_status']}")
        print(f"Signal: {row['signal_type']}")
        print(f"Disqualifications: {row['disqualification_reasons'] or '(none)'}")
        print(f"Warnings: {row['warnings'] or '(none)'}")
    if args.export_dir:
        for path in export_graham_evaluations([evaluation], args.export_dir, f"graham-{evaluation.ticker}-{evaluation.evaluation_date}.json"):
            print(f"Exported {path}")


def _screen_graham_command(args: argparse.Namespace) -> None:
    strategy = _graham_strategy(args)
    evaluations = [strategy.evaluate(ticker, args.as_of) for ticker in args.tickers]
    rows = [graham_summary_row(evaluation) for evaluation in evaluations]
    if args.json:
        print(json.dumps(rows, indent=2, sort_keys=True, default=str))
    else:
        for row in rows:
            print(
                f"{row['ticker']} price={row['price']} eps={row['eps']} bvps={row['book_value_per_share']} "
                f"graham={row['graham_number']} mos={row['margin_of_safety']} score={row['graham_quality_score']} "
                f"dq={row['data_quality_score']} class={row['classification']} status={row['qualification_status']} "
                f"reasons={row['disqualification_reasons'] or '(none)'}"
            )
    if args.export_dir:
        for path in export_graham_evaluations(evaluations, args.export_dir):
            print(f"Exported {path}")


def _valid_ticker_text(ticker: str) -> bool:
    allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.")
    return bool(ticker) and all(char in allowed for char in ticker) and any(char.isalpha() for char in ticker)


def _dedupe_tickers(values: List[str]) -> Tuple[List[str], List[str]]:
    from data.sec_ticker_map import normalize_ticker

    tickers: List[str] = []
    invalid: List[str] = []
    seen = set()
    for value in values:
        try:
            normalized = normalize_ticker(value)
        except ValueError:
            invalid.append(str(value))
            continue
        if not _valid_ticker_text(normalized):
            invalid.append(str(value))
            continue
        if normalized not in seen:
            tickers.append(normalized)
            seen.add(normalized)
    return tickers, invalid


def _read_ticker_file(path: str) -> List[str]:
    file_path = Path(path)
    suffix = file_path.suffix.lower()
    if suffix == ".csv":
        values: List[str] = []
        with file_path.open(newline="", encoding="utf-8") as handle:
            for row in csv.reader(handle):
                values.extend(cell.strip() for cell in row if cell.strip())
        return values
    return [line.strip() for line in file_path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _audit_universe(args: argparse.Namespace) -> Tuple[List[str], List[str], str, int]:
    if args.tickers:
        requested, source = list(args.tickers), "tickers"
    elif args.ticker_file:
        requested, source = _read_ticker_file(args.ticker_file), "ticker-file"
    else:
        requested, source = get_active_common_stock_tickers(limit=args.limit, offset=args.offset), "all-eligible"
    tickers, invalid = _dedupe_tickers(requested)
    if args.limit is not None and args.tickers:
        tickers = tickers[args.offset : args.offset + args.limit]
    elif args.ticker_file:
        tickers = tickers[args.offset :]
        if args.limit is not None:
            tickers = tickers[: args.limit]
    return tickers, invalid, source, len(requested)


def _sort_audit_rows(rows: List[Dict[str, Any]], sort_by: Optional[str], descending: bool) -> List[Dict[str, Any]]:
    if not sort_by:
        return rows

    def key(row: Dict[str, Any]) -> Any:
        value = row.get(sort_by)
        return (value is None, value if value is not None else "", row.get("ticker", ""))

    return sorted(rows, key=key, reverse=descending)


def _filter_audit_rows(rows: List[Dict[str, Any]], args: argparse.Namespace) -> List[Dict[str, Any]]:
    filtered = rows
    if args.qualified_only:
        filtered = [row for row in filtered if row.get("strategy_qualified")]
    if args.data_ready_only:
        filtered = [row for row in filtered if row.get("data_ready")]
    if args.failures_only:
        filtered = [row for row in filtered if not row.get("strategy_qualified")]
    return filtered


def _format_bool(value: Any) -> str:
    if value is True:
        return "yes"
    if value is False:
        return "no"
    return "-"


def _format_cell(value: Any) -> str:
    if value is None or value == "":
        return "-"
    if isinstance(value, bool):
        return _format_bool(value)
    if isinstance(value, float):
        if abs(value) >= 1_000_000:
            return f"{value:.0f}"
        return f"{value:.4g}"
    if isinstance(value, list):
        return "; ".join(str(item) for item in value) if value else "-"
    return str(value)


def _print_aligned_table(rows: List[Dict[str, Any]], columns: List[str]) -> None:
    formatted = [[_format_cell(row.get(column)) for column in columns] for row in rows]
    widths = [len(column) for column in columns]
    for line in formatted:
        for index, cell in enumerate(line):
            widths[index] = max(widths[index], len(cell))
    print("  ".join(column.ljust(widths[index]) for index, column in enumerate(columns)))
    print("  ".join("-" * widths[index] for index, _ in enumerate(columns)))
    for line in formatted:
        print("  ".join(cell.ljust(widths[index]) for index, cell in enumerate(line)))


def _print_audit_summary(summary: Dict[str, Any]) -> None:
    def pct(item: Dict[str, Any]) -> str:
        return f"{item['count']} ({item['percentage']:.1f}%)"

    print()
    print("Summary:")
    print(f"  total requested: {summary['total_requested']}")
    print(f"  valid tickers: {summary['valid_tickers']}")
    print(f"  invalid tickers: {summary['invalid_tickers']}")
    print(f"  price coverage: {pct(summary['price_coverage'])}")
    print(f"  EPS coverage: {pct(summary['eps_coverage'])}")
    print(f"  shares coverage: {pct(summary['shares_coverage'])}")
    print(f"  equity coverage: {pct(summary['equity_coverage'])}")
    print(f"  current-assets coverage: {pct(summary['current_assets_coverage'])}")
    print(f"  current-liabilities coverage: {pct(summary['current_liabilities_coverage'])}")
    print(f"  debt coverage: {pct(summary['debt_coverage'])}")
    print(f"  five-year-history coverage: {pct(summary['five_year_history_coverage'])}")
    print(f"  data ready: {pct(summary['data_ready'])}")
    print(f"  strategy qualified: {pct(summary['strategy_qualified'])}")
    print(f"  average data-quality score: {_format_cell(summary['average_data_quality_score'])}")
    print(f"  median data-quality score: {_format_cell(summary['median_data_quality_score'])}")
    print(f"  top data issues: {_format_cell(summary['top_data_issues'])}")
    print(f"  top disqualification reasons: {_format_cell(summary['top_disqualification_reasons'])}")
    print(f"  warning totals: {summary['warning_totals']}")


def _audit_graham_data_command(args: argparse.Namespace) -> None:
    tickers, invalid_tickers, universe_source, total_requested = _audit_universe(args)
    strategy = _graham_strategy(args)
    rows = [graham_audit_row(strategy.evaluate(ticker, args.as_of)) for ticker in tickers]
    rows = _filter_audit_rows(rows, args)
    rows = _sort_audit_rows(rows, args.sort_by, args.descending)
    summary = graham_audit_summary(rows, invalid_tickers, total_requested)
    configuration = {
        "sort_by": args.sort_by,
        "descending": args.descending,
        "qualified_only": args.qualified_only,
        "data_ready_only": args.data_ready_only,
        "failures_only": args.failures_only,
        "limit": args.limit,
        "offset": args.offset,
    }
    payload = graham_audit_payload(rows, summary, configuration, args.as_of, universe_source)
    missing_plan = graham_missing_data_plan(rows)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    else:
        columns = [
            "ticker",
            "data_ready",
            "strategy_qualified",
            "price",
            "eps_method",
            "shares_method",
            "debt_method",
            "earnings_years",
            "data_quality_score",
            "graham_score",
            "margin_of_safety",
            "primary_data_issue",
            "primary_disqualification_reason",
            "informational_warning_count",
            "caution_warning_count",
            "critical_warning_count",
        ]
        if args.verbose:
            columns.extend(
                [
                    "price_available",
                    "eps_available",
                    "shares_available",
                    "equity_available",
                    "current_assets_available",
                    "current_liabilities_available",
                    "debt_available",
                    "market_cap",
                    "average_dollar_volume",
                ]
            )
        _print_aligned_table(rows, columns)
        _print_audit_summary(summary)
        if invalid_tickers:
            print(f"Invalid tickers: {', '.join(invalid_tickers)}")
    if args.export_dir:
        directory = Path(args.export_dir)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"graham-data-audit-{args.as_of}.json"
        path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
        print(f"Exported {path}")
        if args.export_missing_data_plan:
            plan_path = directory / f"graham-missing-data-plan-{args.as_of}.json"
            plan_path.write_text(json.dumps(missing_plan, indent=2, sort_keys=True, default=str), encoding="utf-8")
            print(f"Exported {plan_path}")
    elif args.export_missing_data_plan:
        print(json.dumps(missing_plan, indent=2, sort_keys=True, default=str))


def _run_graham_backtest_command(args: argparse.Namespace) -> None:
    config = BacktestConfig(
        "graham_value_v1",
        args.tickers,
        args.start_date,
        args.end_date,
        args.starting_capital,
        args.maximum_positions,
        args.position_size_pct,
        args.slippage_pct,
        args.commission,
        args.maximum_holding_days,
    )
    strategy = _graham_strategy(args)
    try:
        result = run_backtest(config, strategy, benchmark_ticker=args.benchmark, persist=not args.no_persist)
    except ValueError as exc:
        print(f"Graham backtest failed: {exc}")
        print("Run update-prices and update-fundamentals before running Graham backtests.")
        return
    _print_backtest_result(result)


def _list_strategy_presets_command() -> None:
    for name in list_presets():
        print(name)


def _show_strategy_preset_command(args: argparse.Namespace) -> None:
    try:
        print(config_to_json(get_preset(args.name)))
    except ValueError as exc:
        print(str(exc))


def _export_strategy_preset_command(args: argparse.Namespace) -> None:
    try:
        preset = get_preset(args.name)
        Path(args.output).write_text(config_to_json(preset), encoding="utf-8")
    except (ValueError, ConfigurationValidationError) as exc:
        print(f"Export failed: {exc}")
        return
    print(f"Exported {args.name} to {args.output}")


def _validate_strategy_config_command(args: argparse.Namespace) -> None:
    try:
        config_from_json(Path(args.path).read_text(encoding="utf-8"))
    except ConfigurationValidationError as exc:
        print("Invalid strategy configuration:")
        for error in exc.errors:
            print(f"  {error.field}: {error.message}")
        return
    print("Strategy configuration is valid.")


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
    elif args.command == "evaluate-graham":
        _evaluate_graham_command(args)
    elif args.command == "screen-graham":
        _screen_graham_command(args)
    elif args.command == "audit-graham-data":
        _audit_graham_data_command(args)
    elif args.command == "run-graham-backtest":
        _run_graham_backtest_command(args)
    elif args.command == "list-strategy-presets":
        _list_strategy_presets_command()
    elif args.command == "show-strategy-preset":
        _show_strategy_preset_command(args)
    elif args.command == "export-strategy-preset":
        _export_strategy_preset_command(args)
    elif args.command == "validate-strategy-config":
        _validate_strategy_config_command(args)
    elif args.command == "db-status":
        _print_db_status()


if __name__ == "__main__":
    main()

