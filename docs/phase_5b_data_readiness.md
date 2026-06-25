# Phase 5B Data Readiness

Phase 5B separates universe size from operational readiness. A security can exist in the raw universe but still be unavailable for Graham, technical, or combined evaluation because local prices, SEC filings, or normalized fields are missing.

## Readiness Categories

- `READY`: resolved eligible security with sufficient stored prices, normalized Graham fields, and technical lookback.
- `PRICE_MISSING`: no stored daily prices on or before the as-of date.
- `PRICE_HISTORY_INSUFFICIENT`: prices exist but do not cover the requested history window.
- `FUNDAMENTALS_MISSING`: no SEC filings are stored for the ticker as of the evaluation date.
- `FUNDAMENTALS_NOT_NORMALIZED`: filings exist but no supported normalized fields are stored.
- `REQUIRED_GRAHAM_FIELDS_MISSING`: normalized fields exist but EPS, shares, equity, current assets/liabilities, or debt/liability support is incomplete.
- `UNSUPPORTED_SECURITY`: resolved security is explicitly excluded, such as preferred stock, warrant, ETF, REIT, financial, OTC, ADR, or unsupported exchange.
- `INELIGIBLE_SECURITY`: resolved security is not eligible for another explicit eligibility reason.
- `UNRESOLVED_TICKER`: requested symbol is not present in `security_universe`.
- `OTHER_EXPLICIT_ERROR`: reserved for future explicit failures that do not fit another category.

## Reconciliation Rule

Every requested unique normalized ticker must be accounted for:

```text
requested = evaluated + explicitly_excluded + explicitly_missing_data + explicitly_invalid_or_unsupported
```

`data-readiness-report` emits `reconciliation.invariant_holds` and `reconciliation.unexplained_count`. `unexplained_count` must be zero before a run can be described as fully accounted for.

## Stage Transitions

Preparation tracks these stages separately:

- `SECURITY_RESOLVED`
- `PRICE_UPDATE_COMPLETE`
- `SEC_INGESTION_COMPLETE`
- `NORMALIZATION_COMPLETE`
- `READINESS_VERIFIED`

Completed stages are skipped on rerun when local readiness already proves completion. Failed stages are retried without deleting successful data for other tickers.

## Resume Behavior

`prepare-universe-data --resume` checks current local state before work starts. It does not assume a ticker is prepared merely because it exists in `securities` or `security_universe`. It attempts only incomplete stages, exports a run summary, and writes a ticker-level failure CSV.

The workflow reuses existing services:

- `data.market_data.update_ticker_prices`
- `fundamentals.service.update_fundamentals_for_ticker`
- existing SQLite repositories and normalized SEC fact tables

It does not download during screening, backtesting, or readiness reporting.

## Exact 100-Ticker Acceptance

Use `outputs/combined_validation/eligible-universe-100.txt`:

```powershell
python main.py data-readiness-report --ticker-file outputs\combined_validation\eligible-universe-100.txt --as-of 2025-06-01 --export-dir outputs\phase5b\pre
python main.py prepare-universe-data --ticker-file outputs\combined_validation\eligible-universe-100.txt --as-of 2025-06-01 --resume --refresh-normalization --export-dir outputs\phase5b\prepare
python main.py data-readiness-report --ticker-file outputs\combined_validation\eligible-universe-100.txt --as-of 2025-06-01 --export-dir outputs\phase5b\post
python main.py universe-coverage-report --ticker-file outputs\combined_validation\eligible-universe-100.txt --as-of 2025-06-01 --export-dir outputs\phase5b
python main.py screen-combined --ticker-file outputs\combined_validation\eligible-universe-100.txt --as-of 2025-06-01 --preset "Graham + Panic - Moderate" --export-dir outputs\phase5b
python main.py run-combined-backtest --ticker-file outputs\combined_validation\eligible-universe-100.txt --start-date 2025-05-01 --end-date 2025-06-01 --no-persist --export-dir outputs\phase5b
python main.py validate-strategy --strategy combined --ticker-file outputs\combined_validation\eligible-universe-100.txt --development-start 2025-01-02 --development-end 2025-03-31 --holdout-start 2025-04-01 --holdout-end 2025-06-01 --export-dir outputs\phase5b
```

Do not claim the 100-ticker validation passed because 100 symbols were loaded. A successful accounting result requires the reconciliation invariant to hold and every non-evaluated ticker to have a visible category.

## Current Acceptance Results

The implementation provides the commands and offline tests. Local acceptance depends on available stored data and external access for missing prices or SEC facts. If `SEC_USER_AGENT` is not configured, preparation reports SEC ingestion failures explicitly instead of silently skipping them.

## Limitations

The workflow cannot repair issuer-specific SEC reporting gaps, unsupported security types, absent yfinance history, or missing historical universe membership. Larger samples should be prepared in bounded batches and interpreted as operational coverage checks, not strategy profitability evidence.
