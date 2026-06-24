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
