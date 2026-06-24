import json

import pytest

import main
from database.repositories import get_active_common_stock_tickers, upsert_security
from fundamentals.debt import select_debt
from reporting.graham_report import (
    classify_warning,
    graham_audit_row,
    graham_audit_summary,
    graham_missing_data_plan,
    warning_counts,
)
from strategies.graham_models import GrahamEvaluation, QualificationStatus
from strategies.graham_value import evaluate_graham_candidate
from tests.test_graham_strategy import _inputs


def debt_row(field, value, concept=None, accession="0000000001-24-000001", accepted="2024-05-01T12:00:00", amendment=False):
    return {
        "standardized_field": field,
        "component_role": field,
        "concept": concept or field,
        "value": value,
        "period_end": "2024-03-31",
        "accepted_at": accepted,
        "filed_date": accepted[:10],
        "accession_number": accession,
        "form_type": "10-Q/A" if amendment else "10-Q",
        "is_amendment": amendment,
    }


def test_debt_direct_total_current_noncurrent_and_short_long_methods():
    direct = select_debt({"total_debt": [debt_row("total_debt", 100, "DebtCurrentAndNoncurrent")]})
    assert direct.value == 100
    assert direct.method == "direct_total_debt"
    assert direct.confidence == "high"

    current_plus_noncurrent = select_debt(
        {
            "debt_current": [debt_row("debt_current", 20)],
            "debt_noncurrent": [debt_row("debt_noncurrent", 80)],
        }
    )
    assert current_plus_noncurrent.value == 100
    assert current_plus_noncurrent.method == "current_plus_noncurrent_debt"

    short_plus_long = select_debt(
        {
            "short_term_borrowings": [debt_row("short_term_borrowings", 15, "ShortTermBorrowings")],
            "long_term_debt": [debt_row("long_term_debt", 85, "LongTermDebt")],
        }
    )
    assert short_plus_long.value == 100
    assert short_plus_long.method == "short_term_plus_long_term_debt"


def test_debt_long_term_only_missing_invalid_duplicate_and_lease_behaviors():
    long_only = select_debt({"long_term_debt": [debt_row("long_term_debt", 85)]})
    assert long_only.value == 85
    assert long_only.method == "long_term_debt_only"
    assert "long-term-debt-only fallback used" in long_only.warnings

    missing = select_debt({"long_term_debt": [debt_row("long_term_debt", -1)]})
    assert missing.value is None
    assert missing.method == "unavailable"
    assert any("negative or invalid" in warning for warning in missing.warnings)

    duplicate = select_debt({"total_debt": [debt_row("total_debt", 90, accepted="2024-04-01T12:00:00"), debt_row("total_debt", 100, accepted="2024-05-01T12:00:00", amendment=True)]})
    assert duplicate.value == 100
    assert duplicate.source_metadata[0]["is_amendment"] is True
    assert duplicate.excluded_components[0]["value"] == 90

    lease = select_debt(
        {
            "finance_lease_liabilities_current": [debt_row("finance_lease_liabilities_current", 5, "FinanceLeaseLiabilityCurrent")],
            "long_term_debt": [debt_row("long_term_debt", 95)],
        }
    )
    assert lease.method == "long_term_debt_only"
    assert any("lease debt components available" in warning for warning in lease.warnings)


def test_debt_selection_is_deterministic_and_visible_rows_handle_future_exclusion():
    visible_rows = {"total_debt": [debt_row("total_debt", 100, accepted="2024-05-01T12:00:00")]}
    first = select_debt(visible_rows)
    second = select_debt(visible_rows)

    assert first == second
    assert first.value == 100


@pytest.mark.parametrize(
    "field, concept",
    [
        ("commercial_paper", "CommercialPaper"),
        ("long_term_debt_current", "LongTermDebtAndFinanceLeaseObligationsCurrent"),
        ("long_term_debt_noncurrent", "LongTermDebtAndFinanceLeaseObligationsNoncurrent"),
    ],
)
def test_xom_vz_gm_style_generalized_debt_components(field, concept):
    selected = select_debt({field: [debt_row(field, 25, concept)], "long_term_debt": [debt_row("long_term_debt", 75, "LongTermDebt")]})

    assert selected.value is not None
    assert selected.method in {"short_term_plus_long_term_debt", "current_plus_noncurrent_debt", "long_term_debt_only"}
    assert any(item["concept"] in {concept, "LongTermDebt"} for item in selected.source_metadata)


def test_data_ready_and_strategy_qualified_are_separate():
    expensive = evaluate_graham_candidate(_inputs(market_price=100.0), minimum_graham_score=0)
    row = graham_audit_row(expensive)
    assert row["data_ready"] is True
    assert row["strategy_qualified"] is False
    assert row["primary_data_issue"] == ""
    assert row["primary_disqualification_reason"] == "margin_of_safety_below_minimum"

    missing = evaluate_graham_candidate(_inputs(eps=None))
    missing_row = graham_audit_row(missing)
    assert missing_row["data_ready"] is False
    assert missing_row["primary_data_issue"] == "EPS unavailable"

    qualified = evaluate_graham_candidate(_inputs())
    assert graham_audit_row(qualified)["strategy_qualified"] is True


def test_warning_severity_classification_and_counts():
    assert classify_warning("annual EPS fallback used instead of TTM EPS") == "informational"
    assert classify_warning("preferred equity unavailable") == "caution"
    assert classify_warning("future-data conflict detected") == "critical"
    counts = warning_counts(["annual EPS fallback used", "goodwill unavailable", "future filing warning"])
    assert counts == {"informational": 1, "caution": 1, "critical": 1}


def test_audit_summary_percentages_top_counts_and_missing_data_plan():
    rows = [
        graham_audit_row(evaluate_graham_candidate(_inputs())),
        graham_audit_row(evaluate_graham_candidate(_inputs(eps=None))),
    ]
    summary = graham_audit_summary(rows, ["BAD!"], total_requested=3)
    assert summary["total_requested"] == 3
    assert summary["valid_tickers"] == 2
    assert summary["invalid_tickers"] == 1
    assert summary["price_coverage"]["percentage"] == 100.0
    assert summary["eps_coverage"]["count"] == 1
    assert summary["top_data_issues"]["EPS unavailable"] == 1

    plan = graham_missing_data_plan(rows)
    assert plan[1]["needs_fundamentals"] is True
    assert "EPS" in plan[1]["missing_fields"]


def test_audit_formatting_blank_values_and_json_schema(capsys):
    rows = [graham_audit_row(evaluate_graham_candidate(_inputs(eps=None)))]
    main._print_aligned_table(rows, ["ticker", "data_ready", "margin_of_safety", "primary_data_issue"])
    output = capsys.readouterr().out
    assert "  " in output
    assert "EPS unavailable" in output
    assert "True9" not in output

    payload = main.graham_audit_payload(rows, graham_audit_summary(rows), {"sort_by": None}, "2025-06-01", "tickers")
    assert {"rows", "summary", "configuration", "audit_timestamp", "as_of_date", "universe_source"}.issubset(payload)


def test_audit_filters_sorting_and_descending():
    qualified = graham_audit_row(evaluate_graham_candidate(_inputs(ticker="AAA")))
    failed = graham_audit_row(evaluate_graham_candidate(_inputs(ticker="BBB", eps=None)))
    args = main.parse_args(["audit-graham-data", "--tickers", "AAA", "BBB", "--as-of", "2025-06-01", "--qualified-only"])
    assert [row["ticker"] for row in main._filter_audit_rows([qualified, failed], args)] == ["AAA"]

    args = main.parse_args(["audit-graham-data", "--tickers", "AAA", "BBB", "--as-of", "2025-06-01", "--data-ready-only"])
    assert [row["ticker"] for row in main._filter_audit_rows([qualified, failed], args)] == ["AAA"]

    args = main.parse_args(["audit-graham-data", "--tickers", "AAA", "BBB", "--as-of", "2025-06-01", "--failures-only"])
    assert [row["ticker"] for row in main._filter_audit_rows([qualified, failed], args)] == ["BBB"]

    sorted_rows = main._sort_audit_rows([qualified, failed], "data_quality_score", True)
    assert sorted_rows[0]["data_quality_score"] >= sorted_rows[1]["data_quality_score"]


def test_ticker_file_duplicate_normalization_and_invalid_handling(tmp_path):
    path = tmp_path / "tickers.txt"
    path.write_text("aapl\nAAPL\nBRK-B\nBAD!\n", encoding="utf-8")
    args = main.parse_args(["audit-graham-data", "--ticker-file", str(path), "--as-of", "2025-06-01"])
    tickers, invalid, source, total = main._audit_universe(args)

    assert tickers == ["AAPL", "BRK.B"]
    assert invalid == ["BAD!"]
    assert source == "ticker-file"
    assert total == 4


def test_universe_limit_offset_and_security_type_exclusions(temp_database):
    upsert_security("AAA", security_type="Common Stock", database_path=temp_database)
    upsert_security("BBB", security_type="ETF", database_path=temp_database)
    upsert_security("CCC", security_type="Common Stock", database_path=temp_database)
    upsert_security("DDD", security_type="Preferred Stock", database_path=temp_database)

    assert get_active_common_stock_tickers(database_path=temp_database) == ["AAA", "CCC"]
    assert get_active_common_stock_tickers(limit=1, offset=1, database_path=temp_database) == ["CCC"]


def test_audit_command_verbose_export_and_missing_plan(monkeypatch, tmp_path, capsys):
    evaluation = evaluate_graham_candidate(_inputs())

    class FakeStrategy:
        def evaluate(self, ticker, as_of):
            return GrahamEvaluation(
                ticker,
                as_of,
                evaluation.inputs,
                evaluation.metrics,
                evaluation.eligibility_status,
                evaluation.qualification_status,
                evaluation.classification,
                evaluation.signal_action,
                evaluation.signal_type,
                evaluation.disqualification_reasons,
                evaluation.warnings,
                evaluation.source_metadata,
            )

    monkeypatch.setattr(main, "_graham_strategy", lambda args: FakeStrategy())
    args = main.parse_args(
        [
            "audit-graham-data",
            "--tickers",
            "AAPL",
            "--as-of",
            "2025-06-01",
            "--verbose",
            "--export-dir",
            str(tmp_path),
            "--export-missing-data-plan",
        ]
    )
    main._audit_graham_data_command(args)
    output = capsys.readouterr().out

    assert "price_available" in output
    assert (tmp_path / "graham-data-audit-2025-06-01.json").exists()
    assert (tmp_path / "graham-missing-data-plan-2025-06-01.json").exists()


def test_audit_command_repeated_output_is_deterministic(monkeypatch, capsys):
    evaluation = evaluate_graham_candidate(_inputs())

    class FakeStrategy:
        def evaluate(self, ticker, as_of):
            return evaluation

    monkeypatch.setattr(main, "_graham_strategy", lambda args: FakeStrategy())
    args = main.parse_args(["audit-graham-data", "--tickers", "AAPL", "--as-of", "2025-06-01", "--sort-by", "ticker"])
    main._audit_graham_data_command(args)
    first = capsys.readouterr().out
    main._audit_graham_data_command(args)
    second = capsys.readouterr().out
    assert first == second
