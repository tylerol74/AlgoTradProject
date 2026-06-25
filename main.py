"""Command-line entry point for AlgoTradProject."""

import argparse
import json
import logging
from pathlib import Path
from typing import List, Optional

from backtesting.engine import run_backtest
from backtesting.models import BacktestConfig
from config.settings import DEFAULT_TEST_TICKERS, LOG_LEVEL
from configurations.models import CombinedStrategyConfig, GrahamStrategyConfig, TechnicalCapitulationConfig, UniverseConfig
from configurations.presets import get_combined_preset, get_preset, list_presets
from configurations.serialization import config_from_json, config_to_json
from configurations.validation import ConfigurationValidationError
from data.market_data import update_price_universe, update_ticker_prices
from data.readiness import (
    build_readiness_report,
    database_audit_for_tickers,
    export_preparation_report,
    export_readiness_report,
    prepare_universe_data,
    read_input_symbols,
)
from data.universe import (
    build_universe_from_sec_map,
    coverage_freshness,
    deterministic_sample,
    read_ticker_file,
    run_tracked_batch,
    universe_status as get_universe_status,
    universe_tickers,
    write_json,
)
from data import strategy_data as repository_strategy_data
from database.repositories import (
    get_backtest_run,
    get_backtest_trades,
    get_database_status,
    get_portfolio_snapshots,
    get_price_history,
    list_security_universe,
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
from reporting.graham_report import export_graham_evaluations, graham_audit_row, graham_evaluation_to_dict, graham_summary_row
from reporting.combined_strategy_report import combined_coverage_summary, combined_summary_row, export_combined_evaluations
from reporting.shortlist_report import export_shortlist_report, shortlist_report
from reporting.validation_report import export_validation_report, validation_report
from strategies.combined_graham_technical import CombinedGrahamTechnicalStrategy, rank_combined_candidates
from strategies.graham_value import GrahamValueStrategy
from strategies.moving_average_reversion import MovingAverageReversionStrategy
from validation.comparison import compare_strategies_fair, validate_across_periods, validate_development_holdout_run
from validation.periods import ValidationPeriod, load_periods
from validation.sensitivity import DEFAULT_VALUES, run_sensitivity
from validation.shortlist import rank_rows, shortlist_summary


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


def _add_combined_threshold_flags(parser: argparse.ArgumentParser) -> None:
    _add_graham_threshold_flags(parser)
    parser.add_argument("--preset", default=None)
    parser.add_argument("--minimum-five-day-decline", type=float, default=None)
    parser.add_argument("--minimum-ten-day-decline", type=float, default=None)
    parser.add_argument("--minimum-relative-volume", type=float, default=None)
    parser.add_argument("--maximum-rsi", type=float, default=None)
    parser.add_argument("--minimum-panic-score", type=float, default=None)
    parser.add_argument("--confirmation-window-days", type=int, default=None)


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
    audit_graham.add_argument("--tickers", nargs="+", required=True)
    audit_graham.add_argument("--as-of", required=True)
    _add_graham_threshold_flags(audit_graham)
    audit_graham.add_argument("--json", action="store_true")
    audit_graham.add_argument("--export-dir", default=None)

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

    update_universe = subparsers.add_parser("update-universe", help="Build or refresh the central security universe")
    update_universe.add_argument("--source", choices=["all", "sec"], default="all")
    update_universe.add_argument("--dry-run", action="store_true")
    update_universe.add_argument("--json", action="store_true")
    update_universe.add_argument("--export-dir", default=None)

    subparsers.add_parser("universe-status", help="Show central security universe status")

    list_universe = subparsers.add_parser("list-universe", help="List central security universe rows")
    list_universe.add_argument("--eligible-only", action="store_true")
    list_universe.add_argument("--exchange", default=None)
    list_universe.add_argument("--security-type", default=None)
    list_universe.add_argument("--limit", type=int, default=None)
    list_universe.add_argument("--offset", type=int, default=0)
    list_universe.add_argument("--sort-by", default="normalized_ticker")
    list_universe.add_argument("--descending", action="store_true")
    list_universe.add_argument("--json", action="store_true")
    list_universe.add_argument("--export-dir", default=None)

    sample_universe = subparsers.add_parser("sample-universe", help="Create a deterministic universe sample")
    sample_universe.add_argument("--eligible-only", action="store_true")
    sample_universe.add_argument("--count", type=int, default=100)
    sample_universe.add_argument("--seed", type=int, default=42)
    sample_universe.add_argument("--method", choices=["deterministic"], default="deterministic")
    sample_universe.add_argument("--json", action="store_true")
    sample_universe.add_argument("--export-dir", default=None)

    universe_prices = subparsers.add_parser("update-universe-prices", help="Batch update prices for a universe")
    src = universe_prices.add_mutually_exclusive_group(required=True)
    src.add_argument("--universe", choices=["all-eligible"], default=None)
    src.add_argument("--ticker-file", default=None)
    universe_prices.add_argument("--limit", type=int, default=None)
    universe_prices.add_argument("--offset", type=int, default=0)
    universe_prices.add_argument("--start-date", default=None)
    universe_prices.add_argument("--end-date", default=None)
    universe_prices.add_argument("--years", type=int, default=None)
    universe_prices.add_argument("--batch-size", type=int, default=25)
    universe_prices.add_argument("--retry-failures", action="store_true")
    universe_prices.add_argument("--max-retries", type=int, default=0)
    universe_prices.add_argument("--resume-run", type=int, default=None)
    universe_prices.add_argument("--dry-run", action="store_true")
    universe_prices.add_argument("--json", action="store_true")
    universe_prices.add_argument("--export-dir", default=None)

    universe_fundamentals = subparsers.add_parser("update-universe-fundamentals", help="Batch update SEC fundamentals for a universe")
    src = universe_fundamentals.add_mutually_exclusive_group(required=True)
    src.add_argument("--universe", choices=["all-eligible"], default=None)
    src.add_argument("--ticker-file", default=None)
    universe_fundamentals.add_argument("--limit", type=int, default=None)
    universe_fundamentals.add_argument("--offset", type=int, default=0)
    universe_fundamentals.add_argument("--years", type=int, default=None)
    universe_fundamentals.add_argument("--as-of", default=None)
    universe_fundamentals.add_argument("--batch-size", type=int, default=10)
    universe_fundamentals.add_argument("--retry-failures", action="store_true")
    universe_fundamentals.add_argument("--max-retries", type=int, default=0)
    universe_fundamentals.add_argument("--resume-run", type=int, default=None)
    universe_fundamentals.add_argument("--refresh-normalization", action="store_true")
    universe_fundamentals.add_argument("--force", action="store_true")
    universe_fundamentals.add_argument("--skip-existing", action="store_true")
    universe_fundamentals.add_argument("--dry-run", action="store_true")
    universe_fundamentals.add_argument("--json", action="store_true")
    universe_fundamentals.add_argument("--export-dir", default=None)

    refresh_norm = subparsers.add_parser("refresh-fundamentals-normalization", help="Refresh normalized fundamentals for selected tickers")
    src = refresh_norm.add_mutually_exclusive_group(required=True)
    src.add_argument("--tickers", nargs="+", default=None)
    src.add_argument("--ticker-file", default=None)
    src.add_argument("--universe", choices=["all-eligible"], default=None)
    refresh_norm.add_argument("--limit", type=int, default=None)
    refresh_norm.add_argument("--offset", type=int, default=0)
    refresh_norm.add_argument("--years", type=int, default=None)
    refresh_norm.add_argument("--as-of", default=None)
    refresh_norm.add_argument("--dry-run", action="store_true")
    refresh_norm.add_argument("--force", action="store_true")
    refresh_norm.add_argument("--skip-download", action="store_true")
    refresh_norm.add_argument("--run-audit", action="store_true")
    refresh_norm.add_argument("--json", action="store_true")
    refresh_norm.add_argument("--export-dir", default=None)

    coverage = subparsers.add_parser("universe-coverage-report", help="Report stored-data coverage for a universe")
    src = coverage.add_mutually_exclusive_group(required=True)
    src.add_argument("--ticker-file", default=None)
    src.add_argument("--universe", choices=["all-eligible"], default=None)
    coverage.add_argument("--limit", type=int, default=None)
    coverage.add_argument("--offset", type=int, default=0)
    coverage.add_argument("--as-of", default=None)
    coverage.add_argument("--json", action="store_true")
    coverage.add_argument("--export-dir", default=None)

    readiness = subparsers.add_parser("data-readiness-report", help="Report stored-data readiness without downloading")
    readiness.add_argument("--ticker-file", required=True)
    readiness.add_argument("--as-of", required=True)
    readiness.add_argument("--price-years", type=int, default=6)
    readiness.add_argument("--json", action="store_true")
    readiness.add_argument("--export-dir", default=None)

    prepare = subparsers.add_parser("prepare-universe-data", help="Resume-safe data preparation for a ticker file")
    prepare.add_argument("--ticker-file", required=True)
    prepare.add_argument("--as-of", default=None)
    prepare.add_argument("--price-years", type=int, default=6)
    prepare.add_argument("--fundamental-years", type=int, default=6)
    prepare.add_argument("--price-batch-size", type=int, default=25)
    prepare.add_argument("--fundamental-batch-size", type=int, default=10)
    prepare.add_argument("--refresh-normalization", action="store_true")
    prepare.add_argument("--resume", action="store_true")
    prepare.add_argument("--json", action="store_true")
    prepare.add_argument("--export-dir", default=None)

    screen_combined = subparsers.add_parser("screen-combined", help="Screen combined Graham plus technical candidates")
    src = screen_combined.add_mutually_exclusive_group()
    src.add_argument("--tickers", nargs="+", default=None)
    src.add_argument("--ticker-file", default=None)
    src.add_argument("--universe", choices=["all-eligible"], default=None)
    screen_combined.add_argument("--limit", type=int, default=None)
    screen_combined.add_argument("--offset", type=int, default=0)
    screen_combined.add_argument("--as-of", required=True)
    _add_combined_threshold_flags(screen_combined)
    screen_combined.add_argument("--qualified-only", action="store_true")
    screen_combined.add_argument("--sort-by", default="combined_score")
    screen_combined.add_argument("--descending", action=argparse.BooleanOptionalAction, default=True)
    screen_combined.add_argument("--json", action="store_true")
    screen_combined.add_argument("--export-dir", default=None)

    combined_backtest = subparsers.add_parser("run-combined-backtest", help="Run combined Graham plus technical backtest")
    src = combined_backtest.add_mutually_exclusive_group()
    src.add_argument("--tickers", nargs="+", default=None)
    src.add_argument("--ticker-file", default=None)
    src.add_argument("--universe", choices=["all-eligible"], default=None)
    combined_backtest.add_argument("--limit", type=int, default=None)
    combined_backtest.add_argument("--offset", type=int, default=0)
    combined_backtest.add_argument("--start-date", required=True)
    combined_backtest.add_argument("--end-date", required=True)
    combined_backtest.add_argument("--starting-capital", type=float, default=100000.0)
    combined_backtest.add_argument("--maximum-positions", type=int, default=10)
    combined_backtest.add_argument("--position-size-pct", type=float, default=0.10)
    combined_backtest.add_argument("--slippage-pct", type=float, default=0.001)
    combined_backtest.add_argument("--commission", type=float, default=0.0)
    _add_combined_threshold_flags(combined_backtest)
    combined_backtest.add_argument("--maximum-holding-days", type=int, default=504)
    combined_backtest.add_argument("--stop-loss-pct", type=float, default=None)
    combined_backtest.add_argument("--benchmark", default=None)
    combined_backtest.add_argument("--no-persist", action="store_true")
    combined_backtest.add_argument("--export-dir", default=None)

    compare_strategies = subparsers.add_parser("compare-strategies", help="Compare Graham, technical, and combined strategies")
    src = compare_strategies.add_mutually_exclusive_group()
    src.add_argument("--tickers", nargs="+", default=None)
    src.add_argument("--ticker-file", default=None)
    src.add_argument("--universe", choices=["all-eligible"], default=None)
    compare_strategies.add_argument("--limit", type=int, default=None)
    compare_strategies.add_argument("--offset", type=int, default=0)
    compare_strategies.add_argument("--start-date", required=True)
    compare_strategies.add_argument("--end-date", required=True)
    compare_strategies.add_argument("--starting-capital", type=float, default=100000.0)
    compare_strategies.add_argument("--maximum-positions", type=int, default=10)
    compare_strategies.add_argument("--position-size-pct", type=float, default=0.10)
    compare_strategies.add_argument("--slippage-pct", type=float, default=0.001)
    compare_strategies.add_argument("--commission", type=float, default=0.0)
    _add_combined_threshold_flags(compare_strategies)
    compare_strategies.add_argument("--maximum-holding-days", type=int, default=504)
    compare_strategies.add_argument("--benchmark", default=None)
    compare_strategies.add_argument("--export-dir", default=None)

    validate_strategy = subparsers.add_parser("validate-strategy", help="Run development and holdout validation")
    validate_strategy.add_argument("--strategy", choices=["graham", "technical", "combined"], required=True)
    validate_strategy.add_argument("--preset", default=None)
    src = validate_strategy.add_mutually_exclusive_group(required=True)
    src.add_argument("--ticker-file", default=None)
    src.add_argument("--universe", choices=["all-eligible"], default=None)
    validate_strategy.add_argument("--limit", type=int, default=None)
    validate_strategy.add_argument("--development-start", required=True)
    validate_strategy.add_argument("--development-end", required=True)
    validate_strategy.add_argument("--holdout-start", required=True)
    validate_strategy.add_argument("--holdout-end", required=True)
    validate_strategy.add_argument("--benchmark", default=None)
    validate_strategy.add_argument("--starting-capital", type=float, default=100000.0)
    validate_strategy.add_argument("--maximum-positions", type=int, default=10)
    validate_strategy.add_argument("--position-size-pct", type=float, default=0.10)
    validate_strategy.add_argument("--slippage-pct", type=float, default=0.001)
    validate_strategy.add_argument("--commission", type=float, default=0.0)
    validate_strategy.add_argument("--json", action="store_true")
    validate_strategy.add_argument("--export-dir", default=None)

    validate_periods = subparsers.add_parser("validate-across-periods", help="Validate a strategy across named periods")
    validate_periods.add_argument("--strategy", choices=["graham", "technical", "combined"], required=True)
    validate_periods.add_argument("--preset", default=None)
    src = validate_periods.add_mutually_exclusive_group(required=True)
    src.add_argument("--ticker-file", default=None)
    src.add_argument("--universe", choices=["all-eligible"], default=None)
    validate_periods.add_argument("--limit", type=int, default=None)
    validate_periods.add_argument("--periods-file", required=True)
    validate_periods.add_argument("--benchmark", default=None)
    validate_periods.add_argument("--json", action="store_true")
    validate_periods.add_argument("--export-dir", default=None)

    sensitivity = subparsers.add_parser("run-sensitivity-analysis", help="Run nearby-value one-parameter sensitivity")
    sensitivity.add_argument("--strategy", choices=["graham", "technical", "combined"], required=True)
    sensitivity.add_argument("--preset", default=None)
    src = sensitivity.add_mutually_exclusive_group(required=True)
    src.add_argument("--ticker-file", default=None)
    src.add_argument("--universe", choices=["all-eligible"], default=None)
    sensitivity.add_argument("--limit", type=int, default=None)
    sensitivity.add_argument("--start-date", required=True)
    sensitivity.add_argument("--end-date", required=True)
    sensitivity.add_argument("--parameter", required=True)
    sensitivity.add_argument("--values", nargs="+", default=None)
    sensitivity.add_argument("--benchmark", default=None)
    sensitivity.add_argument("--json", action="store_true")
    sensitivity.add_argument("--export-dir", default=None)

    shortlist = subparsers.add_parser("shortlist-opportunities", help="Rank a practical research shortlist")
    shortlist.add_argument("--strategy", choices=["graham", "technical", "combined"], required=True)
    shortlist.add_argument("--preset", default=None)
    src = shortlist.add_mutually_exclusive_group()
    src.add_argument("--tickers", nargs="+", default=None)
    src.add_argument("--ticker-file", default=None)
    src.add_argument("--universe", choices=["all-eligible"], default=None)
    shortlist.add_argument("--limit", type=int, default=None)
    shortlist.add_argument("--offset", type=int, default=0)
    shortlist.add_argument("--as-of", required=True)
    shortlist.add_argument("--top", type=int, default=25)
    shortlist.add_argument("--qualified-only", action=argparse.BooleanOptionalAction, default=True)
    shortlist.add_argument("--include-failures", action="store_true")
    shortlist.add_argument("--minimum-data-quality", type=float, default=None)
    shortlist.add_argument("--minimum-margin-of-safety", type=float, default=None)
    shortlist.add_argument("--minimum-panic-score", type=float, default=None)
    shortlist.add_argument("--sort-by", default="ranking_score")
    shortlist.add_argument("--descending", action=argparse.BooleanOptionalAction, default=True)
    shortlist.add_argument("--json", action="store_true")
    shortlist.add_argument("--export-dir", default=None)

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


def _source_tickers(args: argparse.Namespace) -> List[str]:
    if getattr(args, "ticker_file", None):
        tickers = read_ticker_file(args.ticker_file)
    elif getattr(args, "universe", None) == "all-eligible":
        tickers = universe_tickers(True, limit=getattr(args, "limit", None), offset=getattr(args, "offset", 0))
    else:
        tickers = list(getattr(args, "tickers", None) or DEFAULT_TEST_TICKERS)
        offset = max(0, getattr(args, "offset", 0) or 0)
        limit = getattr(args, "limit", None)
        tickers = tickers[offset:]
        if limit is not None:
            tickers = tickers[:limit]
    seen = set()
    result = []
    for ticker in tickers:
        value = ticker.strip().upper()
        if value and value not in seen:
            result.append(value)
            seen.add(value)
    return result


def _combined_config(args: argparse.Namespace) -> CombinedStrategyConfig:
    base = get_combined_preset(args.preset) if getattr(args, "preset", None) else CombinedStrategyConfig()
    graham = GrahamStrategyConfig(
        args.minimum_margin_of_safety,
        args.minimum_graham_score,
        args.minimum_data_quality_score,
        args.minimum_profitable_years,
        args.exclude_financials,
        args.exclude_reits,
    )
    technical = TechnicalCapitulationConfig(
        args.minimum_five_day_decline if args.minimum_five_day_decline is not None else base.technical.minimum_five_day_decline,
        args.minimum_ten_day_decline if args.minimum_ten_day_decline is not None else base.technical.minimum_ten_day_decline,
        args.minimum_relative_volume if args.minimum_relative_volume is not None else base.technical.minimum_relative_volume,
        args.maximum_rsi if args.maximum_rsi is not None else base.technical.maximum_rsi,
        args.minimum_panic_score if args.minimum_panic_score is not None else base.technical.minimum_panic_score,
        base.technical.require_volume_spike,
        base.technical.require_oversold,
        base.technical.moving_average_window,
        base.technical.minimum_distance_below_moving_average,
        base.technical.rsi_window,
        base.technical.volume_lookback,
        args.confirmation_window_days if args.confirmation_window_days is not None else base.technical.confirmation_window_days,
    )
    return CombinedStrategyConfig(graham, technical, base.combination_mode, base.graham_weight, base.technical_weight, base.minimum_combined_score, base.require_graham_first, base.graham_signal_validity_days, base.technical_signal_validity_days)


def _combined_strategy(args: argparse.Namespace) -> CombinedGrahamTechnicalStrategy:
    return CombinedGrahamTechnicalStrategy(
        config=_combined_config(args),
        graham_strategy=_graham_strategy(args),
        maximum_holding_days=getattr(args, "maximum_holding_days", 504),
        stop_loss_pct=getattr(args, "stop_loss_pct", None),
    )


def _update_universe_command(args: argparse.Namespace) -> None:
    initialize_database()
    result = build_universe_from_sec_map(dry_run=args.dry_run)
    payload = result.__dict__
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        for key, value in payload.items():
            print(f"{key}: {value}")
    if args.export_dir:
        print(f"Exported {write_json(Path(args.export_dir) / 'universe-update.json', payload)}")


def _universe_status_command() -> None:
    initialize_database()
    status = get_universe_status()
    for key, value in status.items():
        print(f"{key}: {value}")


def _list_universe_command(args: argparse.Namespace) -> None:
    rows = list_security_universe(args.eligible_only, args.exchange, args.security_type, args.limit, args.offset, args.sort_by, args.descending)
    if args.json:
        print(json.dumps(rows, indent=2, sort_keys=True, default=str))
    else:
        for row in rows:
            print(f"{row['normalized_ticker']}\t{row.get('company_name')}\t{row.get('exchange')}\t{row.get('eligibility_status')}\t{row.get('eligibility_reasons') or ''}")
    if args.export_dir:
        print(f"Exported {write_json(Path(args.export_dir) / 'security-universe.json', rows)}")


def _sample_universe_command(args: argparse.Namespace) -> None:
    tickers = universe_tickers(args.eligible_only)
    sample = deterministic_sample(tickers, args.count, args.seed)
    payload = {"count": len(sample), "eligible_only": args.eligible_only, "seed": args.seed, "tickers": sample}
    if args.export_dir:
        directory = Path(args.export_dir)
        directory.mkdir(parents=True, exist_ok=True)
        txt = directory / f"eligible-universe-{args.count}.txt"
        txt.write_text("\n".join(sample) + "\n", encoding="utf-8")
        write_json(directory / f"eligible-universe-{args.count}.json", payload)
        print(f"Exported {txt}")
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print("\n".join(sample))


def _update_universe_prices_command(args: argparse.Namespace) -> None:
    tickers = _source_tickers(args)
    result = run_tracked_batch("prices", tickers, lambda ticker: update_ticker_prices(ticker, start_date=args.start_date, end_date=args.end_date), dry_run=args.dry_run, max_retries=args.max_retries, resume_run=args.resume_run)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True, default=str))
    else:
        print(f"requested={result['requested_count']} succeeded={result['succeeded_count']} failed={result['failed_count']} skipped={result['skipped_count']} run_id={result['run_id']}")


def _update_universe_fundamentals_command(args: argparse.Namespace) -> None:
    tickers = _source_tickers(args)
    if args.dry_run:
        result = run_tracked_batch("fundamentals", tickers, lambda ticker: {"status": "skipped", "skipped_count": 1}, dry_run=True, max_retries=args.max_retries, resume_run=args.resume_run)
    else:
        result = {"requested_count": len(tickers), "status": "blocked", "error": "SEC_USER_AGENT is required for SEC fundamentals ingestion"}
    print(json.dumps(result, indent=2, sort_keys=True, default=str) if args.json else result)


def _refresh_fundamentals_normalization_command(args: argparse.Namespace) -> None:
    tickers = _source_tickers(args)
    payload = {"tickers": tickers, "status": "skipped" if args.skip_download or args.dry_run else "blocked", "reason": "local normalization refresh is deterministic; SEC re-ingestion requires SEC_USER_AGENT"}
    print(json.dumps(payload, indent=2, sort_keys=True) if args.json else payload)


def _universe_coverage_report_command(args: argparse.Namespace) -> None:
    tickers = _source_tickers(args)
    payload = coverage_freshness(tickers, as_of=args.as_of)
    if args.export_dir:
        print(f"Exported {write_json(Path(args.export_dir) / 'universe-coverage-report.json', payload)}")
    print(json.dumps(payload, indent=2, sort_keys=True, default=str) if args.json else payload)


def _data_readiness_report_command(args: argparse.Namespace) -> None:
    symbols = read_input_symbols(args.ticker_file)
    payload = build_readiness_report(symbols, args.as_of, price_years=args.price_years)
    audit = database_audit_for_tickers(symbols, args.as_of, price_years=args.price_years)
    payload["database_audit"] = audit
    if args.export_dir:
        paths = export_readiness_report(payload, args.export_dir)
        print(f"Exported {paths['json']}")
        print(f"Exported {paths['csv']}")
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
        return
    summary = payload["summary"]
    reconciliation = payload["reconciliation"]
    print(f"requested={payload['requested_count']} unique={payload['unique_normalized_count']} ready={summary['ready']}")
    print(f"price_ready={summary['price_ready']} graham_evaluable={summary['graham_evaluable']} technical_evaluable={summary['technical_evaluable']} combined_evaluable={summary['combined_evaluable']}")
    print(f"categories={summary['categories']}")
    print(f"reconciliation_invariant={reconciliation['invariant_holds']} unexplained={reconciliation['unexplained_count']}")


def _prepare_universe_data_command(args: argparse.Namespace) -> None:
    symbols = read_input_symbols(args.ticker_file)
    payload = prepare_universe_data(
        symbols,
        price_years=args.price_years,
        fundamental_years=args.fundamental_years,
        as_of=args.as_of,
        refresh_normalization=args.refresh_normalization,
        resume=args.resume,
        export_dir=None,
    )
    if args.export_dir:
        paths = export_preparation_report(payload, args.export_dir)
        print(f"Exported {paths['json']}")
        print(f"Exported {paths['failures_csv']}")
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
        return
    summary = payload["summary"]
    print(f"run_identifier={summary['run_identifier']}")
    print(f"requested={summary['requested_ticker_count']} already_complete={summary['already_complete_count']} final_ready={summary['final_ready_count']} remaining_not_ready={summary['remaining_not_ready_count']}")
    print(f"attempted_by_stage={summary['attempted_count_by_stage']}")
    print(f"failed_by_stage={summary['failed_count_by_stage']}")


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


def _audit_graham_data_command(args: argparse.Namespace) -> None:
    strategy = _graham_strategy(args)
    rows = [graham_audit_row(strategy.evaluate(ticker, args.as_of)) for ticker in args.tickers]
    if args.json:
        print(json.dumps(rows, indent=2, sort_keys=True, default=str))
    else:
        headers = [
            "ticker",
            "price_available",
            "eps_available",
            "eps_method",
            "shares_available",
            "shares_method",
            "equity_available",
            "current_assets_available",
            "current_liabilities_available",
            "debt_available",
            "five_year_earnings_history_count",
            "data_quality_score",
            "graham_ready",
            "primary_missing_reason",
            "warning_count",
        ]
        print("\t".join(headers))
        for row in rows:
            print("\t".join(str(row.get(header, "")) for header in headers))
    if args.export_dir:
        directory = Path(args.export_dir)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"graham-data-audit-{args.as_of}.json"
        path.write_text(json.dumps(rows, indent=2, sort_keys=True, default=str), encoding="utf-8")
        print(f"Exported {path}")


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


def _screen_combined_command(args: argparse.Namespace) -> None:
    try:
        tickers = _source_tickers(args)
        strategy = _combined_strategy(args)
    except (ValueError, ConfigurationValidationError) as exc:
        print(f"Combined screen failed: {exc}")
        return
    evaluations = []
    for ticker in tickers:
        history = repository_strategy_data.get_ticker_history(ticker, end_date=args.as_of)
        try:
            evaluations.append(strategy.evaluate(ticker, args.as_of, history))
        except Exception as exc:
            print(f"{ticker}: combined evaluation failed: {exc}")
    evaluations = rank_combined_candidates(evaluations)
    rows = [combined_summary_row(item) for item in evaluations]
    if args.qualified_only:
        rows = [row for row in rows if row["combined_qualified"]]
    if args.sort_by and rows and args.sort_by in rows[0]:
        rows = sorted(rows, key=lambda row: (row.get(args.sort_by) is None, row.get(args.sort_by), row["ticker"]), reverse=args.descending)
    summary = combined_coverage_summary(rows)
    payload = {"rows": rows, "coverage_summary": summary}
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    else:
        for row in rows:
            print(f"{row['ticker']} data_ready={row['data_ready']} graham={row['graham_qualified']} technical={row['technical_qualified']} combined={row['combined_qualified']} score={row['combined_score']} data_issue={row['primary_data_issue'] or '(none)'} graham_fail={row['primary_graham_failure'] or '(none)'} technical_fail={row['primary_technical_failure'] or '(none)'} combined_fail={row['primary_combined_failure'] or '(none)'}")
        print(f"coverage_summary: {summary}")
    if args.export_dir:
        for path in export_combined_evaluations(evaluations, args.export_dir):
            print(f"Exported {path}")


def _run_combined_backtest_command(args: argparse.Namespace) -> None:
    try:
        tickers = _source_tickers(args)
        strategy = _combined_strategy(args)
    except (ValueError, ConfigurationValidationError) as exc:
        print(f"Combined backtest failed: {exc}")
        return
    config = BacktestConfig("combined_graham_technical_v1", tickers, args.start_date, args.end_date, args.starting_capital, args.maximum_positions, args.position_size_pct, args.slippage_pct, args.commission, args.maximum_holding_days)
    try:
        result = run_backtest(config, strategy, benchmark_ticker=args.benchmark, persist=not args.no_persist)
    except ValueError as exc:
        print(f"Combined backtest failed: {exc}")
        print("Run update-prices and update-fundamentals first; this command does not download missing data.")
        return
    _print_backtest_result(result)


def _compare_strategies_command(args: argparse.Namespace) -> None:
    try:
        tickers = _source_tickers(args)
        combined = _combined_strategy(args)
    except (ValueError, ConfigurationValidationError) as exc:
        print(f"Strategy comparison failed: {exc}")
        return
    configs = [
        BacktestConfig("graham_value_v1", tickers, args.start_date, args.end_date, args.starting_capital, args.maximum_positions, args.position_size_pct, args.slippage_pct, args.commission, args.maximum_holding_days),
        BacktestConfig("moving_average_reversion", tickers, args.start_date, args.end_date, args.starting_capital, args.maximum_positions, args.position_size_pct, args.slippage_pct, args.commission, args.maximum_holding_days),
        BacktestConfig("combined_graham_technical_v1", tickers, args.start_date, args.end_date, args.starting_capital, args.maximum_positions, args.position_size_pct, args.slippage_pct, args.commission, args.maximum_holding_days),
    ]
    strategies = [_graham_strategy(args), MovingAverageReversionStrategy(maximum_holding_days=args.maximum_holding_days), combined]
    rows = []
    for config, strategy in zip(configs, strategies):
        try:
            result = run_backtest(config, strategy, benchmark_ticker=args.benchmark, persist=False)
            rows.append({"strategy": config.strategy_name, "requested_universe": len(tickers), "evaluated_universe": len(tickers), "skipped_tickers": 0, "total_return": result.metrics.get("total_return_pct"), "max_drawdown": result.metrics.get("maximum_drawdown"), "completed_trades": result.metrics.get("completed_trade_count"), "win_rate": result.metrics.get("win_rate"), "average_trade": result.metrics.get("average_trade_return"), "average_holding_period": result.metrics.get("average_holding_period_days"), "benchmark_return": (result.metrics.get("benchmark") or {}).get("return_pct")})
        except ValueError as exc:
            rows.append({"strategy": config.strategy_name, "requested_universe": len(tickers), "evaluated_universe": 0, "skipped_tickers": len(tickers), "error": str(exc)})
    print(json.dumps(rows, indent=2, sort_keys=True, default=str))


def _validation_kwargs(args: argparse.Namespace) -> dict:
    return {
        "benchmark": getattr(args, "benchmark", None),
        "starting_capital": getattr(args, "starting_capital", 100000.0),
        "maximum_positions": getattr(args, "maximum_positions", 10),
        "position_size_pct": getattr(args, "position_size_pct", 0.10),
        "slippage_pct": getattr(args, "slippage_pct", 0.001),
        "commission": getattr(args, "commission", 0.0),
        "combined_config": get_combined_preset(args.preset) if getattr(args, "preset", None) else None,
    }


def _validate_strategy_command(args: argparse.Namespace) -> None:
    tickers = _source_tickers(args)
    development = ValidationPeriod("development", args.development_start, args.development_end, "development", "")
    holdout = ValidationPeriod("holdout", args.holdout_start, args.holdout_end, "holdout", "")
    try:
        payload = validate_development_holdout_run(args.strategy, tickers, development, holdout, **_validation_kwargs(args))
    except ValueError as exc:
        print(f"Validation failed: {exc}")
        return
    payload = validation_report(payload)
    if args.export_dir:
        print(f"Exported {export_validation_report(payload, args.export_dir)}")
    print(json.dumps(payload, indent=2, sort_keys=True, default=str) if args.json else payload)


def _validate_across_periods_command(args: argparse.Namespace) -> None:
    tickers = _source_tickers(args)
    periods = load_periods(args.periods_file)
    try:
        payload = validate_across_periods(args.strategy, tickers, periods, **_validation_kwargs(args))
    except ValueError as exc:
        print(f"Validation failed: {exc}")
        return
    payload = validation_report(payload)
    if args.export_dir:
        print(f"Exported {export_validation_report(payload, args.export_dir, 'multi-period-validation.json')}")
    print(json.dumps(payload, indent=2, sort_keys=True, default=str) if args.json else payload)


def _run_sensitivity_analysis_command(args: argparse.Namespace) -> None:
    tickers = _source_tickers(args)
    period = ValidationPeriod("sensitivity", args.start_date, args.end_date, "sensitivity", "")
    values = [float(value) for value in args.values] if args.values else DEFAULT_VALUES.get(args.parameter)
    if not values:
        print(f"Sensitivity failed: no documented values for {args.parameter}")
        return
    try:
        payload = run_sensitivity(args.strategy, tickers, period, args.parameter, values, baseline_config=(get_combined_preset(args.preset) if args.preset else CombinedStrategyConfig()), benchmark=args.benchmark)
    except ValueError as exc:
        print(f"Sensitivity failed: {exc}")
        return
    if args.export_dir:
        print(f"Exported {export_validation_report(payload, args.export_dir, 'sensitivity-analysis.json')}")
    print(json.dumps(payload, indent=2, sort_keys=True, default=str) if args.json else payload)


def _shortlist_rows(args: argparse.Namespace) -> List[dict]:
    tickers = _source_tickers(args)
    for name, value in {
        "minimum_margin_of_safety": 0.30,
        "minimum_graham_score": 70.0,
        "minimum_data_quality_score": 60.0,
        "minimum_profitable_years": 4,
        "minimum_price": 3.0,
        "minimum_market_cap": 300_000_000.0,
        "minimum_average_dollar_volume": 2_000_000.0,
        "exclude_financials": True,
        "exclude_reits": True,
    }.items():
        if getattr(args, name, None) is None:
            setattr(args, name, value)
    if args.strategy == "combined":
        for name, value in {
            "minimum_five_day_decline": None,
            "minimum_ten_day_decline": None,
            "minimum_relative_volume": None,
            "maximum_rsi": None,
            "minimum_panic_score": args.minimum_panic_score,
            "confirmation_window_days": None,
        }.items():
            if getattr(args, name, None) is None:
                setattr(args, name, value)
        strategy = _combined_strategy(args)
        rows = []
        for ticker in tickers:
            history = repository_strategy_data.get_ticker_history(ticker, end_date=args.as_of)
            try:
                rows.append(combined_summary_row(strategy.evaluate(ticker, args.as_of, history)))
            except Exception:
                rows.append({"ticker": ticker, "price": None, "data_ready": False, "combined_qualified": False, "primary_data_issue": "evaluation failed", "primary_combined_failure": "evaluation failed"})
        for row in rows:
            row["qualified"] = row.get("combined_qualified", False)
            row["primary_failure_reason"] = row.get("primary_combined_failure", "")
            row["data_quality_score"] = row.get("data_quality_score", 0.0)
        return rows
    if args.strategy == "graham":
        strategy = _graham_strategy(args)
        rows = []
        for ticker in tickers:
            try:
                row = graham_summary_row(strategy.evaluate(ticker, args.as_of))
                row.update({"qualified": row["qualification_status"] == "QUALIFIED", "panic_score": 0, "relative_volume": None, "rsi": None, "combined_score": row["graham_quality_score"], "primary_data_issue": "", "primary_failure_reason": row["disqualification_reasons"].split("; ")[0] if row["disqualification_reasons"] else ""})
                rows.append(row)
            except Exception:
                rows.append({"ticker": ticker, "qualified": False, "primary_data_issue": "evaluation failed", "primary_failure_reason": "evaluation failed"})
        return rows
    rows = []
    config = TechnicalCapitulationConfig()
    from strategies.combined_graham_technical import evaluate_technical_capitulation
    for ticker in tickers:
        technical = evaluate_technical_capitulation(ticker, args.as_of, repository_strategy_data.get_ticker_history(ticker, end_date=args.as_of), config)
        rows.append({"ticker": ticker, "price": None, "qualified": technical.qualified, "graham_score": 0, "margin_of_safety": 0, "panic_score": technical.panic_score.total_score, "relative_volume": technical.metrics.relative_volume, "rsi": technical.metrics.rsi, "combined_score": technical.panic_score.total_score / 15 * 100, "data_quality_score": 0, "primary_data_issue": "", "primary_failure_reason": technical.disqualification_reasons[0] if technical.disqualification_reasons else ""})
    return rows


def _shortlist_opportunities_command(args: argparse.Namespace) -> None:
    margin_filter = args.minimum_margin_of_safety
    data_quality_filter = args.minimum_data_quality
    panic_filter = args.minimum_panic_score
    rows = _shortlist_rows(args)
    if data_quality_filter is not None:
        rows = [row for row in rows if (row.get("data_quality_score") or 0) >= data_quality_filter]
    if margin_filter is not None:
        rows = [row for row in rows if (row.get("margin_of_safety") or 0) >= margin_filter]
    if panic_filter is not None:
        rows = [row for row in rows if (row.get("panic_score") or 0) >= panic_filter]
    if args.qualified_only and not args.include_failures:
        rows = [row for row in rows if row.get("qualified")]
    ranked = rank_rows(rows)[: args.top]
    summary = shortlist_summary(len(rows), len(rows), len(rows), ranked)
    payload = shortlist_report(ranked, summary)
    if args.export_dir:
        print(f"Exported {export_shortlist_report(ranked, summary, args.export_dir)}")
    print(json.dumps(payload, indent=2, sort_keys=True, default=str) if args.json else payload)


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
    elif args.command == "update-universe":
        _update_universe_command(args)
    elif args.command == "universe-status":
        _universe_status_command()
    elif args.command == "list-universe":
        _list_universe_command(args)
    elif args.command == "sample-universe":
        _sample_universe_command(args)
    elif args.command == "update-universe-prices":
        _update_universe_prices_command(args)
    elif args.command == "update-universe-fundamentals":
        _update_universe_fundamentals_command(args)
    elif args.command == "refresh-fundamentals-normalization":
        _refresh_fundamentals_normalization_command(args)
    elif args.command == "universe-coverage-report":
        _universe_coverage_report_command(args)
    elif args.command == "data-readiness-report":
        _data_readiness_report_command(args)
    elif args.command == "prepare-universe-data":
        _prepare_universe_data_command(args)
    elif args.command == "screen-combined":
        _screen_combined_command(args)
    elif args.command == "run-combined-backtest":
        _run_combined_backtest_command(args)
    elif args.command == "compare-strategies":
        _compare_strategies_command(args)
    elif args.command == "validate-strategy":
        _validate_strategy_command(args)
    elif args.command == "validate-across-periods":
        _validate_across_periods_command(args)
    elif args.command == "run-sensitivity-analysis":
        _run_sensitivity_analysis_command(args)
    elif args.command == "shortlist-opportunities":
        _shortlist_opportunities_command(args)
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

