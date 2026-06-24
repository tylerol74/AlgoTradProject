"""Validation helpers for strategy configuration objects."""

from dataclasses import dataclass, replace
from datetime import date
from typing import List, Sequence, Tuple

from data.sec_ticker_map import normalize_ticker
from configurations.models import SavedStrategyConfig, UniverseConfig

SUPPORTED_STRATEGY_TYPES = {"graham_value_v1"}
SUPPORTED_CONFIG_VERSIONS = {1}


@dataclass(frozen=True)
class ValidationError:
    field: str
    message: str


class ConfigurationValidationError(ValueError):
    """Raised when a configuration fails validation."""

    def __init__(self, errors: Sequence[ValidationError]) -> None:
        self.errors = list(errors)
        message = "; ".join(f"{error.field}: {error.message}" for error in self.errors)
        super().__init__(message)


def _between(errors: List[ValidationError], field: str, value: float, minimum: float, maximum: float) -> None:
    if value < minimum or value > maximum:
        errors.append(ValidationError(field, f"must be between {minimum} and {maximum}"))


def _parse_iso_date(value: str, field: str, errors: List[ValidationError]) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        errors.append(ValidationError(field, "must be an ISO date string"))
        return date.min


def normalize_tickers(tickers: Sequence[str]) -> Tuple[List[str], List[ValidationError]]:
    """Normalize tickers and reject duplicates after normalization."""
    errors: List[ValidationError] = []
    normalized: List[str] = []
    seen = set()
    for index, ticker in enumerate(tickers):
        field = f"universe.tickers[{index}]"
        try:
            value = normalize_ticker(ticker)
        except (AttributeError, ValueError):
            errors.append(ValidationError(field, "must be a non-blank ticker"))
            continue
        if value in seen:
            errors.append(ValidationError(field, f"duplicate normalized ticker {value}"))
        seen.add(value)
        normalized.append(value)
    return normalized, errors


def validate_saved_strategy_config(config: SavedStrategyConfig) -> List[ValidationError]:
    """Return field-specific validation errors for a saved configuration."""
    errors: List[ValidationError] = []
    strategy = config.strategy
    universe = config.universe
    portfolio = config.portfolio
    execution = config.execution
    backtest = config.backtest

    if config.config_version not in SUPPORTED_CONFIG_VERSIONS:
        errors.append(ValidationError("config_version", "unsupported configuration version"))
    if not config.name or not config.name.strip():
        errors.append(ValidationError("name", "cannot be blank"))
    if config.strategy_type not in SUPPORTED_STRATEGY_TYPES:
        errors.append(ValidationError("strategy_type", "unsupported strategy type"))

    _between(errors, "strategy.minimum_margin_of_safety", strategy.minimum_margin_of_safety, 0, 1)
    _between(errors, "strategy.minimum_graham_score", strategy.minimum_graham_score, 0, 100)
    _between(errors, "strategy.minimum_data_quality_score", strategy.minimum_data_quality_score, 0, 100)
    if strategy.minimum_profitable_years < 1 or strategy.minimum_profitable_years > 5:
        errors.append(ValidationError("strategy.minimum_profitable_years", "must be between 1 and 5"))

    if universe.minimum_price < 0:
        errors.append(ValidationError("universe.minimum_price", "cannot be negative"))
    if universe.minimum_market_cap < 0:
        errors.append(ValidationError("universe.minimum_market_cap", "cannot be negative"))
    if universe.minimum_average_dollar_volume < 0:
        errors.append(ValidationError("universe.minimum_average_dollar_volume", "cannot be negative"))
    _, ticker_errors = normalize_tickers(universe.tickers)
    errors.extend(ticker_errors)

    if portfolio.starting_capital <= 0:
        errors.append(ValidationError("portfolio.starting_capital", "must be positive"))
    if portfolio.maximum_positions < 1:
        errors.append(ValidationError("portfolio.maximum_positions", "must be at least 1"))
    if portfolio.position_size_pct <= 0 or portfolio.position_size_pct > 1:
        errors.append(ValidationError("portfolio.position_size_pct", "must be greater than 0 and no greater than 1"))

    if execution.slippage_pct < 0 or execution.slippage_pct >= 1:
        errors.append(ValidationError("execution.slippage_pct", "must be at least 0 and less than 1"))
    if execution.commission < 0:
        errors.append(ValidationError("execution.commission", "cannot be negative"))
    if execution.execution_timing != "next_open":
        errors.append(ValidationError("execution.execution_timing", "must equal next_open"))

    if backtest.start_date and backtest.end_date:
        start = _parse_iso_date(backtest.start_date, "backtest.start_date", errors)
        end = _parse_iso_date(backtest.end_date, "backtest.end_date", errors)
        if start != date.min and end != date.min and start >= end:
            errors.append(ValidationError("backtest.start_date", "must be before backtest.end_date"))
    elif backtest.start_date:
        _parse_iso_date(backtest.start_date, "backtest.start_date", errors)
    elif backtest.end_date:
        _parse_iso_date(backtest.end_date, "backtest.end_date", errors)

    return errors


def validated_config(config: SavedStrategyConfig) -> SavedStrategyConfig:
    """Return a config with normalized tickers, or raise field-specific errors."""
    normalized, ticker_errors = normalize_tickers(config.universe.tickers)
    errors = validate_saved_strategy_config(config)
    if errors:
        raise ConfigurationValidationError(errors)
    if ticker_errors:
        raise ConfigurationValidationError(ticker_errors)
    return replace(config, universe=replace(config.universe, tickers=normalized))


def validated_universe(universe: UniverseConfig) -> UniverseConfig:
    normalized, errors = normalize_tickers(universe.tickers)
    if errors:
        raise ConfigurationValidationError(errors)
    return replace(universe, tickers=normalized)
