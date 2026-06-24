from fundamentals.earnings import EarningsStability
from backtesting.models import SignalAction
from strategies.graham_models import EPSMethod, GrahamInputs, GrahamSignalType, QualificationStatus
from strategies.graham_value import GrahamValueStrategy, evaluate_graham_candidate


def _inputs(**overrides):
    values = dict(
        ticker="TEST",
        evaluation_date="2025-06-01",
        market_price=10.0,
        average_dollar_volume_20d=60_000_000.0,
        shares_outstanding=100_000_000.0,
        market_cap=1_000_000_000.0,
        eps=5.0,
        eps_method=EPSMethod.TTM_DILUTED,
        net_income=500_000_000.0,
        current_assets=600_000_000.0,
        current_liabilities=200_000_000.0,
        total_assets=2_000_000_000.0,
        total_liabilities=800_000_000.0,
        long_term_debt=100_000_000.0,
        total_debt=150_000_000.0,
        shareholders_equity=1_200_000_000.0,
        preferred_equity=0.0,
        goodwill=0.0,
        intangible_assets=0.0,
        operating_income=600_000_000.0,
        interest_expense=50_000_000.0,
        operating_cash_flow=550_000_000.0,
        filing_metadata={
            "shareholders_equity": {"accession_number": "1", "accepted_at": "2025-02-01"},
            "_identity": {
                "cik": "0000000001",
                "exchange": "NYSE",
                "security_type": "Common Stock",
                "earnings_stability": EarningsStability(
                    positive_earnings_years=5,
                    total_earnings_years=5,
                    two_consecutive_losses=False,
                    earliest_annual_eps=4.0,
                    latest_annual_eps=5.0,
                    five_year_eps_growth=0.25,
                    five_year_eps_cagr=0.057,
                    mean_annual_eps=4.5,
                    earnings_volatility=0.4,
                    minimum_annual_eps=4.0,
                    maximum_annual_eps=5.0,
                    annual_rows=[],
                ),
            },
        },
        warnings=[],
    )
    values.update(overrides)
    return GrahamInputs(**values)


def test_candidate_and_strong_candidate_signals_are_standardized():
    evaluation = evaluate_graham_candidate(_inputs())

    assert evaluation.qualification_status == QualificationStatus.QUALIFIED
    assert evaluation.signal_action == "BUY"
    assert evaluation.signal_type == GrahamSignalType.STRONG_GRAHAM_CANDIDATE
    assert evaluation.metrics.category_scores["valuation"]["total"] == 40


def test_no_signal_when_configured_thresholds_fail():
    evaluation = evaluate_graham_candidate(_inputs(market_price=40.0))

    assert evaluation.qualification_status == QualificationStatus.FAILED
    assert evaluation.signal_type == GrahamSignalType.NONE
    assert "margin_of_safety_below_minimum" in evaluation.disqualification_reasons


def test_configurable_universe_thresholds_are_used_for_disqualifications():
    evaluation = evaluate_graham_candidate(
        _inputs(market_price=10.0, average_dollar_volume_20d=3_000_000.0, market_cap=500_000_000.0),
        minimum_margin_of_safety=0.10,
        minimum_graham_score=0,
        minimum_data_quality_score=0,
    )

    assert "liquidity_below_minimum" not in evaluation.disqualification_reasons
    assert "market_cap_below_minimum" not in evaluation.disqualification_reasons

    strict = GrahamValueStrategy(minimum_average_dollar_volume=10_000_000.0, minimum_market_cap=1_000_000_000.0)
    strict_evaluation = evaluate_graham_candidate(
        _inputs(average_dollar_volume_20d=3_000_000.0, market_cap=500_000_000.0),
        strategy_config=strict.strategy_config,
        universe_config=strict.universe_config,
    )

    assert "liquidity_below_minimum" in strict_evaluation.disqualification_reasons
    assert "market_cap_below_minimum" in strict_evaluation.disqualification_reasons


def test_monthly_reevaluation_only_runs_on_first_day():
    strategy = GrahamValueStrategy(reevaluation_frequency="monthly")

    assert strategy._should_evaluate("2025-06-01") is True
    assert strategy._should_evaluate("2025-06-02") is False


def test_qualified_entry_signal_uses_standard_buy_action():
    strategy = GrahamValueStrategy(reevaluation_frequency="daily")
    strategy._cache[("TEST", "2025-06-01")] = evaluate_graham_candidate(_inputs())

    signal = strategy.generate_entry_signal("TEST", "2025-06-01", [])

    assert signal.action == SignalAction.BUY
    assert signal.score > 0
    assert signal.reason == GrahamSignalType.STRONG_GRAHAM_CANDIDATE.value
