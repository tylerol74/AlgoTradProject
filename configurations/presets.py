"""Built-in strategy configuration presets."""

from dataclasses import replace
from typing import Dict, List

from configurations.models import CombinedStrategyConfig, GrahamStrategyConfig, SavedStrategyConfig, TechnicalCapitulationConfig, UniverseConfig

_PRESETS: Dict[str, SavedStrategyConfig] = {
    "Moderate Graham": SavedStrategyConfig(
        name="Moderate Graham",
        description="Baseline Graham value thresholds.",
        strategy=GrahamStrategyConfig(),
        universe=UniverseConfig(),
    ),
    "Strict Graham": SavedStrategyConfig(
        name="Strict Graham",
        description="Higher margin, quality, market-cap, and liquidity thresholds.",
        strategy=GrahamStrategyConfig(
            minimum_margin_of_safety=0.40,
            minimum_graham_score=80.0,
            minimum_data_quality_score=75.0,
            minimum_profitable_years=5,
        ),
        universe=UniverseConfig(
            minimum_market_cap=1_000_000_000.0,
            minimum_average_dollar_volume=10_000_000.0,
        ),
    ),
    "Large-Cap Quality Value": SavedStrategyConfig(
        name="Large-Cap Quality Value",
        description="Large-cap Graham screen emphasizing data and earnings quality.",
        strategy=GrahamStrategyConfig(
            minimum_margin_of_safety=0.20,
            minimum_graham_score=80.0,
            minimum_data_quality_score=80.0,
            minimum_profitable_years=5,
        ),
        universe=UniverseConfig(
            minimum_market_cap=10_000_000_000.0,
            minimum_average_dollar_volume=50_000_000.0,
        ),
    ),
}

_COMBINED_PRESETS: Dict[str, CombinedStrategyConfig] = {
    "Graham + Panic - Moderate": CombinedStrategyConfig(),
    "Graham + Panic - Strict": CombinedStrategyConfig(
        graham=GrahamStrategyConfig(
            minimum_margin_of_safety=0.40,
            minimum_graham_score=80.0,
            minimum_data_quality_score=75.0,
        ),
        technical=TechnicalCapitulationConfig(
            minimum_five_day_decline=0.15,
            minimum_ten_day_decline=0.20,
            minimum_relative_volume=2.0,
            maximum_rsi=30.0,
            minimum_panic_score=9.0,
            confirmation_window_days=5,
        ),
    ),
    "Graham + Panic - Broad": CombinedStrategyConfig(
        graham=GrahamStrategyConfig(
            minimum_margin_of_safety=0.20,
            minimum_graham_score=65.0,
            minimum_data_quality_score=60.0,
        ),
        technical=TechnicalCapitulationConfig(
            minimum_five_day_decline=0.07,
            minimum_ten_day_decline=0.10,
            minimum_relative_volume=1.3,
            maximum_rsi=40.0,
            minimum_panic_score=5.0,
            confirmation_window_days=15,
        ),
    ),
}


def list_presets() -> List[str]:
    """Return available preset names in deterministic order."""
    return sorted(_PRESETS)


def get_preset(name: str) -> SavedStrategyConfig:
    """Return a copy of a named preset."""
    try:
        preset = _PRESETS[name]
    except KeyError:
        raise ValueError(f"Unknown strategy preset: {name}")
    return replace(
        preset,
        strategy=replace(preset.strategy),
        universe=replace(preset.universe, tickers=list(preset.universe.tickers)),
        portfolio=replace(preset.portfolio),
        execution=replace(preset.execution),
        backtest=replace(preset.backtest),
    )


def list_combined_presets() -> List[str]:
    """Return available combined-strategy preset names in deterministic order."""
    return sorted(_COMBINED_PRESETS)


def get_combined_preset(name: str) -> CombinedStrategyConfig:
    """Return a copy of a named combined-strategy preset."""
    try:
        preset = _COMBINED_PRESETS[name]
    except KeyError:
        raise ValueError(f"Unknown combined strategy preset: {name}")
    return replace(
        preset,
        graham=replace(preset.graham),
        technical=replace(preset.technical),
    )


def clone_preset(name: str, new_name: str) -> SavedStrategyConfig:
    """Return a renamed copy without mutating global preset definitions."""
    if not new_name or not new_name.strip():
        raise ValueError("new_name cannot be blank")
    return replace(get_preset(name), name=new_name.strip())
