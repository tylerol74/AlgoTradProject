"""Standalone point-in-time Graham value strategy."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from backtesting.models import Position, Signal, SignalAction
from fundamentals.calculations import (
    book_value_per_share,
    common_shareholders_equity,
    current_ratio,
    debt_to_equity,
    graham_number,
    interest_coverage,
    margin_of_safety,
    net_current_assets,
    pe_times_pb,
    price_to_book,
    price_to_earnings,
    tangible_book_value_per_share,
    tangible_common_equity,
)
from fundamentals.earnings import EarningsStability
from fundamentals.point_in_time import build_graham_inputs
from fundamentals.quality import data_quality_score, graham_quality_score, classify_graham_score
from strategies.base import BaseStrategy
from strategies.graham_models import (
    DataQualityClass,
    GrahamClassification,
    GrahamEvaluation,
    GrahamInputs,
    GrahamMetrics,
    GrahamSignalType,
    QualificationStatus,
)


class GrahamValueStrategy(BaseStrategy):
    """Graham Value Baseline strategy that generates signals only."""

    name = "graham_value_v1"

    def __init__(
        self,
        minimum_margin_of_safety: float = 0.30,
        minimum_graham_score: float = 70.0,
        minimum_data_quality_score: float = 60.0,
        maximum_holding_days: int = 504,
        stop_loss_pct: Optional[float] = None,
        reevaluation_frequency: str = "weekly",
        strategy_data: Optional[Any] = None,
        fundamentals_service: Optional[Any] = None,
    ) -> None:
        self.minimum_margin_of_safety = minimum_margin_of_safety
        self.minimum_graham_score = minimum_graham_score
        self.minimum_data_quality_score = minimum_data_quality_score
        self.maximum_holding_days = maximum_holding_days
        self.stop_loss_pct = stop_loss_pct
        self.reevaluation_frequency = reevaluation_frequency
        self.strategy_data = strategy_data
        self.fundamentals_service = fundamentals_service
        self._cache: Dict[tuple, GrahamEvaluation] = {}

    def _strategy_data(self, price_history: List[Dict[str, Any]]) -> Any:
        if self.strategy_data is not None:
            return self.strategy_data

        class HistoryStrategyData:
            def __init__(self, history: List[Dict[str, Any]]) -> None:
                self.history = history

            def get_ticker_history(self, ticker: str, start_date: Optional[str] = None, end_date: Optional[str] = None) -> List[Dict[str, Any]]:
                rows = [row for row in self.history if row.get("ticker", ticker).upper() == ticker.upper()]
                if start_date:
                    rows = [row for row in rows if row["trade_date"] >= start_date]
                if end_date:
                    rows = [row for row in rows if row["trade_date"] <= end_date]
                return rows

        return HistoryStrategyData(price_history)

    def _should_evaluate(self, as_of_date: str) -> bool:
        if self.reevaluation_frequency == "daily":
            return True
        weekday = datetime.fromisoformat(as_of_date).weekday()
        if self.reevaluation_frequency == "weekly":
            return weekday == 0
        if self.reevaluation_frequency == "monthly":
            return as_of_date.endswith("-01") or True
        return True

    def evaluate(self, ticker: str, evaluation_date: str, price_history: Optional[List[Dict[str, Any]]] = None) -> GrahamEvaluation:
        """Evaluate one ticker/date using only point-in-time inputs."""
        key = (ticker.upper(), evaluation_date)
        if key not in self._cache:
            inputs = build_graham_inputs(ticker, evaluation_date, self._strategy_data(price_history or []), self.fundamentals_service)
            self._cache[key] = evaluate_graham_candidate(
                inputs,
                minimum_margin_of_safety=self.minimum_margin_of_safety,
                minimum_graham_score=self.minimum_graham_score,
                minimum_data_quality_score=self.minimum_data_quality_score,
            )
        return self._cache[key]

    def generate_entry_signal(self, ticker: str, as_of_date: str, price_history: List[Dict[str, Any]]) -> Signal:
        """Generate BUY for qualified Graham candidates."""
        if not self._should_evaluate(as_of_date):
            return Signal(ticker, as_of_date, self.name, SignalAction.HOLD, 0.0, "not scheduled for Graham re-evaluation")
        evaluation = self.evaluate(ticker, as_of_date, price_history)
        if evaluation.signal_action == SignalAction.BUY.value:
            return Signal(ticker, as_of_date, self.name, evaluation.metrics.graham_quality_score, evaluation.signal_type.value)
        return Signal(ticker, as_of_date, self.name, SignalAction.HOLD, 0.0, "; ".join(evaluation.disqualification_reasons or evaluation.warnings))

    def generate_exit_signal(self, position: Position, as_of_date: str, price_history: List[Dict[str, Any]]) -> Signal:
        """Generate SELL when fair value, deterioration, max holding period, or stop loss is reached."""
        evaluation = self.evaluate(position.ticker, as_of_date, price_history)
        price = evaluation.inputs.market_price
        if price is not None and evaluation.metrics.graham_number is not None and price >= evaluation.metrics.graham_number:
            return Signal(position.ticker, as_of_date, self.name, SignalAction.SELL, 100.0, "FAIR_VALUE_REACHED")
        if evaluation.metrics.margin_of_safety is not None and evaluation.metrics.margin_of_safety < 0.10:
            return Signal(position.ticker, as_of_date, self.name, SignalAction.SELL, 90.0, "MARGIN_OF_SAFETY_DERIORATED")
        if any(reason in evaluation.disqualification_reasons for reason in ("non_positive_eps", "non_positive_book_value", "negative_common_equity", "two_consecutive_annual_losses")) or evaluation.metrics.graham_quality_score < 50 or evaluation.metrics.data_quality_score < 60:
            return Signal(position.ticker, as_of_date, self.name, SignalAction.SELL, 80.0, "FUNDAMENTAL_DETERIORATION")
        holding_days = (datetime.fromisoformat(as_of_date) - datetime.fromisoformat(position.entry_date)).days
        if holding_days >= self.maximum_holding_days:
            return Signal(position.ticker, as_of_date, self.name, SignalAction.SELL, 70.0, "MAXIMUM_HOLDING_PERIOD")
        if self.stop_loss_pct is not None and price is not None and position.entry_price > 0 and (price - position.entry_price) / position.entry_price <= -abs(self.stop_loss_pct):
            return Signal(position.ticker, as_of_date, self.name, SignalAction.SELL, 60.0, "STOP_LOSS")
        return Signal(position.ticker, as_of_date, self.name, SignalAction.HOLD, 0.0, "hold")


def evaluate_graham_candidate(
    inputs: GrahamInputs,
    minimum_margin_of_safety: float = 0.30,
    minimum_graham_score: float = 70.0,
    minimum_data_quality_score: float = 60.0,
) -> GrahamEvaluation:
    """Calculate metrics, disqualifications, and signal classification."""
    common_equity = common_shareholders_equity(inputs.shareholders_equity, inputs.preferred_equity)
    book = book_value_per_share(common_equity, inputs.shares_outstanding)
    tangible_equity = tangible_common_equity(common_equity, inputs.goodwill, inputs.intangible_assets)
    tangible_book = tangible_book_value_per_share(tangible_equity, inputs.shares_outstanding)
    gnum = graham_number(inputs.eps, book)
    tangible_gnum = graham_number(inputs.eps, tangible_book)
    mos = margin_of_safety(inputs.market_price, gnum)
    tangible_mos = margin_of_safety(inputs.market_price, tangible_gnum)
    pe = price_to_earnings(inputs.market_price, inputs.eps)
    pb = price_to_book(inputs.market_price, book)
    current = current_ratio(inputs.current_assets, inputs.current_liabilities)
    nca = net_current_assets(inputs.current_assets, inputs.current_liabilities)
    total_debt = inputs.total_debt if inputs.total_debt is not None else inputs.long_term_debt
    warnings = list(inputs.warnings)
    if inputs.total_debt is None and inputs.long_term_debt is not None:
        warnings.append("total debt unavailable; long-term debt used for debt-to-equity")
    dte = debt_to_equity(total_debt, common_equity)
    coverage = interest_coverage(inputs.operating_income, inputs.interest_expense)
    stability: EarningsStability = inputs.filing_metadata.get("_identity", {}).get("earnings_stability")  # type: ignore
    dq = data_quality_score(inputs, warnings)
    partial_metrics = GrahamMetrics(
        book, tangible_book, gnum, tangible_gnum, mos, tangible_mos, pe, pb, pe_times_pb(pe, pb),
        current, nca, dte, coverage,
        stability.positive_earnings_years if stability else 0,
        stability.total_earnings_years if stability else 0,
        stability.five_year_eps_growth if stability else None,
        stability.earnings_volatility if stability else None,
        dq["score"], 0.0, {}, dq["classification"],
    )
    gq = graham_quality_score(inputs, partial_metrics)
    metrics = GrahamMetrics(
        book, tangible_book, gnum, tangible_gnum, mos, tangible_mos, pe, pb, pe_times_pb(pe, pb),
        current, nca, dte, coverage,
        partial_metrics.positive_earnings_years, partial_metrics.total_earnings_years,
        partial_metrics.five_year_eps_growth, partial_metrics.earnings_volatility,
        dq["score"], gq["score"], gq["categories"], dq["classification"],
    )
    reasons = _disqualification_reasons(inputs, metrics, common_equity, minimum_margin_of_safety, minimum_data_quality_score)
    qualifies = not reasons and metrics.graham_quality_score >= minimum_graham_score
    strong = qualifies and metrics.margin_of_safety is not None and metrics.margin_of_safety >= 0.40 and metrics.graham_quality_score >= 80 and metrics.data_quality_score >= 75
    signal_type = GrahamSignalType.STRONG_GRAHAM_CANDIDATE if strong else GrahamSignalType.GRAHAM_CANDIDATE if qualifies else GrahamSignalType.NONE
    return GrahamEvaluation(
        inputs.ticker,
        inputs.evaluation_date,
        inputs,
        metrics,
        QualificationStatus.QUALIFIED if not reasons else QualificationStatus.FAILED,
        QualificationStatus.QUALIFIED if qualifies else QualificationStatus.FAILED,
        classify_graham_score(metrics.graham_quality_score) if qualifies else GrahamClassification.NOT_QUALIFIED,
        SignalAction.BUY.value if qualifies else SignalAction.HOLD.value,
        signal_type,
        reasons,
        sorted(set(warnings + dq["warnings"])),
        inputs.filing_metadata,
    )


def _disqualification_reasons(inputs: GrahamInputs, metrics: GrahamMetrics, common_equity: Optional[float], minimum_margin: float, minimum_data_quality: float) -> List[str]:
    reasons: List[str] = []
    if inputs.market_price is None:
        reasons.append("missing_price")
    elif inputs.market_price < 3:
        reasons.append("price_below_3")
    if inputs.average_dollar_volume_20d is None or inputs.average_dollar_volume_20d < 2_000_000:
        reasons.append("liquidity_below_2m")
    if inputs.market_cap is None or inputs.market_cap < 300_000_000:
        reasons.append("market_cap_below_300m")
    identity = inputs.filing_metadata.get("_identity", {})
    security_type = (identity.get("security_type") or "").lower()
    exchange = (identity.get("exchange") or "").lower()
    if security_type and "common" not in security_type:
        reasons.append("ineligible_security_type")
    if exchange and all(item not in exchange for item in ("nyse", "nasdaq", "arca", "american")):
        reasons.append("ineligible_exchange")
    if identity.get("cik") is None:
        reasons.append("missing_cik")
    if not inputs.filing_metadata or len(inputs.filing_metadata) <= 1:
        reasons.append("no_usable_filing")
    if inputs.eps is None or inputs.eps <= 0:
        reasons.append("non_positive_eps")
    if metrics.book_value_per_share is None or metrics.book_value_per_share <= 0:
        reasons.append("non_positive_book_value")
    if common_equity is None or common_equity <= 0:
        reasons.append("negative_common_equity")
    if "bank" in security_type or "insurance" in security_type or "financial" in security_type:
        reasons.append("excluded_financial_sector")
    if "reit" in security_type:
        reasons.append("excluded_reit")
    stability: EarningsStability = identity.get("earnings_stability")
    if stability and stability.two_consecutive_losses:
        reasons.append("two_consecutive_annual_losses")
    if metrics.data_quality_score < minimum_data_quality:
        reasons.append("data_quality_below_minimum")
    if metrics.margin_of_safety is None or metrics.margin_of_safety < minimum_margin:
        reasons.append("margin_of_safety_below_minimum")
    return sorted(set(reasons))
