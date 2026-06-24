"""Whole-share position sizing helpers."""


def calculate_position_quantity(
    portfolio_value: float,
    available_cash: float,
    execution_price: float,
    position_size_pct: float,
    slippage_pct: float = 0.0,
    commission: float = 0.0,
) -> int:
    """Calculate whole-share quantity without exceeding cash or budget."""
    if execution_price <= 0:
        raise ValueError("execution_price must be positive")
    if position_size_pct <= 0 or position_size_pct > 1:
        raise ValueError("position_size_pct must be greater than 0 and at most 1")
    if available_cash < 0:
        raise ValueError("available_cash cannot be negative")
    if portfolio_value < 0:
        raise ValueError("portfolio_value cannot be negative")
    if slippage_pct < 0:
        raise ValueError("slippage_pct cannot be negative")
    if commission < 0:
        raise ValueError("commission cannot be negative")

    adjusted_price = execution_price * (1.0 + slippage_pct)
    spendable_cash = max(available_cash - commission, 0.0)
    position_budget = max(portfolio_value * position_size_pct, 0.0)
    max_spend = min(position_budget, spendable_cash)
    if adjusted_price <= 0 or max_spend < adjusted_price:
        return 0
    quantity = int(max_spend // adjusted_price)
    while quantity > 0 and quantity * adjusted_price + commission > available_cash + 1e-9:
        quantity -= 1
    return max(quantity, 0)
