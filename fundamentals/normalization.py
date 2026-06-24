"""Validation and period normalization for fundamentals."""

import math
import re
from datetime import date, datetime
from typing import Any, Optional

from fundamentals.concepts import SUPPORTED_FORMS

ACCESSION_RE = re.compile(r"^\d{10}-\d{2}-\d{6}$")


def parse_date(value: Optional[str]) -> Optional[date]:
    """Parse an ISO date or timestamp into a date."""
    if not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"invalid date: {value}") from exc


def validate_iso_date(value: Optional[str], field_name: str) -> Optional[str]:
    """Validate a date-like string and return it unchanged."""
    if value is None:
        return None
    parse_date(value)
    return value


def validate_accession_number(accession_number: str) -> str:
    """Validate a SEC accession number."""
    if not accession_number or not ACCESSION_RE.match(accession_number):
        raise ValueError(f"invalid accession number: {accession_number}")
    return accession_number


def validate_supported_form(form_type: str) -> str:
    """Validate an initially supported SEC form."""
    if form_type not in SUPPORTED_FORMS:
        raise ValueError(f"unsupported form type: {form_type}")
    return form_type


def validate_numeric(value: Any) -> float:
    """Validate a finite numeric fact value."""
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError("fundamental value must be finite")
    return numeric


def classify_period(period_start: Optional[str], period_end: Optional[str]) -> str:
    """Classify fact periods without deriving missing quarterly values."""
    end = parse_date(period_end)
    start = parse_date(period_start)
    if end is None:
        return "unknown"
    if start is None:
        return "instant"
    if start > end:
        raise ValueError("period_start cannot be after period_end")
    days = (end - start).days + 1
    if 75 <= days <= 105:
        return "quarter"
    if 250 <= days <= 380:
        return "annual"
    if 110 <= days < 250:
        return "year_to_date"
    return "unknown"


def normalize_accession(accession_number: str) -> str:
    """Normalize accession numbers found with or without dashes."""
    text = str(accession_number).strip()
    if "-" in text:
        return text
    if len(text) == 18 and text.isdigit():
        return f"{text[:10]}-{text[10:12]}-{text[12:]}"
    return text
