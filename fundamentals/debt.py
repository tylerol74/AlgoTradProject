"""Point-in-time debt aggregation for Graham inputs."""

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class DebtSelection:
    """Selected debt value and transparent aggregation diagnostics."""

    value: Optional[float]
    method: str
    included_components: List[Dict[str, Any]] = field(default_factory=list)
    excluded_components: List[Dict[str, Any]] = field(default_factory=list)
    source_metadata: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    confidence: str = "unavailable"


CURRENT_DEBT_FIELDS = {
    "debt_current",
    "current_portion_of_long_term_debt",
    "long_term_debt_current",
}
NONCURRENT_DEBT_FIELDS = {
    "debt_noncurrent",
    "long_term_debt_noncurrent",
    "long_term_debt",
}
LEASE_FIELDS = {
    "finance_lease_liabilities_current",
    "finance_lease_liabilities_noncurrent",
    "capital_lease_obligations",
}


def _finite_nonnegative(value: Any) -> Optional[float]:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric) or numeric < 0:
        return None
    return numeric


def _availability(row: Dict[str, Any]) -> str:
    return row.get("accepted_at") or row.get("filed_date") or row.get("filing_date") or ""


def _sort_key(row: Dict[str, Any]) -> Tuple[Any, ...]:
    return (
        row.get("period_end") or row.get("report_period") or "",
        _availability(row),
        int(row.get("is_amendment") or 0),
        row.get("accession_number") or "",
        row.get("fact_id") or 0,
    )


def _component(row: Dict[str, Any], role: str) -> Optional[Dict[str, Any]]:
    value = _finite_nonnegative(row.get("value"))
    if value is None:
        return None
    component = dict(row)
    component["value"] = value
    component["component_role"] = role
    return component


def _latest_by_field(rows_by_field: Dict[str, Sequence[Dict[str, Any]]]) -> Tuple[Dict[str, Dict[str, Any]], List[Dict[str, Any]], List[str]]:
    selected: Dict[str, Dict[str, Any]] = {}
    excluded: List[Dict[str, Any]] = []
    warnings: List[str] = []
    for field, rows in rows_by_field.items():
        valid: List[Dict[str, Any]] = []
        for row in rows:
            component = _component(row, field)
            if component is None:
                bad = dict(row)
                bad["component_role"] = field
                bad["exclusion_reason"] = "negative or invalid debt value"
                excluded.append(bad)
                warnings.append(f"{field} fact rejected because debt value is negative or invalid")
            else:
                valid.append(component)
        if not valid:
            continue
        valid.sort(key=_sort_key)
        selected[field] = valid[-1]
        for duplicate in valid[:-1]:
            duplicate = dict(duplicate)
            duplicate["exclusion_reason"] = "older duplicate component"
            excluded.append(duplicate)
    return selected, excluded, sorted(set(warnings))


def _metadata(components: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        {
            "field": row.get("standardized_field") or row.get("component_role"),
            "concept": row.get("concept"),
            "value": row.get("value"),
            "period_end": row.get("period_end") or row.get("report_period"),
            "accepted_at": row.get("accepted_at"),
            "filing_date": row.get("filed_date") or row.get("filing_date"),
            "accession_number": row.get("accession_number"),
            "form_type": row.get("form_type"),
            "is_amendment": bool(row.get("is_amendment")),
        }
        for row in components
    ]


def _sum_components(components: Sequence[Dict[str, Any]]) -> float:
    return sum(float(row["value"]) for row in components)


def select_debt(rows_by_field: Dict[str, Sequence[Dict[str, Any]]]) -> DebtSelection:
    """Select or aggregate debt using deterministic point-in-time components."""
    selected, excluded, warnings = _latest_by_field(rows_by_field)
    lease_components = [selected[field] for field in sorted(LEASE_FIELDS) if field in selected]
    if lease_components:
        warnings.append("lease debt components available; lease inclusion depends on selected debt method")

    direct = selected.get("total_debt")
    if direct is not None:
        method = "direct_total_debt"
        if direct.get("concept") and "Lease" in str(direct.get("concept")):
            method = "direct_total_debt_lease_inclusive"
            warnings.append("direct total debt appears lease-inclusive")
        return DebtSelection(
            direct["value"],
            method,
            [direct],
            excluded + [row for field, row in selected.items() if field != "total_debt"],
            _metadata([direct]),
            sorted(set(warnings)),
            "high",
        )

    current_components = [selected[field] for field in sorted(CURRENT_DEBT_FIELDS) if field in selected]
    noncurrent_components = [selected[field] for field in sorted(NONCURRENT_DEBT_FIELDS) if field in selected]
    current_total = _sum_components(current_components)
    noncurrent_total = _sum_components(noncurrent_components)
    lease_included = bool([row for row in current_components + noncurrent_components if row.get("component_role") in LEASE_FIELDS])
    if current_components and noncurrent_components:
        method = "current_plus_noncurrent_debt"
        if lease_included:
            method = "current_plus_noncurrent_debt_lease_inclusive"
            warnings.append("debt aggregation includes lease liability components")
        components = current_components + noncurrent_components
        return DebtSelection(
            current_total + noncurrent_total,
            method,
            components,
            excluded + lease_components,
            _metadata(components),
            sorted(set(warnings)),
            "high" if not lease_included else "medium",
        )

    short_term_components = [selected[field] for field in ("short_term_borrowings", "commercial_paper", "notes_payable") if field in selected]
    long_term = selected.get("long_term_debt") or selected.get("long_term_debt_noncurrent")
    if short_term_components and long_term is not None:
        components = short_term_components + [long_term]
        return DebtSelection(
            _sum_components(components),
            "short_term_plus_long_term_debt",
            components,
            excluded + lease_components,
            _metadata(components),
            sorted(set(warnings)),
            "medium",
        )

    if long_term is not None:
        warnings.append("long-term-debt-only fallback used")
        return DebtSelection(
            long_term["value"],
            "long_term_debt_only",
            [long_term],
            excluded + lease_components,
            _metadata([long_term]),
            sorted(set(warnings)),
            "low",
        )

    warnings.append("debt unavailable")
    return DebtSelection(None, "unavailable", [], excluded + lease_components, [], sorted(set(warnings)), "unavailable")
