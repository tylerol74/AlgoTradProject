"""Combined Graham value and technical capitulation strategy."""

import math
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple

from backtesting.models import Position, Signal, SignalAction
from configurations.models import CombinedStrategyConfig, TechnicalCapitulationConfig, UniverseConfig
from configurations.validation import ConfigurationValidationError, validate_combined_strategy_config
from indicators.moving_averages import simple_moving_average
from indicators.returns import percentage_return
from strategies.base import BaseStrategy
from strategies.graham_models import GrahamEvaluation
from strategies.graham_value import GrahamValueStrategy


class CombinedSignalType(str, Enum):
    NONE = "NONE"
    TECHNICAL_CAPITULATION = "TECHNICAL_CAPITULATION"
    GRAHAM_TECHNICAL_CANDIDATE = "GRAHAM_TECHNICAL_CANDIDATE"
    STRONG_GRAHAM_TECHNICAL_CANDIDATE = "STRONG_GRAHAM_TECHNICAL_CANDIDATE"


@dataclass(frozen=True)
class PanicScoreComponent:
    name: str
    score: int
    observed_value: Optional[float]
    threshold_value: str
    passed: bool
    explanation: str


@dataclass(frozen=True)
class TechnicalPanicScore:
    total_score: int
    components: List[PanicScoreComponent] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class TechnicalMetrics:
    one_day_return: Optional[float]
    five_day_return: Optional[float]
    ten_day_return: Optional[float]
    twenty_day_return: Optional[float]
    rsi: Optional[float]
    current_volume: Optional[float]
    average_volume: Optional[float]
    relative_volume: Optional[float]
    moving_average: Optional[float]
    percent_below_moving_average: Optional[float]
    recent_volatility: Optional[float]
    gap_percentage: Optional[float]


@dataclass(frozen=True)
class TechnicalCapitulationEvaluation:
    ticker: str
    evaluation_date: str
    metrics: TechnicalMetrics
    panic_score: TechnicalPanicScore
    qualified: bool
    disqualification_reasons: List[str]
    warnings: List[str]
    source_dates: List[str]


@dataclass(frozen=True)
class CombinedStrategyEvaluation:
    ticker: str
    evaluation_date: str
    graham_evaluation: GrahamEvaluation
    technical_evaluation: TechnicalCapitulationEvaluation
    combined_score: float
    combination_mode: str
    qualified: bool
    classification: str
    signal_type: CombinedSignalType
    disqualification_reasons: List[str]
    warnings: List[str]
    explanation: str
    graham_signal_date: str
    technical_signal_date: str


def _finite(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    number = float(value)
    return None if math.isnan(number) or math.isinf(number) else number


def _trailing_return(closes: Sequence[float], periods: int) -> Optional[float]:
    if len(closes) <= periods:
        return None
    prior = _finite(closes[-periods - 1])
    current = _finite(closes[-1])
    if prior is None or current is None or prior <= 0:
        return None
    return percentage_return(current, prior)


def _rsi(closes: Sequence[float], window: int) -> Optional[float]:
    if len(closes) <= window:
        return None
    changes = [closes[index] - closes[index - 1] for index in range(len(closes) - window, len(closes))]
    gains = [max(change, 0.0) for change in changes]
    losses = [abs(min(change, 0.0)) for change in changes]
    avg_gain = sum(gains) / float(window)
    avg_loss = sum(losses) / float(window)
    if avg_loss == 0:
        return 100.0
    return 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))


def _volatility(values: Sequence[float]) -> Optional[float]:
    clean = [value for value in values if _finite(value) is not None]
    if len(clean) < 2:
        return None
    mean = sum(clean) / len(clean)
    return math.sqrt(sum((value - mean) ** 2 for value in clean) / (len(clean) - 1))


def calculate_technical_metrics(ticker: str, evaluation_date: str, price_history: List[Dict[str, Any]], config: TechnicalCapitulationConfig = TechnicalCapitulationConfig()) -> Tuple[TechnicalMetrics, List[str], List[str]]:
    rows = BaseStrategy.history_as_of(price_history, evaluation_date)
    source_dates = [row["trade_date"] for row in rows]
    minimum_rows = max(21, config.rsi_window + 1, config.volume_lookback + 1, config.moving_average_window)
    warnings = [] if len(rows) >= minimum_rows else [f"insufficient lookback: need {minimum_rows} rows, found {len(rows)}"]
    closes = [float(row["close"]) for row in rows if _finite(row.get("close")) is not None]
    volumes = [float(row["volume"]) for row in rows if _finite(row.get("volume")) is not None]
    prior_volumes = volumes[-config.volume_lookback - 1:-1] if len(volumes) > config.volume_lookback else []
    average_volume = sum(prior_volumes) / len(prior_volumes) if prior_volumes else None
    current_volume = volumes[-1] if volumes else None
    moving_average = simple_moving_average(closes, config.moving_average_window) if closes else None
    distance = (closes[-1] - moving_average) / moving_average if closes and moving_average and moving_average > 0 else None
    recent_returns = [_trailing_return(closes[:idx + 1], 1) for idx in range(max(1, len(closes) - 20), len(closes))]
    gap = None
    if len(rows) >= 2:
        latest_open = _finite(rows[-1].get("open"))
        previous_close = _finite(rows[-2].get("close"))
        if latest_open is not None and previous_close and previous_close > 0:
            gap = percentage_return(latest_open, previous_close)
    return TechnicalMetrics(_trailing_return(closes, 1), _trailing_return(closes, 5), _trailing_return(closes, 10), _trailing_return(closes, 20), _rsi(closes, config.rsi_window), current_volume, average_volume, current_volume / average_volume if current_volume is not None and average_volume and average_volume > 0 else None, moving_average, distance, _volatility([value for value in recent_returns if value is not None]), gap), warnings, source_dates


def _score_band(value: Optional[float], bands: Sequence[Tuple[float, int]]) -> int:
    if value is None:
        return 0
    score = 0
    for threshold, band_score in bands:
        if value >= threshold:
            score = band_score
    return score


def calculate_panic_score(metrics: TechnicalMetrics) -> TechnicalPanicScore:
    five_decline = max(0.0, -(metrics.five_day_return or 0.0)) if metrics.five_day_return is not None else None
    ten_decline = max(0.0, -(metrics.ten_day_return or 0.0)) if metrics.ten_day_return is not None else None
    ma_decline = max(0.0, -(metrics.percent_below_moving_average or 0.0)) if metrics.percent_below_moving_average is not None else None
    values = [
        ("five_day_decline", five_decline, _score_band(five_decline, [(0.05, 1), (0.10, 2), (0.15, 3)]), "5%/10%/15%"),
        ("ten_day_decline", ten_decline, _score_band(ten_decline, [(0.08, 1), (0.15, 2), (0.25, 3)]), "8%/15%/25%"),
        ("relative_volume", metrics.relative_volume, _score_band(metrics.relative_volume, [(1.2, 1), (1.5, 2), (2.0, 3)]), "1.2x/1.5x/2.0x"),
        ("rsi", metrics.rsi, 0 if metrics.rsi is None or metrics.rsi > 40 else 1 if metrics.rsi >= 35 else 2 if metrics.rsi >= 25 else 3, "40/35/25"),
        ("distance_below_moving_average", ma_decline, _score_band(ma_decline, [(0.03, 1), (0.05, 2), (0.10, 3)]), "3%/5%/10%"),
    ]
    components = [PanicScoreComponent(name, int(score), observed, threshold, int(score) > 0, f"{name} score {int(score)}") for name, observed, score, threshold in values]
    warnings = [f"{name} unavailable" for name, observed, _, _ in values if observed is None]
    return TechnicalPanicScore(sum(component.score for component in components), components, warnings)


def evaluate_technical_capitulation(ticker: str, evaluation_date: str, price_history: List[Dict[str, Any]], config: TechnicalCapitulationConfig = TechnicalCapitulationConfig()) -> TechnicalCapitulationEvaluation:
    metrics, warnings, source_dates = calculate_technical_metrics(ticker, evaluation_date, price_history, config)
    panic = calculate_panic_score(metrics)
    reasons = []
    five = max(0.0, -(metrics.five_day_return or 0.0)) if metrics.five_day_return is not None else None
    ten = max(0.0, -(metrics.ten_day_return or 0.0)) if metrics.ten_day_return is not None else None
    ma = max(0.0, -(metrics.percent_below_moving_average or 0.0)) if metrics.percent_below_moving_average is not None else None
    if five is None or five < config.minimum_five_day_decline:
        reasons.append("five_day_decline_below_minimum")
    if ten is None or ten < config.minimum_ten_day_decline:
        reasons.append("ten_day_decline_below_minimum")
    if config.require_volume_spike and (metrics.relative_volume is None or metrics.relative_volume < config.minimum_relative_volume):
        reasons.append("relative_volume_below_minimum")
    if config.require_oversold and (metrics.rsi is None or metrics.rsi > config.maximum_rsi):
        reasons.append("rsi_above_maximum")
    if ma is None or ma < config.minimum_distance_below_moving_average:
        reasons.append("moving_average_distance_below_minimum")
    if panic.total_score < config.minimum_panic_score:
        reasons.append("panic_score_below_minimum")
    return TechnicalCapitulationEvaluation(ticker.upper(), evaluation_date, metrics, panic, not reasons, sorted(set(reasons)), sorted(set(warnings + panic.warnings)), source_dates)


def normalize_technical_score(panic_score: int) -> float:
    return max(0.0, min(100.0, panic_score / 15.0 * 100.0))


def _trading_day_distance(source_dates: Sequence[str], start_date: str, end_date: str) -> Optional[int]:
    if start_date > end_date:
        return None
    if start_date == end_date:
        return 0
    dates = [value for value in source_dates if start_date <= value <= end_date]
    return max(0, len(dates) - 1) if dates else None


def evaluate_combined_candidate(graham_evaluation: GrahamEvaluation, technical_evaluation: TechnicalCapitulationEvaluation, config: CombinedStrategyConfig = CombinedStrategyConfig(), graham_signal_date: Optional[str] = None, technical_signal_date: Optional[str] = None) -> CombinedStrategyEvaluation:
    errors = validate_combined_strategy_config(config)
    if errors:
        raise ConfigurationValidationError(errors)
    graham_date = graham_signal_date or graham_evaluation.evaluation_date
    technical_date = technical_signal_date or technical_evaluation.evaluation_date
    graham_score = float(graham_evaluation.metrics.graham_quality_score)
    technical_score = normalize_technical_score(technical_evaluation.panic_score.total_score)
    combined_score = round(graham_score * config.graham_weight + technical_score * config.technical_weight, 6)
    reasons = []
    if graham_evaluation.qualification_status.value != "QUALIFIED":
        reasons.append("graham_not_qualified")
    if graham_evaluation.metrics.margin_of_safety is None or graham_evaluation.metrics.margin_of_safety < config.graham.minimum_margin_of_safety:
        reasons.append("margin_of_safety_below_minimum")
    if graham_score < config.graham.minimum_graham_score:
        reasons.append("graham_score_below_minimum")
    if graham_evaluation.metrics.data_quality_score < config.graham.minimum_data_quality_score:
        reasons.append("data_quality_below_minimum")
    if not technical_evaluation.qualified:
        reasons.append("technical_not_qualified")
    if config.require_graham_first and technical_date < graham_date:
        reasons.append("technical_before_graham")
    gap = _trading_day_distance(technical_evaluation.source_dates, graham_date, technical_date)
    if gap is None or gap > config.technical.confirmation_window_days:
        reasons.append("confirmation_window_expired")
    if combined_score < config.minimum_combined_score:
        reasons.append("combined_score_below_minimum")
    qualified = not reasons if config.combination_mode != "weighted_composite" else combined_score >= config.minimum_combined_score and "technical_before_graham" not in reasons and "confirmation_window_expired" not in reasons
    strong = qualified and graham_score >= 80 and technical_evaluation.panic_score.total_score >= 9
    signal = CombinedSignalType.STRONG_GRAHAM_TECHNICAL_CANDIDATE if strong else CombinedSignalType.GRAHAM_TECHNICAL_CANDIDATE if qualified else CombinedSignalType.NONE
    return CombinedStrategyEvaluation(graham_evaluation.ticker, technical_evaluation.evaluation_date, graham_evaluation, technical_evaluation, combined_score, config.combination_mode, qualified, "strong" if strong else "qualified" if qualified else "not_qualified", signal, sorted(set(reasons)), sorted(set(graham_evaluation.warnings + technical_evaluation.warnings)), f"Graham score {graham_score:.2f}, technical score {technical_score:.2f}, combined score {combined_score:.2f}", graham_date, technical_date)


def rank_combined_candidates(evaluations: Sequence[CombinedStrategyEvaluation]) -> List[CombinedStrategyEvaluation]:
    return sorted(evaluations, key=lambda item: (-item.combined_score, -(item.graham_evaluation.metrics.margin_of_safety or -1.0), -item.graham_evaluation.metrics.graham_quality_score, -item.technical_evaluation.panic_score.total_score, -(item.graham_evaluation.inputs.average_dollar_volume_20d or 0.0), item.ticker))


class CombinedGrahamTechnicalStrategy(BaseStrategy):
    name = "combined_graham_technical_v1"

    def __init__(self, config: CombinedStrategyConfig = CombinedStrategyConfig(), graham_strategy: Optional[GrahamValueStrategy] = None, universe_config: Optional[UniverseConfig] = None, maximum_holding_days: int = 504, stop_loss_pct: Optional[float] = None, enable_technical_recovery_exit: bool = False) -> None:
        errors = validate_combined_strategy_config(config)
        if errors:
            raise ConfigurationValidationError(errors)
        self.config = config
        self.maximum_holding_days = maximum_holding_days
        self.stop_loss_pct = stop_loss_pct
        self.enable_technical_recovery_exit = enable_technical_recovery_exit
        self.graham_strategy = graham_strategy or GrahamValueStrategy(strategy_config=config.graham, universe_config=universe_config or UniverseConfig(), reevaluation_frequency="daily")
        self._evaluation_cache: Dict[Tuple[str, str], CombinedStrategyEvaluation] = {}

    def evaluate(self, ticker: str, evaluation_date: str, price_history: List[Dict[str, Any]]) -> CombinedStrategyEvaluation:
        key = (ticker.upper(), evaluation_date)
        if key not in self._evaluation_cache:
            graham = self.graham_strategy.evaluate(ticker, evaluation_date, price_history)
            technical = evaluate_technical_capitulation(ticker, evaluation_date, price_history, self.config.technical)
            self._evaluation_cache[key] = evaluate_combined_candidate(graham, technical, self.config)
        return self._evaluation_cache[key]

    def generate_entry_signal(self, ticker: str, as_of_date: str, price_history: List[Dict[str, Any]]) -> Signal:
        evaluation = self.evaluate(ticker, as_of_date, price_history)
        if evaluation.qualified:
            return Signal(ticker, as_of_date, self.name, SignalAction.BUY, evaluation.combined_score, evaluation.signal_type.value)
        return Signal(ticker, as_of_date, self.name, SignalAction.HOLD, 0.0, "; ".join(evaluation.disqualification_reasons))

    def generate_exit_signal(self, position: Position, as_of_date: str, price_history: List[Dict[str, Any]]) -> Signal:
        graham_exit = self.graham_strategy.generate_exit_signal(position, as_of_date, price_history)
        if graham_exit.action == SignalAction.SELL:
            return Signal(position.ticker, as_of_date, self.name, SignalAction.SELL, graham_exit.score, graham_exit.reason)
        rows = self.history_as_of(price_history, as_of_date)
        latest_close = float(rows[-1]["close"]) if rows else None
        if self.stop_loss_pct is not None and latest_close is not None and position.entry_price > 0 and percentage_return(latest_close, position.entry_price) <= -abs(self.stop_loss_pct):
            return Signal(position.ticker, as_of_date, self.name, SignalAction.SELL, 60.0, "STOP_LOSS")
        if (datetime.fromisoformat(as_of_date) - datetime.fromisoformat(position.entry_date)).days >= self.maximum_holding_days:
            return Signal(position.ticker, as_of_date, self.name, SignalAction.SELL, 70.0, "MAXIMUM_HOLDING_PERIOD")
        if self.enable_technical_recovery_exit:
            technical = evaluate_technical_capitulation(position.ticker, as_of_date, price_history, self.config.technical)
            recovered_ma = technical.metrics.moving_average is not None and latest_close is not None and latest_close >= technical.metrics.moving_average
            recovered_rsi = technical.metrics.rsi is not None and technical.metrics.rsi > 55
            if recovered_rsi or recovered_ma or technical.panic_score.total_score < 2:
                return Signal(position.ticker, as_of_date, self.name, SignalAction.SELL, 50.0, "TECHNICAL_RECOVERY")
        return Signal(position.ticker, as_of_date, self.name, SignalAction.HOLD, 0.0, "hold")
