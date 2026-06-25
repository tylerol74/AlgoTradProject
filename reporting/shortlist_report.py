"""Shortlist report serialization."""

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List


def shortlist_report(rows: List[Any], summary: Dict[str, Any]) -> Dict[str, Any]:
    return {"rows": [asdict(row) if hasattr(row, "__dataclass_fields__") else row for row in rows], "summary": summary}


def export_shortlist_report(rows: List[Any], summary: Dict[str, Any], export_dir: str, filename: str = "shortlist-report.json") -> Path:
    path = Path(export_dir) / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(shortlist_report(rows, summary), indent=2, sort_keys=True, default=str), encoding="utf-8")
    return path
