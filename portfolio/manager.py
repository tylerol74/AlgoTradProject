"""Portfolio state management for deterministic backtests."""

from dataclasses import replace
from typing import Dict, Optional

from backtesting.models import Position, Trade

FLOAT_TOLERANCE = 1e-8


class PortfolioManager:
    """Track cash, open positions, completed trades, and P&L."""

    def __init__(self, starting_capital: float, maximum_positions: int) -> None:
        if starting_capital <= 0:
            raise ValueError("starting_capital must be positive")
        if maximum_positions <= 0:
            raise ValueError("maximum_positions must be positive")
        self.starting_capital = float(starting_capital)
        self.cash = float(starting_capital)
        self.maximum_positions = int(maximum_positions)
        self.open_positions: Dict[str, Position] = {}
        self.completed_trades = []
        self.total_commissions = 0.0
        self.realized_pnl = 0.0

    def has_position(self, ticker: str) -> bool:
        """Return whether ticker has an open position."""
        return ticker.upper() in self.open_positions

    def get_position(self, ticker: str) -> Optional[Position]:
        """Return an open position if present."""
        return self.open_positions.get(ticker.upper())

    def open_position(
        self,
        ticker: str,
        strategy: str,
        quantity: int,
        signal_date: str,
        entry_date: str,
        entry_price: float,
        commission: float,
        score: float = 0.0,
        reason: str = "",
    ) -> Position:
        """Open a long whole-share position and deduct cash."""
        normalized = ticker.upper()
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        if entry_price <= 0:
            raise ValueError("entry_price must be positive")
        if commission < 0:
            raise ValueError("commission cannot be negative")
        if self.has_position(normalized):
            raise ValueError(f"duplicate position for {normalized}")
        if len(self.open_positions) >= self.maximum_positions:
            raise ValueError("maximum positions reached")
        total_cost = quantity * entry_price + commission
        if total_cost > self.cash + FLOAT_TOLERANCE:
            raise ValueError("insufficient cash; leverage is not allowed")
        self.cash -= total_cost
        if self.cash < 0 and abs(self.cash) <= FLOAT_TOLERANCE:
            self.cash = 0.0
        if self.cash < -FLOAT_TOLERANCE:
            raise ValueError("cash cannot become negative")
        position = Position(
            ticker=normalized,
            strategy=strategy,
            quantity=float(quantity),
            signal_date=signal_date,
            entry_date=entry_date,
            entry_price=float(entry_price),
            current_price=float(entry_price),
            entry_commission=float(commission),
            signal_score=float(score),
            reason=reason,
        )
        self.open_positions[normalized] = position
        self.total_commissions += commission
        return position

    def close_position(
        self,
        ticker: str,
        exit_date: str,
        exit_price: float,
        commission: float,
        exit_reason: str,
    ) -> Trade:
        """Close an existing long position and record a completed trade."""
        normalized = ticker.upper()
        if exit_price <= 0:
            raise ValueError("exit_price must be positive")
        if commission < 0:
            raise ValueError("commission cannot be negative")
        position = self.open_positions.get(normalized)
        if position is None:
            raise ValueError(f"no open position for {normalized}")
        proceeds = position.quantity * exit_price - commission
        self.cash += proceeds
        gross_pnl = (exit_price - position.entry_price) * position.quantity
        net_pnl = gross_pnl - position.entry_commission - commission
        invested = position.entry_price * position.quantity + position.entry_commission
        return_pct = net_pnl / invested if invested > 0 else 0.0
        trade = Trade(
            ticker=normalized,
            strategy=position.strategy,
            signal_date=position.signal_date,
            entry_date=position.entry_date,
            entry_price=position.entry_price,
            exit_date=exit_date,
            exit_price=float(exit_price),
            quantity=position.quantity,
            gross_pnl=gross_pnl,
            net_pnl=net_pnl,
            return_pct=return_pct,
            exit_reason=exit_reason,
            entry_commission=position.entry_commission,
            exit_commission=float(commission),
        )
        del self.open_positions[normalized]
        self.completed_trades.append(trade)
        self.total_commissions += commission
        self.realized_pnl += net_pnl
        return trade

    def mark_to_market(self, price_map: Dict[str, float]) -> None:
        """Update current prices for held positions using supplied close prices."""
        for ticker, price in price_map.items():
            normalized = ticker.upper()
            if normalized in self.open_positions and price is not None:
                self.open_positions[normalized] = replace(
                    self.open_positions[normalized], current_price=float(price)
                )

    def calculate_holdings_value(self, price_map: Dict[str, float]) -> float:
        """Calculate current market value of open positions."""
        value = 0.0
        for ticker, position in self.open_positions.items():
            price = price_map.get(ticker, position.current_price)
            value += position.quantity * float(price)
        return value

    def calculate_total_value(self, price_map: Dict[str, float]) -> float:
        """Calculate cash plus marked holdings value."""
        return self.cash + self.calculate_holdings_value(price_map)

    def unrealized_pnl(self, price_map: Dict[str, float]) -> float:
        """Calculate unrealized P&L for open positions."""
        total = 0.0
        for ticker, position in self.open_positions.items():
            price = float(price_map.get(ticker, position.current_price))
            total += (price - position.entry_price) * position.quantity
        return total
