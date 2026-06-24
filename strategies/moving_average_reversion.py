"""Diagnostic moving-average reversion strategy."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from backtesting.models import Position, Signal, SignalAction
from indicators.moving_averages import simple_moving_average
from indicators.returns import percentage_return
from strategies.base import BaseStrategy


@dataclass(frozen=True)
class MovingAverageReversionStrategy(BaseStrategy):
    """Generate diagnostic mean-reversion signals around a moving average.

    This strategy only emits standardized signals. It does not execute orders,
    modify cash, write trades, or download market data.
    """

    moving_average_window: int = 20
    entry_discount_pct: float = 0.05
    stop_loss_pct: float = 0.10
    maximum_holding_days: Optional[int] = None
    name: str = "moving_average_reversion"

    def _hold_signal(self, ticker: str, as_of_date: str, reason: str) -> Signal:
        return Signal(
            ticker=ticker,
            signal_date=as_of_date,
            strategy=self.name,
            action=SignalAction.HOLD,
            score=0.0,
            reason=reason,
        )

    def _latest_context(
        self,
        ticker: str,
        as_of_date: str,
        price_history: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        rows = self.history_as_of(price_history, as_of_date)
        if len(rows) < self.moving_average_window:
            return None
        closes = [float(row["close"]) for row in rows]
        sma = simple_moving_average(closes, self.moving_average_window)
        if sma is None:
            return None
        latest = rows[-1]
        return {
            "ticker": ticker,
            "row": latest,
            "close": float(latest["close"]),
            "sma": sma,
            "distance": (float(latest["close"]) - sma) / sma,
        }

    def generate_entry_signal(
        self,
        ticker: str,
        as_of_date: str,
        price_history: List[Dict[str, Any]],
    ) -> Signal:
        """Return BUY when latest close is below the SMA threshold, else HOLD."""
        context = self._latest_context(ticker, as_of_date, price_history)
        if context is None:
            return self._hold_signal(ticker, as_of_date, "insufficient history")

        distance = float(context["distance"])
        if distance <= -self.entry_discount_pct:
            score = round(abs(distance), 6)
            return Signal(
                ticker=ticker,
                signal_date=as_of_date,
                strategy=self.name,
                action=SignalAction.BUY,
                score=score,
                reason=(
                    f"close {context['close']:.2f} is {abs(distance):.2%} below "
                    f"{self.moving_average_window}-day SMA {context['sma']:.2f}"
                ),
            )

        return self._hold_signal(ticker, as_of_date, "entry threshold not met")

    def generate_exit_signal(
        self,
        position: Position,
        as_of_date: str,
        price_history: List[Dict[str, Any]],
    ) -> Signal:
        """Return SELL for SMA recovery, stop loss, or max holding period, else HOLD."""
        context = self._latest_context(position.ticker, as_of_date, price_history)
        if context is None:
            return self._hold_signal(position.ticker, as_of_date, "insufficient history")

        current_close = float(context["close"])
        sma = float(context["sma"])
        trade_return = percentage_return(current_close, position.entry_price)

        if current_close >= sma:
            return Signal(
                ticker=position.ticker,
                signal_date=as_of_date,
                strategy=self.name,
                action=SignalAction.SELL,
                score=round(max(trade_return, 0.0), 6),
                reason=f"close {current_close:.2f} reached {self.moving_average_window}-day SMA {sma:.2f}",
            )

        if trade_return <= -self.stop_loss_pct:
            return Signal(
                ticker=position.ticker,
                signal_date=as_of_date,
                strategy=self.name,
                action=SignalAction.SELL,
                score=round(abs(trade_return), 6),
                reason=f"stop loss triggered at {trade_return:.2%}",
            )

        if self.maximum_holding_days is not None:
            entry_date = datetime.strptime(position.entry_date, "%Y-%m-%d").date()
            current_date = datetime.strptime(as_of_date, "%Y-%m-%d").date()
            holding_days = (current_date - entry_date).days
            if holding_days >= self.maximum_holding_days:
                return Signal(
                    ticker=position.ticker,
                    signal_date=as_of_date,
                    strategy=self.name,
                    action=SignalAction.SELL,
                    score=round(max(holding_days / float(self.maximum_holding_days), 0.0), 6),
                    reason=f"maximum holding period reached: {holding_days} days",
                )

        return self._hold_signal(position.ticker, as_of_date, "exit threshold not met")
