# AlgoTradProject

AlgoTradProject is a modular Python 3.9-compatible foundation for historical backtesting and eventual paper trading. The current phase adds a point-in-time SEC fundamentals data layer on top of the centralized SQLite market-data, strategy, backtesting, and reporting foundations. Daily OHLCV behavior is unchanged.

Live brokerage integration, live trading, options, machine learning, dashboards, and full backtesting are intentionally not included yet.

## Current phase

Phase 4A: point-in-time historical fundamentals storage for future Graham-style backtesting.

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
- Saved-backtest review with ticker attribution, exit-reason analysis, time-period returns, and equity/drawdown series
- Multi-run comparison with comparability warnings
- Explicit development/validation experiment runner without automatic optimization
- CSV and JSON report exports
- SEC EDGAR company facts and submissions ingestion
- Local ticker-to-CIK mapping cache
- Point-in-time fundamentals queries that filter on `accepted_at <= as_of_date`
- Preservation of original and amended filings
- Standardized fundamental fields with source metadata

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

## Phase 4A fundamentals

Phase 4A stores SEC EDGAR filing metadata and standardized XBRL facts for U.S. public companies. It is designed for future Graham-style research, but no Graham strategy or fundamental-based execution is implemented yet.

Primary source:

- SEC company submissions metadata
- SEC company facts JSON
- SEC company ticker mapping

SEC requests require a User-Agent that identifies the application and contact. Configure it locally without committing secrets:

```powershell
$env:SEC_USER_AGENT = "AlgoTradProject/0.1 your-email@example.com"
```

The code intentionally fails fast if `SEC_USER_AGENT` is missing. Request pacing and retry settings are configured in `config/settings.py` through environment variables such as `SEC_REQUEST_DELAY_SECONDS`.

Point-in-time rule:

```python
get_fundamentals_as_of("AAPL", "2024-06-01")
```

returns only facts from filings publicly available on or before `2024-06-01`. The query filters by `accepted_at <= as_of_date`. If `accepted_at` is missing, it falls back to `filing_date` and marks `accepted_at_fallback_used` in the returned metadata.

Filing date, accepted timestamp, and report period are different:

- `report_period` is the fiscal period covered by the filing.
- `filing_date` is the SEC filing date.
- `accepted_at` is the timestamp used for point-in-time availability when present.

Amendments and restatements are preserved. A 10-K/A does not overwrite the original 10-K. Queries before the amendment acceptance date can still return the original filing; queries after the amendment may return amended facts when they supersede the relevant period.

Supported forms initially:

- `10-K`
- `10-K/A`
- `10-Q`
- `10-Q/A`

Unsupported forms such as 8-K, registration statements, foreign issuer forms, and proxy statements are excluded for now.

Standardized fields include revenue, gross profit, operating income, net income, diluted EPS, basic EPS, interest expense, total assets, total liabilities, current assets, current liabilities, cash and equivalents, inventory, accounts receivable, long-term debt, total debt, shareholders equity, retained earnings, operating cash flow, capital expenditures, dividends paid, weighted-average shares, and shares outstanding.

Historical fundamentals are complex. SEC company facts can contain duplicate concepts, amendments, restatements, instant facts, annual facts, quarter facts, and year-to-date facts. Phase 4A stores period start/end and classifies periods, but it does not derive standalone quarterly values from year-to-date facts.

Update fundamentals:

```powershell
python main.py update-fundamentals --tickers AAPL MSFT KO F INTC --years 5
```

Show fundamentals status:

```powershell
python main.py fundamentals-status
```

Show filings:

```powershell
python main.py show-filings AAPL --forms 10-K 10-Q
```

Query point-in-time fundamentals:

```powershell
python main.py show-fundamentals AAPL --as-of 2025-06-01
```

Phase 4A does not add brokerage integration, live trading, paper trading, options, leverage, machine learning, dashboards, or a Graham strategy. It also does not use yfinance as the authoritative historical fundamentals source; yfinance fundamentals are not point-in-time reliable enough for this layer.
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

## Phase 4B Graham Value Backend

Phase 4B adds a standalone, point-in-time Graham Value Baseline backend. It evaluates common-stock candidates using stored historical prices and stored SEC fundamentals only; it does not call SEC EDGAR or yfinance during evaluation, screening, or Graham backtesting.

The primary Graham Number formula is:

```text
Graham Number = sqrt(22.5 * EPS * book value per share)
```

EPS selection is point-in-time and uses this hierarchy:

1. trailing-twelve-month diluted EPS when it can be constructed safely
2. latest annual diluted EPS
3. trailing-twelve-month basic EPS when it can be constructed safely
4. latest annual basic EPS

Safe TTM EPS is used only when exactly four completed, non-overlapping, non-duplicate quarterly periods are visible as of the evaluation date. The periods must have valid start/end dates, consistent EPS concept and unit, consistent diluted or basic method, compatible fiscal-year and fiscal-period metadata, filing acceptance on or before the evaluation date, source accession numbers, and no annual or YTD value silently mixed in as a quarter. Fiscal quarters are matched from SEC fiscal-year and fiscal-period metadata where available, so non-calendar fiscal years are supported and calendar month labels are not the authority.

Standalone quarters may be derived from year-to-date facts only when the math is provable:

- Q2 standalone = six-month YTD minus Q1
- Q3 standalone = nine-month YTD minus six-month YTD
- Q4 standalone = annual EPS minus nine-month YTD

Those derivations require exact period alignment, consistent concept and units, matching fiscal-year boundaries, no conflicting selected amended fact, and all source filings accepted by the evaluation date. Unsafe TTM construction is rejected. When TTM is rejected, the strategy falls back to the latest reliable annual EPS in the documented hierarchy and preserves the rejected TTM reasons, source periods, accepted timestamps, forms, accession numbers, amendment flags, direct versus derived period counts, and warnings.

Amendment handling is availability-aware: original filings remain selected before an amendment acceptance timestamp, and amended facts become selectable only after the amendment is public. Amendments are not applied retroactively.

Historical shares are selected without present-day leakage:

1. latest valid instant shares-outstanding fact available as of the evaluation date
2. weighted-average diluted shares matching the selected EPS period
3. weighted-average basic shares matching the selected EPS period
4. no share value, with an explicit warning

Zero, negative, NaN, and infinite share counts are rejected. Weighted-average substitutions are warned, filing metadata is retained, and large instant-versus-weighted-average inconsistencies are surfaced as possible split-adjustment warnings. The code does not silently repair split inconsistencies.

Book value per share is common shareholders' equity divided by point-in-time shares. Preferred equity is subtracted when available. Tangible book value subtracts goodwill and intangible assets only when those values are explicitly known.

Point-in-time rules are mandatory:

- use facts from filings accepted on or before the evaluation date
- use filing date only as a documented fallback when `accepted_at` is missing
- preserve amendment timing
- exclude future filings and current-share leakage
- never mutate stored price or fundamental records during evaluation

## Graham Data Quality and Scoring

Data-quality diagnostics are visible penalties, not a hidden number. Each penalty includes a code, point deduction, affected field, explanation, and source metadata where relevant. The score is `100 - visible penalties`, bounded to 0-100. Penalties cover accepted timestamp gaps, filing-date fallback, annual/basic EPS fallback, share fallback or missing shares, missing preferred equity, missing current assets or liabilities, missing debt, missing goodwill or intangibles, incomplete five-year earnings history, and possible future-data issues.

The transparent Graham score is 0 to 100:

- valuation: 40 points
- financial strength: 25 points
- earnings quality: 20 points
- tradability: 10 points
- data quality: 5 points

Data-quality scores are also 0 to 100:

- 90-100: High confidence
- 75-89: Good
- 60-74: Usable with warnings
- 40-59: Low confidence
- below 40: Insufficient

Hard disqualifications are returned individually. They include price below the configured minimum, insufficient average dollar volume, insufficient market cap, missing usable filing data, non-positive EPS, non-positive book value per share, non-positive common equity, two consecutive annual losses, low data quality, low margin of safety, low Graham score, insufficient profitable years, excluded financial companies, excluded REITs, unsupported security types, and evidence of future-data leakage.

Graham Number is a screening estimate, not guaranteed intrinsic value. The strategy is intentionally conservative and should not be optimized blindly to past results.

## Graham Configuration

Reusable configuration models live in `configurations/`. They use typed dataclasses and deterministic JSON serialization so a future UI can control strategy criteria without hard-coded thresholds.

Initial configurable fields:

- Strategy: margin of safety, Graham score, data-quality score, profitable years, financial exclusion, REIT exclusion
- Universe: minimum price, market cap, average dollar volume, tickers
- Portfolio: starting capital, maximum positions, position size
- Execution: slippage, commission, execution timing
- Backtest: start date, end date, benchmark

Example JSON:

```json
{
  "backtest": {
    "benchmark": "AAPL",
    "end_date": "2025-12-31",
    "start_date": "2025-01-01"
  },
  "config_version": 1,
  "description": "Baseline Graham screen",
  "execution": {
    "commission": 0.0,
    "execution_timing": "next_open",
    "slippage_pct": 0.001
  },
  "name": "Moderate Graham",
  "portfolio": {
    "maximum_positions": 10,
    "position_size_pct": 0.1,
    "starting_capital": 100000.0
  },
  "strategy": {
    "exclude_financials": true,
    "exclude_reits": true,
    "minimum_data_quality_score": 60.0,
    "minimum_graham_score": 70.0,
    "minimum_margin_of_safety": 0.3,
    "minimum_profitable_years": 4
  },
  "strategy_type": "graham_value_v1",
  "universe": {
    "minimum_average_dollar_volume": 2000000.0,
    "minimum_market_cap": 300000000.0,
    "minimum_price": 3.0,
    "tickers": ["AAPL", "MSFT"]
  }
}
```

Validation rejects unknown fields, duplicate normalized tickers, unsupported versions, unsupported strategy types, invalid execution timing, invalid ranges, and invalid date ordering. Missing optional nested fields use documented defaults.

Built-in presets:

- Moderate Graham
- Strict Graham
- Large-Cap Quality Value

## Graham CLI

Evaluate one ticker:

```powershell
python main.py evaluate-graham AAPL --as-of 2025-06-01
```

Screen a ticker list:

```powershell
python main.py screen-graham --tickers AAPL MSFT KO F INTC --as-of 2025-06-01
```

Audit stored Graham data coverage without downloading or mutating price/fundamental tables:

```powershell
python main.py audit-graham-data --tickers AAPL MSFT KO F INTC --as-of 2025-06-01
python main.py audit-graham-data --tickers AAPL MSFT KO F INTC --as-of 2025-06-01 --json
python main.py audit-graham-data --tickers AAPL MSFT KO F INTC --as-of 2025-06-01 --export-dir .\reports
```

The audit output includes one row per ticker with price, EPS, shares, equity, current assets, current liabilities, debt availability, EPS and share methods, five-year earnings-history count, data-quality score, Graham-ready status, primary missing reason, and warning count. It clearly distinguishes missing data from failed eligibility rules.

Run a standalone Graham backtest using next-open execution:

```powershell
python main.py run-graham-backtest --tickers AAPL MSFT KO F INTC --start-date 2024-01-01 --end-date 2025-06-01
```

Configurable CLI thresholds include:

```powershell
--minimum-margin-of-safety 0.30
--minimum-graham-score 70
--minimum-data-quality-score 60
--minimum-profitable-years 4
--minimum-price 3
--minimum-market-cap 300000000
--minimum-average-dollar-volume 2000000
--exclude-financials
--exclude-reits
```

Preset and configuration commands:

```powershell
python main.py list-strategy-presets
python main.py show-strategy-preset "Moderate Graham"
python main.py export-strategy-preset "Moderate Graham" --output moderate-graham.json
python main.py validate-strategy-config moderate-graham.json
```

## Graham Limitations

Version 1 excludes financial companies and REITs by default. It does not support brokerage integration, Streamlit, paper trading, options, short selling, leverage, machine learning, automated parameter optimization, intraday trading, financial-sector valuation, REIT valuation, warrants, preferred-share valuation, or Graham plus technical combined strategies.

SEC company facts vary by issuer. Some companies report unusual concepts, restatements, fiscal calendars, 52/53-week years, missing preferred equity, incomplete goodwill or intangible detail, or YTD-only interim facts. The Graham backend rejects unsafe TTM EPS construction instead of guessing, and it reports warnings or data-quality penalties when issuer facts are incomplete or ambiguous.

The future UI should use the backend configuration models rather than duplicating Graham thresholds.







