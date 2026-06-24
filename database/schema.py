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
