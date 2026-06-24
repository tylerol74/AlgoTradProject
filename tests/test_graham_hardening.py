import json

import pytest

import main
from database.connection import get_connection
from database.schema import initialize_database
from fundamentals.earnings import select_eps
from fundamentals.point_in_time import select_historical_shares
from fundamentals.quality import data_quality_score, graham_quality_score
from reporting.graham_report import graham_audit_row, graham_summary_row
from strategies.graham_models import (
    DataQualityClass,
    EPSMethod,
    GrahamClassification,
    GrahamEvaluation,
    GrahamInputs,
    GrahamMetrics,
    GrahamSignalType,
    QualificationStatus,
)
from strategies.graham_value import evaluate_graham_candidate
from tests.test_graham_strategy import _inputs


def eps_row(
    value,
    start,
    end,
    fp,
    fy=2024,
    concept="EarningsPerShareDiluted",
    accession="0000000001-24-000001",
    accepted="2024-05-01T12:00:00",
    form="10-Q",
    unit="USD/shares",
    amendment=False,
):
    return {
        "value": value,
        "period_start": start,
        "period_end": end,
        "fiscal_period": fp,
        "fiscal_year": fy,
        "concept": concept,
        "unit": unit,
        "accepted_at": accepted,
        "filed_date": accepted[:10],
        "accession_number": accession,
        "form_type": form,
        "is_amendment": amendment,
    }


def basic(row):
    copied = dict(row)
    copied["concept"] = "EarningsPerShareBasic"
    return copied


def four_quarters():
    return [
        eps_row(1.0, "2023-10-01", "2023-12-31", "Q1", fy=2024),
        eps_row(1.1, "2024-01-01", "2024-03-31", "Q2", fy=2024),
        eps_row(1.2, "2024-04-01", "2024-06-30", "Q3", fy=2024),
        eps_row(1.3, "2024-07-01", "2024-09-30", "Q4", fy=2024),
    ]


def test_eps_annual_fallbacks_and_preference_order():
    diluted = [eps_row(4.0, "2024-01-01", "2024-12-31", "FY", form="10-K")]
    basic_rows = [basic(eps_row(5.0, "2024-01-01", "2024-12-31", "FY", form="10-K"))]

    selected = select_eps(diluted, basic_rows)
    assert selected.method == EPSMethod.ANNUAL_DILUTED
    assert selected.value == 4.0
    assert "annual EPS fallback used instead of TTM EPS" in selected.warnings

    selected_basic = select_eps([], basic_rows)
    assert selected_basic.method == EPSMethod.ANNUAL_BASIC
    assert selected_basic.value == 5.0
    assert "basic EPS fallback used" in selected_basic.warnings


def test_four_direct_quarters_select_ttm_and_non_calendar_fiscal_year():
    selected = select_eps(four_quarters(), [])

    assert selected.method == EPSMethod.TTM_DILUTED
    assert selected.value == pytest.approx(4.6)
    assert selected.source_periods == ["2023-12-31", "2024-03-31", "2024-06-30", "2024-09-30"]
    assert len(selected.direct_periods) == 4
    assert not selected.derived_periods


def test_ytd_derivations_for_q2_q3_and_q4_are_used_only_when_aligned():
    rows = [
        eps_row(1.0, "2024-01-01", "2024-03-31", "Q1"),
        eps_row(2.3, "2024-01-01", "2024-06-30", "Q2"),
        eps_row(3.9, "2024-01-01", "2024-09-30", "Q3"),
        eps_row(5.8, "2024-01-01", "2024-12-31", "FY", form="10-K"),
    ]

    selected = select_eps(rows, [])

    assert selected.method == EPSMethod.TTM_DILUTED
    assert selected.value == pytest.approx(5.8)
    assert [row["derivation"] for row in selected.derived_periods] == [
        "Q2 standalone derived from six-month YTD minus Q1",
        "Q3 standalone derived from nine-month YTD minus six-month YTD",
        "Q4 standalone derived from annual EPS minus nine-month YTD",
    ]


@pytest.mark.parametrize(
    "rows, expected_reason",
    [
        (four_quarters()[:3], "fewer than four standalone quarters"),
        ([four_quarters()[0], four_quarters()[0], four_quarters()[1], four_quarters()[2], four_quarters()[3]], "duplicate EPS period"),
        ([eps_row(1, "2024-01-01", "2024-03-31", "Q1"), eps_row(1, "2024-03-29", "2024-06-30", "Q2"), eps_row(1, "2024-07-01", "2024-09-30", "Q3"), eps_row(1, "2024-10-01", "2024-12-31", "Q4")], "overlap"),
        ([eps_row(1, "2024-01-01", "2024-06-30", "Q2")], "fewer than four standalone quarters"),
        ([eps_row(1, "2024-01-01", "2024-03-31", "Q1"), eps_row(2, "2024-01-02", "2024-06-30", "Q2")], "fewer than four standalone quarters"),
    ],
)
def test_unsafe_ttm_scenarios_are_rejected(rows, expected_reason):
    selected = select_eps(rows, [])

    assert selected.method in (EPSMethod.ANNUAL_DILUTED, EPSMethod.UNAVAILABLE)
    assert any(expected_reason in reason for reason in selected.rejection_reasons + selected.warnings)


def test_mixed_concepts_units_and_fiscal_gaps_reject_ttm():
    rows = four_quarters()
    rows[1] = basic(rows[1])
    selected = select_eps(rows, [])
    assert any("EPS concepts differ" in reason for reason in selected.rejection_reasons)

    rows = four_quarters()
    rows[2]["unit"] = "USD"
    selected = select_eps(rows, [])
    assert any("EPS units differ" in reason for reason in selected.rejection_reasons)

    rows = four_quarters()
    rows[2]["fiscal_year"] = 2026
    selected = select_eps(rows, [])
    assert any("fiscal quarters are not consecutive" in reason for reason in selected.rejection_reasons)


def test_future_filing_exclusion_and_amendment_visibility_are_driven_by_visible_rows():
    original = eps_row(1.0, "2024-01-01", "2024-12-31", "FY", accession="0000000001-24-000001", form="10-K")
    amendment = eps_row(2.0, "2024-01-01", "2024-12-31", "FY", accession="0000000001-24-000002", accepted="2024-07-01T12:00:00", form="10-K/A", amendment=True)

    before = select_eps([original], [])
    after = select_eps([original, amendment], [])

    assert before.value == 1.0
    assert before.accession_numbers == ["0000000001-24-000001"]
    assert after.value == 2.0
    assert after.accession_numbers == ["0000000001-24-000002"]
    assert after.source_filings[0]["is_amendment"] is True


def share_row(value, start=None, end="2024-06-30", field="shares_outstanding", fp="Q2", accepted="2024-07-15T12:00:00", amendment=False):
    return {
        "value": value,
        "period_start": start,
        "period_end": end,
        "report_period": end,
        "standardized_field": field,
        "accepted_at": accepted,
        "filed_date": accepted[:10],
        "accession_number": "0000000001-24-000010",
        "form_type": "10-Q/A" if amendment else "10-Q",
        "is_amendment": amendment,
        "fiscal_period": fp,
        "fiscal_year": 2024,
    }


def test_historical_share_hierarchy_and_invalid_rejections():
    eps_selection = select_eps([eps_row(1.0, "2024-01-01", "2024-12-31", "FY", form="10-K")], [])
    instant = [share_row(100)]
    diluted = [share_row(90, "2024-01-01", end="2024-12-31", fp="FY", field="weighted_average_diluted_shares")]
    basic_rows = [share_row(80, "2024-01-01", end="2024-12-31", fp="FY", field="weighted_average_basic_shares")]

    selected = select_historical_shares(instant, diluted, basic_rows, eps_selection)
    assert selected.method == "shares_outstanding"
    assert selected.value == 100

    selected = select_historical_shares([], diluted, basic_rows, eps_selection)
    assert selected.method == "weighted_average_diluted_shares"
    assert "shares fallback used: weighted_average_diluted_shares" in selected.warnings

    selected = select_historical_shares([], [], basic_rows, eps_selection)
    assert selected.method == "weighted_average_basic_shares"

    selected = select_historical_shares([share_row(0), share_row(float("inf"))], [], [], eps_selection)
    assert selected.value is None
    assert any("not positive and finite" in warning for warning in selected.warnings)


def test_current_and_future_shares_do_not_leak_and_amendment_after_acceptance_wins():
    eps_selection = select_eps([eps_row(1.0, "2024-01-01", "2024-12-31", "FY", form="10-K")], [])
    original = share_row(100, accepted="2024-03-01T12:00:00")
    amendment = share_row(110, accepted="2024-04-01T12:00:00", amendment=True)
    current = share_row(999, end="2026-06-24", accepted="2026-06-24T12:00:00")

    before = select_historical_shares([original], [], [], eps_selection)
    after = select_historical_shares([original, amendment], [], [], eps_selection)
    historical = select_historical_shares([original], [], [], eps_selection)

    assert before.value == 100
    assert after.value == 110
    assert after.source["is_amendment"] is True
    assert historical.value != current["value"]


def test_split_inconsistency_warning():
    eps_selection = select_eps([eps_row(1.0, "2024-01-01", "2024-12-31", "FY", form="10-K")], [])
    selected = select_historical_shares(
        [share_row(1000)],
        [share_row(100, "2024-01-01", field="weighted_average_diluted_shares")],
        [],
        eps_selection,
    )
    assert any("split-adjustment inconsistency" in warning for warning in selected.warnings)


def test_data_quality_penalties_are_structured_and_arithmetic_is_visible():
    inputs = _inputs(
        eps_method=EPSMethod.ANNUAL_BASIC,
        shares_outstanding=None,
        preferred_equity=None,
        current_assets=None,
        current_liabilities=None,
        long_term_debt=None,
        total_debt=None,
        goodwill=None,
        intangible_assets=None,
        warnings=["incomplete five-year earnings history", "future filing warning"],
    )

    result = data_quality_score(inputs)

    assert result["score"] == max(0.0, 100.0 - sum(item["points"] for item in result["penalties"]))
    assert all({"code", "points", "field", "explanation", "source"}.issubset(item) for item in result["penalties"])
    codes = {item["code"] for item in result["penalties"]}
    assert {
        "annual_eps_fallback",
        "basic_eps_fallback",
        "shares_unavailable",
        "preferred_equity_missing",
        "current_assets_missing",
        "current_liabilities_missing",
        "long_term_debt_missing",
        "total_debt_missing",
        "goodwill_missing",
        "intangible_assets_missing",
        "incomplete_history",
        "possible_future_data",
    }.issubset(codes)


def test_graham_score_categories_boundaries_and_explanations():
    evaluation = evaluate_graham_candidate(_inputs(market_cap=10_000_000_000.0))

    categories = evaluation.metrics.category_scores
    assert categories["valuation"]["total"] == 40
    assert categories["financial_strength"]["total"] == 25
    assert categories["earnings_quality"]["total"] == 20
    assert categories["tradability"]["total"] == 10
    assert categories["data_quality"]["total"] == 5
    assert evaluation.metrics.graham_quality_score == 100
    assert sum(category["total"] for category in categories.values()) == evaluation.metrics.graham_quality_score
    assert all(component["explanation"] for category in categories.values() for component in category["components"].values())

    failed = evaluate_graham_candidate(_inputs(eps=-1.0), minimum_graham_score=0, minimum_data_quality_score=0)
    assert failed.metrics.graham_quality_score >= 0
    assert failed.qualification_status == QualificationStatus.FAILED
    assert "non_positive_eps" in failed.disqualification_reasons

    assert graham_quality_score(_inputs(), GrahamMetrics(None, None, None, None, 0.20, None, 10, 1, 10, 2, 1, 0.5, 5, 5, 5, 0.25, 0, 90, 0))["categories"]["valuation"]["rules"]["margin_of_safety"] == 8


def test_report_and_audit_include_explainability_metadata():
    eps_selection = select_eps(four_quarters(), [])
    inputs = _inputs(
        filing_metadata={
            "shareholders_equity": {"accession_number": "0000000001-24-000020"},
            "_identity": {
                "cik": "0000000001",
                "exchange": "NYSE",
                "security_type": "Common Stock",
                "eps_selection": eps_selection,
                "shares_method": "shares_outstanding",
                "shares_source": share_row(100),
                "earnings_stability": _inputs().filing_metadata["_identity"]["earnings_stability"],
            },
        }
    )
    evaluation = evaluate_graham_candidate(inputs)

    row = graham_summary_row(evaluation)
    audit = graham_audit_row(evaluation)

    assert row["eps_method"] == "TTM_DILUTED"
    assert row["eps_accession_numbers"]
    assert row["shares_accession_number"] == "0000000001-24-000010"
    assert row["book_value_source"] == "0000000001-24-000020"
    assert "valuation" in row["graham_score_breakdown"]
    assert audit["ticker"] == "TEST"
    assert audit["eps_available"] is True
    assert audit["graham_ready"] is True


def test_audit_command_text_json_and_deterministic_output(monkeypatch, capsys):
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
    args = main.parse_args(["audit-graham-data", "--tickers", "AAPL", "MSFT", "--as-of", "2025-06-01"])
    main._audit_graham_data_command(args)
    first = capsys.readouterr().out
    main._audit_graham_data_command(args)
    second = capsys.readouterr().out
    assert first == second
    assert "ticker\tprice_available\teps_available" in first

    args = main.parse_args(["audit-graham-data", "--tickers", "AAPL", "--as-of", "2025-06-01", "--json"])
    main._audit_graham_data_command(args)
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["ticker"] == "AAPL"


def test_source_tables_are_not_mutated_by_audit_style_reads(tmp_path):
    db = tmp_path / "audit.db"
    initialize_database(db)
    with get_connection(db) as connection:
        before = {
            "daily_prices": connection.execute("SELECT COUNT(*) AS c FROM daily_prices").fetchone()["c"],
            "fundamental_filings": connection.execute("SELECT COUNT(*) AS c FROM fundamental_filings").fetchone()["c"],
            "fundamental_facts": connection.execute("SELECT COUNT(*) AS c FROM fundamental_facts").fetchone()["c"],
        }

    evaluate_graham_candidate(_inputs())

    with get_connection(db) as connection:
        after = {
            "daily_prices": connection.execute("SELECT COUNT(*) AS c FROM daily_prices").fetchone()["c"],
            "fundamental_filings": connection.execute("SELECT COUNT(*) AS c FROM fundamental_filings").fetchone()["c"],
            "fundamental_facts": connection.execute("SELECT COUNT(*) AS c FROM fundamental_facts").fetchone()["c"],
        }
    assert after == before
