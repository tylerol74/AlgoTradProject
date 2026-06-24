# AlgoTradProject

AlgoTradProject is a modular Python 3.9-compatible foundation for historical backtesting and eventual paper trading. This first phase intentionally includes only the SQLite database layer.

## Project layout

```text
AlgoTradProject/
    config/
    data/
    database/
    strategies/
    backtesting/
    portfolio/
    reporting/
    tests/
    main.py
    requirements.txt
    README.md
    .gitignore
```

## Current scope

Included:

- SQLite connection management with foreign keys enabled
- Idempotent schema initialization
- Tables for securities, prices, fundamentals, signals, backtest runs, trades, and portfolio snapshots
- Indexes for common ticker and date lookups
- Pytest coverage for initialization, inserts, duplicates/upserts, and foreign key enforcement

Not included yet:

- Market data downloader
- Trading strategies
- Backtesting engine
- Dashboard or reporting UI
- Live brokerage integration

## Setup

From the project root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## Initialize the database

```powershell
python main.py init-db
```

The database path is configured in `config/settings.py`. By default, it creates:

```text
data/algotrad.db
```

## Run tests

```powershell
pytest
```
