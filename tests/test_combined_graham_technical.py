from dataclasses import asdict

import pytest

from backtesting.models import Position, Signal, SignalAction
from configurations.models import CombinedStrategyConfig, GrahamStrategyConfig, TechnicalCapitulationConfig
from configurations.presets import get_combined_preset, list_combined_presets
from configurations.validation import validate_combined_strategy_config
from database.repositories import count_price_rows, upsert_daily_prices, upsert_security
from database.schema import initialize_database
import database.connection as connection_module
from main import parse_args
from reporting.combined_strategy_report import combined_summary_row
from strategies.combined_graham_technical import (
    CombinedGrahamTechnicalStrategy,
    CombinedSignalType,
    calculate_panic_score,
    calculate_technical_metrics,
    evaluate_combined_candidate,
    evaluate_technical_capitulation,
    normalize_technical_score,
    rank_combined_candidates,
)
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


@pytest.fixture
def temp_database(tmp_path, monkeypatch):
    db_path = tmp_path / "combined_test.db"
    initialize_database(db_path)
    monkeypatch.setattr(connection_module, "DATABASE_PATH", db_path)
    return db_path


def price_history(ticker="TEST", selloff=True):
    rows = []
    for index in range(31):
        close = 100.0 + index * 0.2
        volume = 1000
        if selloff and index >= 21:
            close = 105.0 - (index - 20) * 2.0
        if selloff and index == 30:
            volume = 3000
        rows.append(
            {
                "ticker": ticker,
                "trade_date": f"2024-01-{index + 1:02d}",
                "open": close + 0.5,
                "high": close + 1.0,
                "low": close - 1.0,
                "close": close,
                "adjusted_close": close,
                "volume": volume,
                "downloaded_at": "2026-06-24T00:00:00+00:00",
            }
        )
    return rows


def graham_eval(
    ticker="TEST",
    qualified=True,
    score=75.0,
    margin=0.35,
    data_quality=70.0,
    average_dollar_volume=10_000_000.0,
):
    inputs = GrahamInputs(
        ticker=ticker,
        evaluation_date="2024-01-31",
        market_price=50.0,
        average_dollar_volume_20d=average_dollar_volume,
        shares_outstanding=1_000_000.0,
        market_cap=50_000_000.0,
        eps=5.0,
        eps_method=EPSMethod.ANNUAL_DILUTED,
        net_income=5_000_000.0,
        current_assets=10_000_000.0,
        current_liabilities=4_000_000.0,
        total_assets=30_000_000.0,
        total_liabilities=10_000_000.0,
        long_term_debt=2_000_000.0,
        total_debt=2_000_000.0,
        shareholders_equity=20_000_000.0,
        preferred_equity=0.0,
        goodwill=0.0,
        intangible_assets=0.0,
        operating_income=2_000_000.0,
        interest_expense=100_000.0,
        operating_cash_flow=3_000_000.0,
        filing_metadata={},
        warnings=[],
    )
    metrics = GrahamMetrics(
        book_value_per_share=20.0,
        tangible_book_value_per_share=20.0,
        graham_number=75.0,
        tangible_graham_number=75.0,
        margin_of_safety=margin,
        tangible_margin_of_safety=margin,
        price_to_earnings=10.0,
        price_to_book=2.5,
        pe_times_pb=25.0,
        current_ratio=2.5,
        net_current_assets=6_000_000.0,
        debt_to_equity=0.1,
        interest_coverage=20.0,
        positive_earnings_years=5,
        total_earnings_years=5,
        five_year_eps_growth=0.1,
        earnings_volatility=0.1,
        data_quality_score=data_quality,
        graham_quality_score=score,
        category_scores={"valuation": {"score": 40}},
        data_quality_classification=DataQualityClass.GOOD,
    )
    return GrahamEvaluation(
        ticker,
        "2024-01-31",
        inputs,
        metrics,
        QualificationStatus.QUALIFIED if qualified else QualificationStatus.FAILED,
        QualificationStatus.QUALIFIED if qualified else QualificationStatus.FAILED,
        GrahamClassification.QUALIFIED if qualified else GrahamClassification.NOT_QUALIFIED,
        "BUY" if qualified else "HOLD",
        GrahamSignalType.GRAHAM_CANDIDATE if qualified else GrahamSignalType.NONE,
        [] if qualified else ["graham_score_below_minimum"],
        [],
        {},
    )


class FakeGrahamStrategy:
    def __init__(self, evaluation):
        self.evaluation = evaluation

    def evaluate(self, ticker, evaluation_date, price_history=None):
        return self.evaluation

    def generate_exit_signal(self, position, as_of_date, price_history):
        return Signal(position.ticker, as_of_date, "fake_graham", SignalAction.HOLD, 0.0, "hold")


def test_technical_metric_calculation_and_no_future_access():
    history = price_history() + [
        {
            "ticker": "TEST",
            "trade_date": "2024-02-01",
            "open": 1.0,
            "high": 1.0,
            "low": 1.0,
            "close": 1.0,
            "adjusted_close": 1.0,
            "volume": 999999,
            "downloaded_at": "x",
        }
    ]
    metrics, warnings, source_dates = calculate_technical_metrics("TEST", "2024-01-31", history)
    assert not warnings
    assert source_dates[-1] == "2024-01-31"
    assert metrics.five_day_return < -0.10
    assert metrics.ten_day_return < -0.15
    assert metrics.relative_volume > 1.5
    assert metrics.rsi <= 35
    assert metrics.percent_below_moving_average < -0.05


def test_insufficient_lookback_reports_warning():
    metrics, warnings, _ = calculate_technical_metrics("TEST", "2024-01-05", price_history()[:5])
    assert metrics.five_day_return is None
    assert warnings


def test_panic_score_components_and_total():
    technical = evaluate_technical_capitulation("TEST", "2024-01-31", price_history())
    names = [component.name for component in technical.panic_score.components]
    assert names == [
        "five_day_decline",
        "ten_day_decline",
        "relative_volume",
        "rsi",
        "distance_below_moving_average",
    ]
    assert technical.panic_score.total_score >= 7
    assert technical.qualified


def test_technical_rejection_when_no_selloff():
    technical = evaluate_technical_capitulation("TEST", "2024-01-31", price_history(selloff=False))
    assert not technical.qualified
    assert "five_day_decline_below_minimum" in technical.disqualification_reasons


def test_graham_and_technical_qualified_combines_to_candidate():
    technical = evaluate_technical_capitulation("TEST", "2024-01-31", price_history())
    combined = evaluate_combined_candidate(graham_eval(), technical)
    assert combined.qualified
    assert combined.signal_type == CombinedSignalType.GRAHAM_TECHNICAL_CANDIDATE
    assert combined.combined_score > 70


def test_graham_failed_and_technical_qualified_rejects():
    technical = evaluate_technical_capitulation("TEST", "2024-01-31", price_history())
    combined = evaluate_combined_candidate(graham_eval(qualified=False, score=40), technical)
    assert not combined.qualified
    assert "graham_not_qualified" in combined.disqualification_reasons


def test_graham_qualified_and_technical_failed_rejects():
    technical = evaluate_technical_capitulation("TEST", "2024-01-31", price_history(selloff=False))
    combined = evaluate_combined_candidate(graham_eval(), technical)
    assert not combined.qualified
    assert "technical_not_qualified" in combined.disqualification_reasons


def test_weighted_composite_score_and_invalid_weights():
    technical = evaluate_technical_capitulation("TEST", "2024-01-31", price_history())
    config = CombinedStrategyConfig(combination_mode="weighted_composite", graham_weight=0.5, technical_weight=0.5)
    combined = evaluate_combined_candidate(graham_eval(score=80), technical, config)
    assert combined.combined_score == round(80 * 0.5 + normalize_technical_score(technical.panic_score.total_score) * 0.5, 6)
    errors = validate_combined_strategy_config(CombinedStrategyConfig(combination_mode="weighted_composite", graham_weight=0.7, technical_weight=0.4))
    assert any(error.field == "weights" for error in errors)


def test_confirmation_same_day_inside_and_outside_window():
    technical = evaluate_technical_capitulation("TEST", "2024-01-31", price_history())
    assert evaluate_combined_candidate(graham_eval(), technical, graham_signal_date="2024-01-31", technical_signal_date="2024-01-31").qualified
    inside = evaluate_combined_candidate(graham_eval(), technical, graham_signal_date="2024-01-25", technical_signal_date="2024-01-31")
    assert inside.qualified
    outside = evaluate_combined_candidate(graham_eval(), technical, graham_signal_date="2024-01-10", technical_signal_date="2024-01-31")
    assert not outside.qualified
    assert "confirmation_window_expired" in outside.disqualification_reasons


def test_graham_first_requirement_rejects_earlier_technical_signal():
    technical = evaluate_technical_capitulation("TEST", "2024-01-31", price_history())
    combined = evaluate_combined_candidate(graham_eval(), technical, graham_signal_date="2024-01-31", technical_signal_date="2024-01-30")
    assert not combined.qualified
    assert "technical_before_graham" in combined.disqualification_reasons


def test_strategy_generates_candidate_signal_and_no_duplicate_hold():
    strategy = CombinedGrahamTechnicalStrategy(graham_strategy=FakeGrahamStrategy(graham_eval()))
    signal = strategy.generate_entry_signal("TEST", "2024-01-31", price_history())
    assert signal.action == SignalAction.BUY
    assert signal.reason == CombinedSignalType.GRAHAM_TECHNICAL_CANDIDATE.value
    hold = strategy.generate_entry_signal("TEST", "2024-01-31", price_history(selloff=False))
    assert hold.action == SignalAction.HOLD


def test_strong_candidate_generation():
    strategy = CombinedGrahamTechnicalStrategy(graham_strategy=FakeGrahamStrategy(graham_eval(score=85, margin=0.45, data_quality=80)))
    signal = strategy.generate_entry_signal("TEST", "2024-01-31", price_history())
    assert signal.action == SignalAction.BUY
    assert signal.reason in {
        CombinedSignalType.GRAHAM_TECHNICAL_CANDIDATE.value,
        CombinedSignalType.STRONG_GRAHAM_TECHNICAL_CANDIDATE.value,
    }


def test_deterministic_candidate_ranking():
    technical = evaluate_technical_capitulation("TEST", "2024-01-31", price_history())
    low = evaluate_combined_candidate(graham_eval("AAA", score=72, margin=0.31), technical)
    high = evaluate_combined_candidate(graham_eval("ZZZ", score=80, margin=0.40), technical)
    assert [item.ticker for item in rank_combined_candidates([low, high])] == ["ZZZ", "AAA"]


def test_exit_rules_stop_loss_max_holding_and_technical_recovery():
    strategy = CombinedGrahamTechnicalStrategy(
        graham_strategy=FakeGrahamStrategy(graham_eval()),
        stop_loss_pct=0.25,
        maximum_holding_days=1,
    )
    position = Position("TEST", "combined", 10, "2024-01-01", 100.0, 70.0)
    stop_signal = strategy.generate_exit_signal(position, "2024-01-31", price_history())
    assert stop_signal.action == SignalAction.SELL
    assert stop_signal.reason in {"STOP_LOSS", "MAXIMUM_HOLDING_PERIOD"}


def test_reporting_row_and_configuration_serialization():
    technical = evaluate_technical_capitulation("TEST", "2024-01-31", price_history())
    combined = evaluate_combined_candidate(graham_eval(), technical)
    row = combined_summary_row(combined)
    assert row["ticker"] == "TEST"
    assert row["combined_qualified"]
    assert asdict(CombinedStrategyConfig())["combination_mode"] == "both_required"


def test_presets_and_cli_parsing():
    assert "Graham + Panic - Moderate" in list_combined_presets()
    assert get_combined_preset("Graham + Panic - Strict").technical.minimum_panic_score == 9.0
    screen_args = parse_args(["screen-combined", "--tickers", "AAPL", "--as-of", "2024-01-31", "--json"])
    backtest_args = parse_args(["run-combined-backtest", "--tickers", "AAPL", "--start-date", "2024-01-01", "--end-date", "2024-01-31", "--no-persist"])
    compare_args = parse_args(["compare-strategies", "--tickers", "AAPL", "--start-date", "2024-01-01", "--end-date", "2024-01-31"])
    assert screen_args.command == "screen-combined"
    assert backtest_args.command == "run-combined-backtest"
    assert compare_args.command == "compare-strategies"


def test_no_mutation_of_daily_prices_or_fundamental_tables(temp_database):
    upsert_security("TEST")
    upsert_daily_prices(price_history())
    before = count_price_rows("TEST")
    strategy = CombinedGrahamTechnicalStrategy(graham_strategy=FakeGrahamStrategy(graham_eval()))
    strategy.generate_entry_signal("TEST", "2024-01-31", price_history())
    assert count_price_rows("TEST") == before
