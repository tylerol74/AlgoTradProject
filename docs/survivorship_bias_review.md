# Survivorship Bias Review

The current universe process is based on stored SEC ticker-map rows and the current `security_universe` table. It supports `first_seen_at`, `last_seen_at`, `delisted_at`, active flags, exchange, security type, and eligibility reasons, but it does not yet reconstruct historical index or exchange membership for a past date.

Implications:

- Historical backtests using `--universe all-eligible` may use a present-day eligible universe unless the caller supplies a historical ticker file.
- Delisted or inactive securities are preserved only when the source data provides or retains them.
- `first_seen_at` and `last_seen_at` are stored but are not yet sufficient to prove historical membership.
- Exchange, security-type, sector, and industry changes are not stored as a time series.
- Present-day classifications can be applied backward if the user does not provide a point-in-time universe.

The system should not describe results as survivorship-bias-free. Validation reports should warn when current active tickers are used for historical tests.
