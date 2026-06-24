# AlgoTradProject

AlgoTradProject is a modular Python 3.9-compatible foundation for historical backtesting and eventual paper trading. The current phase adds a read-only strategy foundation on top of the centralized SQLite market-data layer. Daily OHLCV data is still downloaded once and stored in SQLite; strategies and indicators consume stored data only.

Live brokerage integration, live trading, options, machine learning, dashboards, and full backtesting are intentionally not included yet.

## Current phase

Phase 3B: minimal deterministic historical backtesting and portfolio simulation engine.

Included:

- SQLite connection management with foreign keys enabled
- Idempotent database schema initialization
- Repository functions for securities and daily prices
- yfinance-based daily OHLCV downloads
- Safe daily price UPSERTs using `(ticker, trade_date)`
- Incremental updates that start after the latest stored date
- Validation for downloaded price rows
- CLI commands for initialization, updates, price display, and database status
- Pytest coverage with yfinance mocked for offline tests
- Read-only strategy data access through repository functions
- Standardized `Signal`, `Position`, `Trade`, and `BacktestConfig` dataclasses
- `SignalAction` enum values: `BUY`, `SELL`, `HOLD`
- Moving average and return indicators
- Diagnostic moving-average reversion strategy that generates signals only
- Deterministic next-day-open backtest execution engine
- Whole-share long-only portfolio simulation
- Slippage, commissions, maximum positions, daily snapshots, metrics, benchmark returns, and SQLite persistence

## Project structure

```text
AlgoTradProject/
    config/
        __init__.py
        settings.py
    data/
        __init__.py
        market_data.py
        strategy_data.py
        validation.py
    database/
        __init__.py
        connection.py
        schema.py
        repositories.py
    indicators/
        __init__.py
        moving_averages.py
        returns.py
    backtesting/
        __init__.py
        engine.py
        execution.py
        metrics.py
        models.py
    portfolio/
        __init__.py
        manager.py
        position_sizing.py
    strategies/
        base.py
        moving_average_reversion.py
    tests/
        test_backtest_engine.py
        test_backtest_metrics.py
        test_backtest_persistence.py
        test_database.py
        test_execution.py
        test_indicators.py
        test_market_data.py
        test_moving_average_reversion.py
        test_portfolio_manager.py
        test_position_sizing.py
        test_repositories.py
        test_strategy_data.py
    main.py
    requirements.txt
    README.md
    .gitignore
```


## Phase 3B backtesting architecture

Phase 3B adds a minimal historical backtesting engine that consumes standardized `Signal` objects from strategies and executes simulated long-only orders against stored SQLite daily prices. It does not download market data during backtests. Missing history should be resolved with `update-prices` before running a backtest.

Core definitions:

- `Signal`: strategy output with ticker, signal date, strategy name, action, score, and reason.
- `Order`: pending executable instruction with ticker, side (`BUY` or `SELL`), quantity, signal date, execution date, strategy, score, and reason.
- `Position`: an open long holding with entry date, entry price, quantity, current price, strategy, signal date, entry commission, score, and reason.
- `Trade`: a completed round trip with entry/exit dates and prices, quantity, gross P&L, net P&L, return percentage, commissions, and exit reason.
- `PortfolioSnapshot`: end-of-day cash, holdings value, total value, and drawdown.

Daily event order:

1. Identify the current trading date from the union of stored dates for configured tickers.
2. Execute pending orders scheduled for the current date at the current open.
3. Mark open positions to market using the current close or most recent prior close.
4. Evaluate exit signals after the current close.
5. Evaluate entry signals after the current close.
6. Rank competing entries by score descending, then ticker ascending.
7. Schedule accepted orders for the ticker's next available trading-day open.
8. Record the end-of-day portfolio snapshot.

Signals generated from a close never execute at that same close. This next-day-open rule is intended to prevent same-bar execution and reduce look-ahead bias.

Position sizing uses whole shares only. The engine calculates quantity at execution using current portfolio value, available cash, configured position-size percentage, slippage-adjusted fill price, and commission. It prevents leverage, short selling, fractional shares, duplicate simultaneous positions, and more than the configured maximum number of positions.

Slippage is applied deterministically: buys execute at `open * (1 + slippage_pct)` and sells execute at `open * (1 - slippage_pct)`. Commission is charged once per executed order. If a next-day open is missing or invalid, the order is skipped rather than filled at another price.

At the final backtest date, any open positions are liquidated at the final available close with sell slippage and commission. The exit reason is `OPEN_AT_END_LIQUIDATION`; this is a backtest boundary assumption, not a trading rule.

Performance metrics include starting capital, ending value, total return, trade counts, win rate, average trade return, gross profit/loss, profit factor, maximum drawdown, average holding period, total commissions, and time with capital invested. Optional benchmark return uses an already-stored SQLite ticker and does not change portfolio cash.

Backtest runs persist to existing SQLite tables: `backtest_runs`, `backtest_trades`, and `portfolio_snapshots`. Additional configuration and metrics are stored as sorted JSON in `parameters_json` so the table schema does not need to change.

Run a backtest:

```powershell
python main.py run-backtest --tickers AAPL MSFT KO F INTC --start-date 2024-01-01 --end-date 2026-06-24 --starting-capital 100000 --maximum-positions 5 --position-size-pct 0.20 --slippage-pct 0.001 --commission 0 --maximum-holding-days 60 --ma-window 20 --entry-distance-pct 0.05 --stop-loss-pct 0.10 --benchmark AAPL
```

Run without persistence:

```powershell
python main.py run-backtest --tickers AAPL MSFT --start-date 2024-01-01 --end-date 2026-06-24 --no-persist
```

View a saved backtest:

```powershell
python main.py show-backtest BACKTEST_ID
```

The moving-average reversion strategy remains diagnostic. It is not an investment recommendation. Backtests can be overfit, and historical performance does not guarantee future results. Live trading, paper brokerage integration, options, leverage, short selling, machine learning, and dashboards are not included.
## Setup

From the project root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## Database location

The SQLite database path is configured in `config/settings.py`:

```text
data/algotrad.db
```

Database files under `data/*.db` and related SQLite sidecar files are excluded by `.gitignore`.

## Initialize the database

```powershell
python main.py init-db
```

## Update stored prices

Use the default test universe from `config/settings.py` (`AAPL`, `MSFT`, `KO`, `F`, `INTC`):

```powershell
python main.py update-prices
```

Update selected tickers:

```powershell
python main.py update-prices --tickers AAPL MSFT KO
```

Use a custom inclusive start date:

```powershell
python main.py update-prices --start-date 2024-01-01
```

Use a custom inclusive end date:

```powershell
python main.py update-prices --end-date 2026-06-24
```

Run the initial Phase 2 test universe:

```powershell
python main.py update-prices --tickers AAPL MSFT KO F INTC --start-date 2024-01-01
```

## Show stored prices

These commands read only from SQLite and do not call yfinance:

```powershell
python main.py show-prices AAPL
python main.py show-prices AAPL --start-date 2026-01-01
```

## Database status

```powershell
python main.py db-status
```

This prints the number of securities, total daily price rows, earliest/latest stored dates, and row counts by ticker.

## yfinance date behavior

The CLI accepts `--end-date` as an inclusive date. yfinance commonly treats `end` as exclusive, so the market-data layer adds one calendar day before calling yfinance. This helps include the requested final trading date when data exists. Weekends and market holidays are not treated as missing-data errors.

If `YFINANCE_AUTO_ADJUST` is set so yfinance omits `Adj Close`, the downloader stores `adjusted_close` as the `Close` value and keeps the row valid.

## Data validation

Downloaded rows are validated before insertion. Rows are skipped if required ticker/date/OHLC/volume fields are missing, prices are non-numeric or non-positive, `high < low`, or open/close are outside the low/high range. Zero volume is allowed because some valid series can report zero volume while still carrying valid OHLC prices.

Invalid rows are logged and do not stop the full ticker update.

## Run tests

Tests do not require live internet access because yfinance downloads are mocked:

```powershell
pytest
```

or, from the local virtual environment:

```powershell
.\.venv\Scripts\pytest.exe -q
```

## Troubleshooting

Empty downloads can happen for invalid tickers, unsupported symbols, market holidays, future dates, or temporary Yahoo Finance issues. The update summary reports `no_data` or `failed` per ticker without stopping the full universe update.

If an invalid ticker fails, continue using `db-status` and `show-prices` to inspect any tickers that did update successfully.

All future strategy and backtesting modules should read price history from SQLite through repository functions rather than making their own yfinance calls.






