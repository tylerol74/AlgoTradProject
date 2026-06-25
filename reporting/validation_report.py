"""Validation report serialization."""

import json
from pathlib import Path
from typing import Any, Dict


def validation_report(payload: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(payload)
    payload.setdefault("survivorship_warnings", ["current universe membership may introduce survivorship bias"])
    payload.setdefault("conclusion", "validation is descriptive and does not prove future profitability")
    return payload


def export_validation_report(payload: Dict[str, Any], export_dir: str, filename: str = "validation-report.json") -> Path:
    path = Path(export_dir) / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(validation_report(payload), indent=2, sort_keys=True, default=str), encoding="utf-8")
    return path
