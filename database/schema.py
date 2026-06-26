"""Database schema creation for AlgoTradProject."""

import logging
from pathlib import Path
from typing import Optional, Union

from database.connection import get_connection

logger = logging.getLogger(__name__)
DatabasePath = Union[str, Path]


_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS securities (
        ticker TEXT PRIMARY KEY,
        company_name TEXT,
        exchange TEXT,
        security_type TEXT,
        is_active INTEGER,
        updated_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS daily_prices (
        ticker TEXT,
        trade_date TEXT,
        open REAL,
        high REAL,
        low REAL,
        close REAL,
        adjusted_close REAL,
        volume INTEGER,
        downloaded_at TEXT,
        PRIMARY KEY (ticker, trade_date),
        FOREIGN KEY (ticker) REFERENCES securities(ticker)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fundamentals (
        ticker TEXT,
        period_end TEXT,
        filing_date TEXT,
        revenue REAL,
        net_income REAL,
        eps REAL,
        book_value_per_share REAL,
        current_assets REAL,
        current_liabilities REAL,
        long_term_debt REAL,
        shares_outstanding REAL,
        downloaded_at TEXT,
        PRIMARY KEY (ticker, period_end),
        FOREIGN KEY (ticker) REFERENCES securities(ticker)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS signals (
        signal_id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker TEXT,
        signal_date TEXT,
        strategy TEXT,
        signal_type TEXT,
        score REAL,
        reason TEXT,
        created_at TEXT,
        FOREIGN KEY (ticker) REFERENCES securities(ticker)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS backtest_runs (
        backtest_id INTEGER PRIMARY KEY AUTOINCREMENT,
        strategy TEXT,
        start_date TEXT,
        end_date TEXT,
        starting_capital REAL,
        ending_capital REAL,
        parameters_json TEXT,
        created_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS backtest_trades (
        trade_id INTEGER PRIMARY KEY AUTOINCREMENT,
        backtest_id INTEGER,
        ticker TEXT,
        signal_date TEXT,
        entry_date TEXT,
        entry_price REAL,
        exit_date TEXT,
        exit_price REAL,
        quantity REAL,
        pnl REAL,
        return_pct REAL,
        exit_reason TEXT,
        FOREIGN KEY (backtest_id) REFERENCES backtest_runs(backtest_id),
        FOREIGN KEY (ticker) REFERENCES securities(ticker)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS portfolio_snapshots (
        backtest_id INTEGER,
        snapshot_date TEXT,
        cash REAL,
        holdings_value REAL,
        total_value REAL,
        drawdown REAL,
        PRIMARY KEY (backtest_id, snapshot_date),
        FOREIGN KEY (backtest_id) REFERENCES backtest_runs(backtest_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sec_ticker_map (
        ticker TEXT PRIMARY KEY,
        cik TEXT NOT NULL,
        title TEXT,
        source TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fundamental_filings (
        filing_id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker TEXT NOT NULL,
        cik TEXT NOT NULL,
        accession_number TEXT NOT NULL,
        form_type TEXT NOT NULL,
        filing_date TEXT NOT NULL,
        accepted_at TEXT,
        report_period TEXT,
        fiscal_year INTEGER,
        fiscal_period TEXT,
        is_amendment INTEGER NOT NULL DEFAULT 0,
        source_url TEXT,
        downloaded_at TEXT NOT NULL,
        UNIQUE(cik, accession_number),
        FOREIGN KEY (ticker) REFERENCES securities(ticker)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fundamental_facts (
        fact_id INTEGER PRIMARY KEY AUTOINCREMENT,
        filing_id INTEGER NOT NULL,
        ticker TEXT NOT NULL,
        taxonomy TEXT NOT NULL,
        concept TEXT NOT NULL,
        standardized_field TEXT,
        unit TEXT,
        value REAL,
        period_start TEXT,
        period_end TEXT,
        frame TEXT,
        form_type TEXT,
        filed_date TEXT,
        accepted_at TEXT,
        fiscal_year INTEGER,
        fiscal_period TEXT,
        accession_number TEXT,
        source_name TEXT,
        downloaded_at TEXT NOT NULL,
        FOREIGN KEY (filing_id) REFERENCES fundamental_filings(filing_id),
        UNIQUE(filing_id, taxonomy, concept, unit, period_start, period_end, value)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS security_universe (
        ticker TEXT PRIMARY KEY,
        normalized_ticker TEXT NOT NULL UNIQUE,
        company_name TEXT,
        cik TEXT,
        exchange TEXT,
        security_type TEXT,
        sector TEXT,
        industry TEXT,
        is_active INTEGER NOT NULL DEFAULT 1,
        is_common_stock INTEGER NOT NULL DEFAULT 0,
        is_adr INTEGER NOT NULL DEFAULT 0,
        is_etf INTEGER NOT NULL DEFAULT 0,
        is_etn INTEGER NOT NULL DEFAULT 0,
        is_reit INTEGER NOT NULL DEFAULT 0,
        is_financial INTEGER NOT NULL DEFAULT 0,
        is_warrant INTEGER NOT NULL DEFAULT 0,
        is_right INTEGER NOT NULL DEFAULT 0,
        is_unit INTEGER NOT NULL DEFAULT 0,
        is_preferred INTEGER NOT NULL DEFAULT 0,
        is_otc INTEGER NOT NULL DEFAULT 0,
        source TEXT,
        source_updated_at TEXT,
        first_seen_at TEXT,
        last_seen_at TEXT,
        delisted_at TEXT,
        eligibility_status TEXT,
        eligibility_reasons TEXT,
        metadata_json TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ingestion_runs (
        run_id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_type TEXT NOT NULL,
        started_at TEXT NOT NULL,
        completed_at TEXT,
        requested_count INTEGER NOT NULL DEFAULT 0,
        succeeded_count INTEGER NOT NULL DEFAULT 0,
        partial_count INTEGER NOT NULL DEFAULT 0,
        failed_count INTEGER NOT NULL DEFAULT 0,
        configuration_json TEXT,
        status TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ingestion_run_items (
        item_id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id INTEGER NOT NULL,
        ticker TEXT NOT NULL,
        status TEXT NOT NULL,
        inserted_count INTEGER NOT NULL DEFAULT 0,
        updated_count INTEGER NOT NULL DEFAULT 0,
        unchanged_count INTEGER NOT NULL DEFAULT 0,
        skipped_count INTEGER NOT NULL DEFAULT 0,
        retry_count INTEGER NOT NULL DEFAULT 0,
        error_type TEXT,
        error_message TEXT,
        started_at TEXT,
        completed_at TEXT,
        FOREIGN KEY (run_id) REFERENCES ingestion_runs(run_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS provider_failure_cooldowns (
        provider TEXT NOT NULL,
        key_type TEXT NOT NULL,
        key_value TEXT NOT NULL,
        ticker TEXT,
        failure_type TEXT NOT NULL,
        retry_classification TEXT NOT NULL,
        error_message TEXT,
        first_seen_at TEXT NOT NULL,
        last_seen_at TEXT NOT NULL,
        cooldown_until TEXT,
        occurrence_count INTEGER NOT NULL DEFAULT 1,
        PRIMARY KEY (provider, key_type, key_value, failure_type)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS provider_refresh_status (
        provider TEXT NOT NULL,
        key_type TEXT NOT NULL,
        key_value TEXT NOT NULL,
        status TEXT NOT NULL,
        http_status INTEGER,
        last_success_at TEXT,
        last_retrieved_at TEXT NOT NULL,
        response_retrieved_at TEXT NOT NULL,
        ticker TEXT,
        metadata_json TEXT,
        PRIMARY KEY (provider, key_type, key_value)
    )
    """,
)

_INDEX_STATEMENTS = (
    "CREATE INDEX IF NOT EXISTS idx_daily_prices_ticker ON daily_prices(ticker)",
    "CREATE INDEX IF NOT EXISTS idx_daily_prices_trade_date ON daily_prices(trade_date)",
    "CREATE INDEX IF NOT EXISTS idx_daily_prices_ticker_trade_date ON daily_prices(ticker, trade_date)",
    "CREATE INDEX IF NOT EXISTS idx_fundamentals_ticker ON fundamentals(ticker)",
    "CREATE INDEX IF NOT EXISTS idx_fundamentals_period_end ON fundamentals(period_end)",
    "CREATE INDEX IF NOT EXISTS idx_signals_ticker ON signals(ticker)",
    "CREATE INDEX IF NOT EXISTS idx_signals_signal_date ON signals(signal_date)",
    "CREATE INDEX IF NOT EXISTS idx_signals_strategy_date ON signals(strategy, signal_date)",
    "CREATE INDEX IF NOT EXISTS idx_backtest_trades_backtest_id ON backtest_trades(backtest_id)",
    "CREATE INDEX IF NOT EXISTS idx_backtest_trades_ticker ON backtest_trades(ticker)",
    "CREATE INDEX IF NOT EXISTS idx_portfolio_snapshots_backtest_id ON portfolio_snapshots(backtest_id)",
    "CREATE INDEX IF NOT EXISTS idx_portfolio_snapshots_snapshot_date ON portfolio_snapshots(snapshot_date)",
    "CREATE INDEX IF NOT EXISTS idx_sec_ticker_map_cik ON sec_ticker_map(cik)",
    "CREATE INDEX IF NOT EXISTS idx_fundamental_filings_ticker ON fundamental_filings(ticker)",
    "CREATE INDEX IF NOT EXISTS idx_fundamental_filings_cik ON fundamental_filings(cik)",
    "CREATE INDEX IF NOT EXISTS idx_fundamental_filings_filing_date ON fundamental_filings(filing_date)",
    "CREATE INDEX IF NOT EXISTS idx_fundamental_filings_accepted_at ON fundamental_filings(accepted_at)",
    "CREATE INDEX IF NOT EXISTS idx_fundamental_filings_report_period ON fundamental_filings(report_period)",
    "CREATE INDEX IF NOT EXISTS idx_fundamental_filings_accession ON fundamental_filings(accession_number)",
    "CREATE INDEX IF NOT EXISTS idx_fundamental_filings_ticker_accepted ON fundamental_filings(ticker, accepted_at)",
    "CREATE INDEX IF NOT EXISTS idx_fundamental_facts_ticker ON fundamental_facts(ticker)",
    "CREATE INDEX IF NOT EXISTS idx_fundamental_facts_filing_id ON fundamental_facts(filing_id)",
    "CREATE INDEX IF NOT EXISTS idx_fundamental_facts_accepted_at ON fundamental_facts(accepted_at)",
    "CREATE INDEX IF NOT EXISTS idx_fundamental_facts_report_period ON fundamental_facts(period_end)",
    "CREATE INDEX IF NOT EXISTS idx_fundamental_facts_standardized_field ON fundamental_facts(standardized_field)",
    "CREATE INDEX IF NOT EXISTS idx_fundamental_facts_accession ON fundamental_facts(accession_number)",
    "CREATE INDEX IF NOT EXISTS idx_fundamental_facts_ticker_field_period ON fundamental_facts(ticker, standardized_field, period_end)",
    "CREATE INDEX IF NOT EXISTS idx_security_universe_eligibility ON security_universe(eligibility_status, normalized_ticker)",
    "CREATE INDEX IF NOT EXISTS idx_security_universe_exchange ON security_universe(exchange)",
    "CREATE INDEX IF NOT EXISTS idx_security_universe_type ON security_universe(security_type)",
    "CREATE INDEX IF NOT EXISTS idx_security_universe_cik ON security_universe(cik)",
    "CREATE INDEX IF NOT EXISTS idx_ingestion_runs_type ON ingestion_runs(run_type, status)",
    "CREATE INDEX IF NOT EXISTS idx_ingestion_run_items_run ON ingestion_run_items(run_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_ingestion_run_items_ticker ON ingestion_run_items(ticker)",
    "CREATE INDEX IF NOT EXISTS idx_provider_failure_cooldowns_key ON provider_failure_cooldowns(provider, key_type, key_value)",
    "CREATE INDEX IF NOT EXISTS idx_provider_failure_cooldowns_until ON provider_failure_cooldowns(cooldown_until)",
    "CREATE INDEX IF NOT EXISTS idx_provider_refresh_status_key ON provider_refresh_status(provider, key_type, key_value)",
    "CREATE INDEX IF NOT EXISTS idx_provider_refresh_status_success ON provider_refresh_status(provider, last_success_at)",
)


def initialize_database(database_path: Optional[DatabasePath] = None) -> None:
    """Create database tables and indexes if they do not already exist."""
    logger.info("Initializing database%s", f" at {database_path}" if database_path else "")
    with get_connection(database_path) as connection:
        for statement in _SCHEMA_STATEMENTS:
            connection.execute(statement)
        for statement in _INDEX_STATEMENTS:
            connection.execute(statement)
    logger.info("Database initialization complete")
