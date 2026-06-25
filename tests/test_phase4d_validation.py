import json

import pytest

from main import parse_args
from reporting.shortlist_report import shortlist_report
from reporting.validation_report import validation_report
from validation.diagnostics import aggregate_results, trade_count_label, trade_diagnostics
from validation.periods import ValidationPeriod, load_periods, validate_development_holdout
from validation.sensitivity import _classify, run_sensitivity
from validation.shortlist import OpportunityRankingResult, rank_rows, shortlist_summary


def test_validation_period_json_parsing(tmp_path):
    path = tmp_path / "periods.json"
    path.write_text(json.dumps({"periods": [{"name": "p1", "start_date": "2025-01-01", "end_date": "2025-01-31", "type": "development", "description": "dev"}]}), encoding="utf-8")
    periods = load_periods(str(path))
    assert periods[0].name == "p1"
    assert periods[0].period_type == "development"


def test_invalid_overlapping_development_holdout():
    development = ValidationPeriod("dev", "2025-01-01", "2025-02-01", "development")
    holdout = ValidationPeriod("hold", "2025-02-01", "2025-03-01", "holdout")
    with pytest.raises(ValueError):
        validate_development_holdout(development, holdout)


def test_trade_count_sufficiency_labels_and_warnings():
    assert trade_count_label(0) == "insufficient"
    assert trade_count_label(10) == "very limited"
    assert trade_count_label(30) == "limited"
    assert trade_count_label(75) == "moderate"
    assert trade_count_label(100) == "stronger sample"
    diagnostics = trade_diagnostics([{"ticker": "A", "return_pct": 0.5}, {"ticker": "A", "return_pct": -0.1}])
    assert "strategy generates too few completed trades" in diagnostics["warnings"]
    assert "one ticker contributes an outsized share of trades" in diagnostics["warnings"]


def test_stability_classification():
    assert _classify({"completed_trade_count": 2}, {"completed_trade_count": 3}) == "insufficient evidence"
    assert _classify({"completed_trade_count": 20, "total_return_pct": 0.10}, {"completed_trade_count": 21, "total_return_pct": 0.11}) == "stable"
    assert _classify({"completed_trade_count": 20, "total_return_pct": 0.10}, {"completed_trade_count": 5, "total_return_pct": -0.30}) == "highly sensitive"


def test_legacy_technical_panic_characterization():
    def legacy_score(price_change_5d, price_change_10d, volume_spike, dollar_volume):
        score = 0
        if price_change_5d <= -25:
            score += 5
        elif price_change_5d <= -20:
            score += 4
        elif price_change_5d <= -15:
            score += 3
        elif price_change_5d <= -10:
            score += 2
        elif price_change_5d <= -5:
            score += 1
        if price_change_10d <= -30:
            score += 4
        elif price_change_10d <= -20:
            score += 3
        elif price_change_10d <= -10:
            score += 2
        if volume_spike >= 5:
            score += 5
        elif volume_spike >= 3:
            score += 4
        elif volume_spike >= 2:
            score += 3
        elif volume_spike >= 1.5:
            score += 1
        if dollar_volume >= 50_000_000:
            score += 3
        elif dollar_volume >= 10_000_000:
            score += 2
        elif dollar_volume >= 2_000_000:
            score += 1
        return score
    assert legacy_score(-10, -10, 1.5, 2_000_000) == 6
    assert legacy_score(10, 10, 5, 50_000_000) == 8


def test_legacy_opportunity_score_characterization():
    value_score = 70
    discount = 30
    panic = 7
    legacy = value_score * 0.45 + discount * 0.35 + panic * 4
    assert legacy == 70.0
    negative = 0 * 0.45 + (-20) * 0.35 + 0
    assert negative < 0


def test_shortlist_ranking_components_hard_disqualification_and_ties():
    rows = [
        {"ticker": "BBB", "graham_score": 80, "margin_of_safety": 0.4, "panic_score": 8, "relative_volume": 2, "data_quality_score": 80, "combined_score": 80, "qualified": True},
        {"ticker": "AAA", "graham_score": 80, "margin_of_safety": 0.4, "panic_score": 8, "relative_volume": 2, "data_quality_score": 80, "combined_score": 80, "qualified": True},
        {"ticker": "CCC", "graham_score": 100, "margin_of_safety": 1.0, "panic_score": 15, "relative_volume": 3, "data_quality_score": 100, "combined_score": 100, "qualified": False, "primary_data_issue": "missing price"},
    ]
    ranked = rank_rows(rows)
    assert ranked[0].ticker == "AAA"
    assert ranked[-1].ticker == "CCC"
    assert ranked[-1].hard_disqualified
    assert "graham" in ranked[0].component_scores


def test_shortlist_summary_and_reports():
    ranked = [OpportunityRankingResult("AAA", 10, {}, {"primary_data_issue": ""}, True, False, 1, "ok")]
    summary = shortlist_summary(1, 1, 1, ranked)
    assert summary["qualified"] == 1
    assert shortlist_report(ranked, summary)["summary"]["returned"] == 1
    assert "survivorship_warnings" in validation_report({"strategy": "combined"})


def test_aggregate_summary_insufficient_evidence():
    from validation.diagnostics import StrategyValidationResult
    result = StrategyValidationResult("combined", "default", ValidationPeriod("p", "2025-01-01", "2025-01-31", "custom"), 1, 1, 0, 0.0, None, None, None, 0.0, None, None, 0.0, 0.0, 0.0, 0.0, None, None)
    summary = aggregate_results("combined", [result])
    assert summary.conclusion == "insufficient evidence"


def test_phase4d_cli_commands_registered():
    assert parse_args(["validate-strategy", "--strategy", "technical", "--ticker-file", "x", "--development-start", "2025-01-01", "--development-end", "2025-01-31", "--holdout-start", "2025-02-01", "--holdout-end", "2025-02-28"]).command == "validate-strategy"
    assert parse_args(["validate-across-periods", "--strategy", "technical", "--ticker-file", "x", "--periods-file", "p.json"]).command == "validate-across-periods"
    assert parse_args(["run-sensitivity-analysis", "--strategy", "combined", "--ticker-file", "x", "--start-date", "2025-01-01", "--end-date", "2025-02-01", "--parameter", "minimum_panic_score"]).command == "run-sensitivity-analysis"
    assert parse_args(["shortlist-opportunities", "--strategy", "combined", "--tickers", "F", "--as-of", "2025-06-01"]).command == "shortlist-opportunities"
