"""Deterministic JSON serialization for strategy configurations."""

import json
from dataclasses import asdict, fields, is_dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Type, TypeVar

from configurations.models import (
    BacktestSettings,
    ExecutionConfig,
    GrahamStrategyConfig,
    PortfolioConfig,
    SavedStrategyConfig,
    UniverseConfig,
)
from configurations.validation import ConfigurationValidationError, ValidationError, validated_config

T = TypeVar("T")

_MODEL_FIELDS = {
    "strategy": GrahamStrategyConfig,
    "universe": UniverseConfig,
    "portfolio": PortfolioConfig,
    "execution": ExecutionConfig,
    "backtest": BacktestSettings,
}


def _construct(model: Type[T], payload: Mapping[str, Any], prefix: str) -> T:
    allowed = {field.name for field in fields(model)}
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ConfigurationValidationError([ValidationError(f"{prefix}.{name}" if prefix else name, "unknown field") for name in unknown])
    return model(**dict(payload))  # type: ignore[arg-type]


def config_to_json(config: SavedStrategyConfig) -> str:
    """Serialize a validated configuration to stable JSON."""
    validated = validated_config(config)
    return json.dumps(asdict(validated), indent=2, sort_keys=True)


def config_from_dict(payload: Mapping[str, Any]) -> SavedStrategyConfig:
    """Deserialize a configuration mapping, rejecting unknown fields."""
    allowed = {field.name for field in fields(SavedStrategyConfig)}
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ConfigurationValidationError([ValidationError(name, "unknown field") for name in unknown])
    if "name" not in payload:
        raise ConfigurationValidationError([ValidationError("name", "is required")])
    values: Dict[str, Any] = dict(payload)
    for key, model in _MODEL_FIELDS.items():
        if key in values:
            if not isinstance(values[key], Mapping):
                raise ConfigurationValidationError([ValidationError(key, "must be an object")])
            values[key] = _construct(model, values[key], key)
    config = SavedStrategyConfig(**values)
    return validated_config(config)


def config_from_json(text: str) -> SavedStrategyConfig:
    """Deserialize and validate configuration JSON."""
    payload = json.loads(text)
    if not isinstance(payload, Mapping):
        raise ConfigurationValidationError([ValidationError("config", "must be a JSON object")])
    return config_from_dict(payload)


def load_config(path: str) -> SavedStrategyConfig:
    return config_from_json(Path(path).read_text(encoding="utf-8"))


def save_config(config: SavedStrategyConfig, path: str) -> None:
    Path(path).write_text(config_to_json(config), encoding="utf-8")
