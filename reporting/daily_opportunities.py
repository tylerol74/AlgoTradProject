"""Daily opportunities report exports built from stored strategy evaluations."""

import csv
import json
from pathlib import Path
from typing import Any, Dict, List, Sequence


def _ranked(rows: Sequence[Dict[str, Any]], score_key: str, max_results: int) -> List[Dict[str, Any]]:
    ordered = sorted(
        rows,
        key=lambda row: (
            not bool(row.get("data_ready")),
            not bool(row.get(score_key) is not None),
            -(float(row.get(score_key) or 0.0)),
            row.get("ticker") or "",
        ),
    )
    result = []
    for rank, row in enumerate(ordered[:max_results], start=1):
        copied = dict(row)
        copied["rank"] = rank
        result.append(copied)
    return result


def build_daily_opportunities(
    rows: Sequence[Dict[str, Any]],
    metadata: Dict[str, Dict[str, Any]],
    readiness_rows: Dict[str, Dict[str, Any]],
    as_of: str,
    max_results: int = 10,
) -> Dict[str, Any]:
    """Build capped daily report sections without changing strategy criteria."""
    enriched = []
    for row in rows:
        ticker = row["ticker"]
        meta = metadata.get(ticker, {})
        ready = readiness_rows.get(ticker, {})
        enriched.append(
            {
                "ticker": ticker,
                "company_name": meta.get("company_name"),
                "exchange": meta.get("exchange"),
                "security_type": meta.get("security_type"),
                "latest_stored_price": row.get("price"),
                "price_date": ready.get("latest_price_date"),
                "data_ready": row.get("data_ready"),
                "technical_evaluable": row.get("five_day_return") is not None,
                "graham_evaluable": bool(ready.get("graham_evaluable")),
                "readiness_technical_evaluable": bool(ready.get("technical_evaluable")),
                "combined_evaluable": bool(ready.get("combined_evaluable")),
                "data_freshness_status": ready.get("final_readiness_category") or ("READY" if row.get("data_ready") else "NOT_READY"),
                "graham_score": row.get("graham_score"),
                "graham_qualified": row.get("graham_qualified"),
                "graham_failure_reason": row.get("primary_graham_failure"),
                "technical_score": row.get("panic_score"),
                "technical_qualified": row.get("technical_qualified"),
                "technical_failure_reason": row.get("primary_technical_failure"),
                "combined_score": row.get("combined_score"),
                "combined_qualified": row.get("combined_qualified"),
                "data_quality_or_readiness_warning": row.get("primary_data_issue") or ready.get("graham_evaluability_reason") or "",
                "five_day_return": row.get("five_day_return"),
                "ten_day_return": row.get("ten_day_return"),
                "relative_volume": row.get("relative_volume"),
                "rsi": row.get("rsi"),
                "margin_of_safety": row.get("margin_of_safety"),
            }
        )
    combined_candidates = [row for row in enriched if row.get("combined_qualified")]
    combined_watchlist = [
        row
        for row in enriched
        if row.get("combined_evaluable") and not row.get("combined_qualified") and row.get("combined_score") is not None
    ]
    graham_rows = [
        row for row in enriched if row.get("graham_evaluable") and row.get("graham_score") is not None and not row.get("combined_qualified")
    ]
    technical_rows = [
        row
        for row in enriched
        if row.get("readiness_technical_evaluable") and row.get("technical_score") is not None and not row.get("combined_qualified")
    ]
    sections = {
        "COMBINED CANDIDATES": _ranked(combined_candidates, "combined_score", max_results),
        "COMBINED WATCHLIST": _ranked(combined_watchlist, "combined_score", max_results),
        "GRAHAM WATCHLIST": _ranked(graham_rows, "graham_score", max_results),
        "TECHNICAL WATCHLIST": _ranked(technical_rows, "technical_score", max_results),
    }
    return {
        "as_of": as_of,
        "summary": {
            "combined_qualified_count": len(combined_candidates),
            "combined_watchlist_count": len(combined_watchlist),
            "combined_rows_displayed": len(sections["COMBINED CANDIDATES"]) + len(sections["COMBINED WATCHLIST"]),
            "graham_qualified_count": sum(1 for row in graham_rows if row.get("graham_qualified")),
            "graham_watchlist_count": sum(1 for row in graham_rows if not row.get("graham_qualified")),
            "graham_rows_displayed": len(sections["GRAHAM WATCHLIST"]),
            "technical_qualified_count": sum(1 for row in technical_rows if row.get("technical_qualified")),
            "technical_watchlist_count": sum(1 for row in technical_rows if not row.get("technical_qualified")),
            "technical_rows_displayed": len(sections["TECHNICAL WATCHLIST"]),
        },
        "sections": sections,
    }


def export_daily_opportunities(payload: Dict[str, Any], export_dir: str) -> Dict[str, str]:
    """Export daily opportunities as JSON and narrow CSV."""
    directory = Path(export_dir) / payload["as_of"]
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / "daily-opportunities.json"
    csv_path = directory / "daily-opportunities.csv"
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    fieldnames = [
        "section",
        "rank",
        "ticker",
        "company_name",
        "exchange",
        "security_type",
        "latest_stored_price",
        "price_date",
        "data_ready",
        "technical_evaluable",
        "graham_evaluable",
        "readiness_technical_evaluable",
        "combined_evaluable",
        "data_freshness_status",
        "graham_score",
        "graham_qualified",
        "graham_failure_reason",
        "technical_score",
        "technical_qualified",
        "technical_failure_reason",
        "combined_score",
        "combined_qualified",
        "data_quality_or_readiness_warning",
        "five_day_return",
        "ten_day_return",
        "relative_volume",
        "rsi",
        "margin_of_safety",
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for section, rows in payload["sections"].items():
            for row in rows:
                item = {"section": section, **row}
                writer.writerow({key: item.get(key) for key in fieldnames})
    return {"json": str(json_path), "csv": str(csv_path)}
