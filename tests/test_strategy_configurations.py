import json
from dataclasses import replace

import pytest

from configurations.models import (
    BacktestSettings,
    ExecutionConfig,
    GrahamStrategyConfig,
    PortfolioConfig,
    SavedStrategyConfig,
    UniverseConfig,
)
from configurations.presets import clone_preset, get_preset, list_presets
from configurations.serialization import config_from_json, config_to_json
from configurations.validation import ConfigurationValidationError, validate_saved_strategy_config


def test_configuration_defaults_match_graham_specification():
    config = SavedStrategyConfig(name="Default")

    assert config.strategy == GrahamStrategyConfig(
        minimum_margin_of_safety=0.30,
        minimum_graham_score=70.0,
        minimum_data_quality_score=60.0,
        minimum_profitable_years=4,
        exclude_financials=True,
        exclude_reits=True,
    )
    assert config.universe == UniverseConfig(
        minimum_price=3.00,
        minimum_market_cap=300_000_000.0,
        minimum_average_dollar_volume=2_000_000.0,
        tickers=[],
    )
    assert config.portfolio == PortfolioConfig(100_000.0, 10, 0.10)
    assert config.execution == ExecutionConfig(0.001, 0.0, "next_open")
    assert config.backtest == BacktestSettings(None, None, "AAPL")
    assert config.config_version == 1


def test_configuration_validation_returns_field_specific_errors():
    config = SavedStrategyConfig(
        name=" ",
        strategy_type="unknown",
        strategy=GrahamStrategyConfig(
            minimum_margin_of_safety=1.5,
            minimum_graham_score=-1,
            minimum_data_quality_score=101,
            minimum_profitable_years=6,
        ),
        universe=UniverseConfig(-1, -1, -1, ["aapl", "AAPL"]),
        portfolio=PortfolioConfig(0, 0, 1.5),
        execution=ExecutionConfig(-0.1, -1, "same_day"),
        backtest=BacktestSettings("2025-01-02", "2025-01-01"),
        config_version=999,
    )

    fields = {error.field for error in validate_saved_strategy_config(config)}

    assert "name" in fields
    assert "strategy_type" in fields
    assert "strategy.minimum_margin_of_safety" in fields
    assert "strategy.minimum_graham_score" in fields
    assert "strategy.minimum_data_quality_score" in fields
    assert "strategy.minimum_profitable_years" in fields
    assert "universe.minimum_price" in fields
    assert "universe.minimum_market_cap" in fields
    assert "universe.minimum_average_dollar_volume" in fields
    assert "universe.tickers[1]" in fields
    assert "portfolio.starting_capital" in fields
    assert "portfolio.maximum_positions" in fields
    assert "portfolio.position_size_pct" in fields
    assert "execution.slippage_pct" in fields
    assert "execution.commission" in fields
    assert "execution.execution_timing" in fields
    assert "backtest.start_date" in fields
    assert "config_version" in fields


def test_configuration_json_round_trip_is_deterministic_and_normalizes_tickers():
    config = SavedStrategyConfig(
        name="Round Trip",
        universe=UniverseConfig(tickers=["aapl", "brk-b"]),
        backtest=BacktestSettings("2025-01-01", "2025-12-31", "SPY"),
    )

    text = config_to_json(config)
    loaded = config_from_json(text)

    assert loaded.universe.tickers == ["AAPL", "BRK.B"]
    assert config_to_json(loaded) == text


def test_unknown_fields_and_unsupported_versions_are_rejected():
    payload = json.loads(config_to_json(SavedStrategyConfig(name="Valid")))
    payload["unknown"] = True

    with pytest.raises(ConfigurationValidationError) as unknown:
        config_from_json(json.dumps(payload))
    assert unknown.value.errors[0].field == "unknown"

    payload = json.loads(config_to_json(SavedStrategyConfig(name="Valid")))
    payload["config_version"] = 999
    with pytest.raises(ConfigurationValidationError) as unsupported:
        config_from_json(json.dumps(payload))
    assert unsupported.value.errors[0].field == "config_version"

    with pytest.raises(ConfigurationValidationError) as missing_name:
        config_from_json("{}")
    assert missing_name.value.errors[0].field == "name"


def test_presets_match_specification_and_cloning_does_not_mutate_original():
    assert list_presets() == ["Large-Cap Quality Value", "Moderate Graham", "Strict Graham"]

    moderate = get_preset("Moderate Graham")
    strict = get_preset("Strict Graham")
    large = get_preset("Large-Cap Quality Value")

    assert moderate.strategy.minimum_margin_of_safety == 0.30
    assert moderate.universe.minimum_market_cap == 300_000_000.0
    assert strict.strategy.minimum_margin_of_safety == 0.40
    assert strict.strategy.minimum_graham_score == 80.0
    assert strict.universe.minimum_average_dollar_volume == 10_000_000.0
    assert large.strategy.minimum_margin_of_safety == 0.20
    assert large.strategy.minimum_data_quality_score == 80.0
    assert large.universe.minimum_market_cap == 10_000_000_000.0

    cloned = clone_preset("Moderate Graham", "My Graham")
    cloned = replace(cloned, universe=replace(cloned.universe, tickers=["AAPL"]))

    assert cloned.name == "My Graham"
    assert get_preset("Moderate Graham").universe.tickers == []


def test_invalid_execution_timing_rejected():
    config = SavedStrategyConfig(name="Bad", execution=ExecutionConfig(execution_timing="close"))

    errors = validate_saved_strategy_config(config)

    assert [error.field for error in errors] == ["execution.execution_timing"]
