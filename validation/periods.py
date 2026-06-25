"""Validation period parsing and development/holdout checks."""

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping


@dataclass(frozen=True)
class ValidationPeriod:
    name: str
    start_date: str
    end_date: str
    period_type: str
    description: str = ""


def _period_from_mapping(payload: Mapping[str, Any]) -> ValidationPeriod:
    return ValidationPeriod(
        name=str(payload["name"]),
        start_date=str(payload["start_date"]),
        end_date=str(payload["end_date"]),
        period_type=str(payload.get("type") or payload.get("period_type") or "custom"),
        description=str(payload.get("description") or ""),
    )


def validate_period(period: ValidationPeriod) -> None:
    if period.start_date > period.end_date:
        raise ValueError(f"{period.name}: start_date must be on or before end_date")


def validate_development_holdout(development: ValidationPeriod, holdout: ValidationPeriod) -> None:
    validate_period(development)
    validate_period(holdout)
    if development.end_date >= holdout.start_date:
        raise ValueError("development and holdout periods must not overlap and development must end before holdout starts")


def load_periods(path: str) -> List[ValidationPeriod]:
    file_path = Path(path)
    if file_path.suffix.lower() == ".json":
        payload = json.loads(file_path.read_text(encoding="utf-8-sig"))
        rows = payload["periods"] if isinstance(payload, dict) and "periods" in payload else payload
    else:
        with file_path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
    periods = [_period_from_mapping(row) for row in rows]
    for period in periods:
        validate_period(period)
    return periods


def default_supported_periods(start_date: str, end_date: str) -> List[ValidationPeriod]:
    return [ValidationPeriod("available_range", start_date, end_date, "custom", "Stored data available range")]
