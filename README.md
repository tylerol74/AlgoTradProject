# AlgoTradProject

AlgoTradProject is a modular Python 3.9-compatible foundation for historical backtesting and eventual paper trading. The current phase adds a read-only strategy foundation on top of the centralized SQLite market-data layer. Daily OHLCV data is still downloaded once and stored in SQLite; strategies and indicators consume stored data only.

Live brokerage integration, live trading, options, machine learning, dashboards, and full backtesting are intentionally not included yet.

## Current phase

Phase 3A: read-only strategy-data interface, standardized models, reusable technical indicators, and one diagnostic strategy.

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
        models.py
    strategies/
        base.py
        moving_average_reversion.py
    tests/
        test_database.py
        test_indicators.py
        test_market_data.py
        test_moving_average_reversion.py
        test_repositories.py
        test_strategy_data.py
    main.py
    requirements.txt
    README.md
    .gitignore
```

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




