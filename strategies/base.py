"""Base interfaces for read-only strategies."""

from abc import ABC, abstractmethod
from typing import Any, Dict, List

from backtesting.models import Position, Signal


class BaseStrategy(ABC):
    """Abstract base class for strategies that only generate signals."""

    name = "base"

    @staticmethod
    def history_as_of(price_history: List[Dict[str, Any]], as_of_date: str) -> List[Dict[str, Any]]:
        """Return rows dated on or before as_of_date, sorted by trade_date."""
        return sorted(
            [row for row in price_history if row.get("trade_date") <= as_of_date],
            key=lambda row: row["trade_date"],
        )

    @abstractmethod
    def generate_entry_signal(
        self,
        ticker: str,
        as_of_date: str,
        price_history: List[Dict[str, Any]],
    ) -> Signal:
        """Generate an entry signal from price rows dated on or before as_of_date."""

    @abstractmethod
    def generate_exit_signal(
        self,
        position: Position,
        as_of_date: str,
        price_history: List[Dict[str, Any]],
    ) -> Signal:
        """Generate an exit signal from price rows dated on or before as_of_date."""
