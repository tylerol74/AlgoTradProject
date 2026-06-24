"""Minimal deterministic historical backtesting engine.

Daily event sequence:
1. Identify the current trading date.
2. Execute orders scheduled for the current date at that date's open.
3. Mark current positions to market using the current close.
4. Evaluate exit signals after the current close.
5. Evaluate entry signals after the current close.
6. Rank competing entry signals deterministically.
7. Schedule accepted orders for each ticker's next available trading-day open.
8. Record the end-of-day portfolio snapshot.

Signals generated from a close never execute at that same close.
"""

import json
from dataclasses import asdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from backtesting.execution import calculate_sell_fill_price, order_fill_price, resolve_next_execution
from backtesting.metrics import calculate_benchmark_return, calculate_metrics
from backtesting.models import BacktestConfig, BacktestResult, Order, OrderSide, PortfolioSnapshot, SignalAction
from data import strategy_data as default_strategy_data
from database import repositories
from portfolio.manager import PortfolioManager
from portfolio.position_sizing import calculate_position_quantity
from strategies.base import BaseStrategy


def _normalize_tickers(tickers: List[str]) -> List[str]:
    normalized = [ticker.strip().upper() for ticker in tickers if ticker and ticker.strip()]
    if len(normalized) != len(set(normalized)):
        raise ValueError("duplicate tickers are not allowed")
    return normalized


def _validate_config(config: BacktestConfig) -> List[str]:
    if config.starting_capital <= 0:
        raise ValueError("starting_capital must be positive")
    tickers = _normalize_tickers(config.tickers)
    if not tickers:
        raise ValueError("at least one ticker is required")
    if config.start_date > config.end_date:
        raise ValueError("start_date must be on or before end_date")
    if config.maximum_positions <= 0:
        raise ValueError("maximum_positions must be positive")
    if config.position_size_pct <= 0 or config.position_size_pct > 1:
        raise ValueError("position_size_pct must be greater than 0 and at most 1")
    if config.slippage_pct < 0 or config.slippage_pct >= 1:
        raise ValueError("slippage_pct must be non-negative and less than 1")
    if config.commission_per_trade < 0:
        raise ValueError("commission_per_trade cannot be negative")
    return tickers


def _latest_close_on_or_before(history: List[Dict[str, Any]], as_of_date: str) -> Optional[float]:
    prior = [row for row in history if row["trade_date"] <= as_of_date and row.get("close") is not None]
    if not prior:
        return None
    close = prior[-1]["close"]
    return float(close) if close and float(close) > 0 else None


def _row_on_date(history: List[Dict[str, Any]], trade_date: str) -> Optional[Dict[str, Any]]:
    for row in history:
        if row["trade_date"] == trade_date:
            return row
    return None


def _pending_tickers(pending_orders: List[Order], side: Optional[OrderSide] = None) -> Set[str]:
    return {order.ticker for order in pending_orders if side is None or order.side == side}


def _snapshot(date: str, portfolio: PortfolioManager, price_map: Dict[str, float], running_peak: float) -> PortfolioSnapshot:
    holdings_value = portfolio.calculate_holdings_value(price_map)
    total_value = portfolio.cash + holdings_value
    peak = max(running_peak, total_value)
    drawdown = (total_value - peak) / peak if peak > 0 else 0.0
    return PortfolioSnapshot(date, portfolio.cash, holdings_value, total_value, drawdown)


def _config_payload(config: BacktestConfig, metrics: Dict[str, Any], benchmark: Optional[Dict[str, Any]]) -> str:
    payload = {"config": asdict(config), "metrics": metrics, "benchmark": benchmark}
    return json.dumps(payload, sort_keys=True)


def _persist_result(result: BacktestResult, benchmark: Optional[Dict[str, Any]]) -> BacktestResult:
    ending_value = result.metrics["ending_portfolio_value"]
    parameters_json = _config_payload(result.config, result.metrics, benchmark)
    backtest_id = repositories.create_backtest_run(
        result.config.strategy_name,
        result.config.start_date,
        result.config.end_date,
        result.config.starting_capital,
        parameters_json,
    )
    repositories.insert_backtest_trades(backtest_id, result.trades)
    repositories.insert_portfolio_snapshots(backtest_id, result.portfolio_snapshots)
    repositories.complete_backtest_run(backtest_id, ending_value)
    return BacktestResult(backtest_id, result.config, result.trades, result.portfolio_snapshots, result.metrics)


def run_backtest(
    config: BacktestConfig,
    strategy: BaseStrategy,
    strategy_data: Any = default_strategy_data,
    benchmark_ticker: Optional[str] = None,
    persist: bool = True,
) -> BacktestResult:
    """Run a deterministic long-only daily backtest using stored SQLite prices only."""
    tickers = _validate_config(config)
    histories = {
        ticker: strategy_data.get_ticker_history(ticker, end_date=config.end_date)
        for ticker in tickers
    }
    if not any(histories.values()):
        raise ValueError("No usable price history found. Run update-prices first.")

    master_dates = strategy_data.get_trading_dates(tickers, start_date=config.start_date, end_date=config.end_date)
    if not master_dates:
        raise ValueError("No trading dates found for requested range. Run update-prices first.")

    portfolio = PortfolioManager(config.starting_capital, config.maximum_positions)
    pending_orders: List[Order] = []
    snapshots: List[PortfolioSnapshot] = []
    running_peak = config.starting_capital
    last_date = master_dates[-1]

    for current_date in master_dates:
        today_orders = [order for order in pending_orders if order.execution_date == current_date]
        pending_orders = [order for order in pending_orders if order.execution_date != current_date]

        for order in sorted([o for o in today_orders if o.side == OrderSide.SELL], key=lambda item: item.ticker):
            row = strategy_data.get_price_on_date(order.ticker, current_date)
            if row is None or row.get("open") is None or float(row["open"]) <= 0:
                continue
            if portfolio.has_position(order.ticker):
                fill_price = order_fill_price(order, float(row["open"]), config.slippage_pct)
                portfolio.close_position(order.ticker, current_date, fill_price, config.commission_per_trade, order.reason)

        price_map = {
            ticker: close
            for ticker, close in (
                (ticker, _latest_close_on_or_before(histories[ticker], current_date)) for ticker in tickers
            )
            if close is not None
        }

        for order in sorted([o for o in today_orders if o.side == OrderSide.BUY], key=lambda item: item.ticker):
            row = strategy_data.get_price_on_date(order.ticker, current_date)
            if row is None or row.get("open") is None or float(row["open"]) <= 0:
                continue
            if portfolio.has_position(order.ticker):
                continue
            fill_price = order_fill_price(order, float(row["open"]), config.slippage_pct)
            quantity = calculate_position_quantity(
                portfolio.calculate_total_value(price_map),
                portfolio.cash,
                float(row["open"]),
                config.position_size_pct,
                config.slippage_pct,
                config.commission_per_trade,
            )
            if quantity <= 0:
                continue
            portfolio.open_position(
                order.ticker,
                order.strategy,
                quantity,
                order.signal_date,
                current_date,
                fill_price,
                config.commission_per_trade,
                order.score,
                order.reason,
            )

        price_map = {
            ticker: close
            for ticker, close in (
                (ticker, _latest_close_on_or_before(histories[ticker], current_date)) for ticker in tickers
            )
            if close is not None
        }
        portfolio.mark_to_market(price_map)

        if current_date == last_date:
            for ticker in sorted(list(portfolio.open_positions.keys())):
                close_price = _latest_close_on_or_before(histories[ticker], current_date)
                if close_price is not None:
                    fill_price = calculate_sell_fill_price(close_price, config.slippage_pct)
                    portfolio.close_position(
                        ticker,
                        current_date,
                        fill_price,
                        config.commission_per_trade,
                        "OPEN_AT_END_LIQUIDATION",
                    )
            price_map = {}
        else:
            pending_sell_tickers = _pending_tickers(pending_orders, OrderSide.SELL)
            for ticker, position in sorted(portfolio.open_positions.items()):
                current_row = _row_on_date(histories[ticker], current_date)
                if current_row is None or current_row.get("close") is None:
                    continue
                if ticker in pending_sell_tickers:
                    continue
                signal = strategy.generate_exit_signal(position, current_date, histories[ticker])
                if signal and signal.action == SignalAction.SELL:
                    execution_date, _, cancel_reason = resolve_next_execution(ticker, current_date, config.end_date)
                    if cancel_reason is None and execution_date is not None:
                        pending_orders.append(Order(ticker, OrderSide.SELL, int(position.quantity), current_date, execution_date, position.strategy, signal.score, signal.reason))
                        pending_sell_tickers.add(ticker)

            pending_buy_tickers = _pending_tickers(pending_orders, OrderSide.BUY)
            blocked_tickers = set(portfolio.open_positions.keys()) | pending_buy_tickers | _pending_tickers(pending_orders, OrderSide.SELL)
            entry_signals = []
            for ticker in tickers:
                if ticker in blocked_tickers:
                    continue
                current_row = _row_on_date(histories[ticker], current_date)
                if current_row is None or current_row.get("close") is None:
                    continue
                signal = strategy.generate_entry_signal(ticker, current_date, histories[ticker])
                if signal and signal.action == SignalAction.BUY:
                    entry_signals.append(signal)

            entry_signals.sort(key=lambda signal: (-signal.score, signal.ticker))
            for signal in entry_signals:
                projected_positions = len(portfolio.open_positions) + len(_pending_tickers(pending_orders, OrderSide.BUY))
                if projected_positions >= config.maximum_positions:
                    break
                if signal.ticker in _pending_tickers(pending_orders):
                    continue
                execution_date, _, cancel_reason = resolve_next_execution(signal.ticker, current_date, config.end_date)
                if cancel_reason is not None or execution_date is None:
                    continue
                pending_orders.append(Order(signal.ticker, OrderSide.BUY, 0, current_date, execution_date, signal.strategy, signal.score, signal.reason))

        snapshot = _snapshot(current_date, portfolio, price_map, running_peak)
        running_peak = max(running_peak, snapshot.total_value)
        snapshots.append(snapshot)

    ending_value = snapshots[-1].total_value if snapshots else config.starting_capital
    metrics = calculate_metrics(config.starting_capital, ending_value, portfolio.completed_trades, snapshots, portfolio.total_commissions)
    benchmark = None
    if benchmark_ticker:
        benchmark_history = strategy_data.get_ticker_history(benchmark_ticker.upper(), start_date=config.start_date, end_date=config.end_date)
        benchmark = calculate_benchmark_return(benchmark_history, config.start_date, config.end_date)
        metrics["benchmark"] = benchmark

    result = BacktestResult(None, config, list(portfolio.completed_trades), snapshots, metrics)
    if persist:
        return _persist_result(result, benchmark)
    return result
