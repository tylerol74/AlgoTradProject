import pytest

from portfolio.manager import PortfolioManager


def test_opening_position_deducts_correct_cash():
    portfolio = PortfolioManager(1000, 2)
    portfolio.open_position("AAPL", "test", 5, "2024-01-01", "2024-01-02", 100, 1)
    assert portfolio.cash == 499


def test_closing_position_adds_proceeds_and_commissions():
    portfolio = PortfolioManager(1000, 2)
    portfolio.open_position("AAPL", "test", 5, "2024-01-01", "2024-01-02", 100, 1)
    trade = portfolio.close_position("AAPL", "2024-01-03", 110, 2, "exit")
    assert portfolio.cash == 1047
    assert portfolio.total_commissions == 3
    assert trade.gross_pnl == 50
    assert trade.net_pnl == 47
    assert trade.return_pct == pytest.approx(47 / 501)


def test_unrealized_pnl_is_correct():
    portfolio = PortfolioManager(1000, 2)
    portfolio.open_position("AAPL", "test", 5, "2024-01-01", "2024-01-02", 100, 0)
    assert portfolio.unrealized_pnl({"AAPL": 105}) == 25


def test_duplicate_positions_are_prevented():
    portfolio = PortfolioManager(1000, 2)
    portfolio.open_position("AAPL", "test", 1, "2024-01-01", "2024-01-02", 100, 0)
    with pytest.raises(ValueError, match="duplicate"):
        portfolio.open_position("AAPL", "test", 1, "2024-01-01", "2024-01-02", 100, 0)


def test_maximum_position_count_is_enforced():
    portfolio = PortfolioManager(1000, 1)
    portfolio.open_position("AAPL", "test", 1, "2024-01-01", "2024-01-02", 100, 0)
    with pytest.raises(ValueError, match="maximum"):
        portfolio.open_position("MSFT", "test", 1, "2024-01-01", "2024-01-02", 100, 0)


def test_leverage_is_prevented():
    portfolio = PortfolioManager(1000, 2)
    with pytest.raises(ValueError, match="insufficient cash"):
        portfolio.open_position("AAPL", "test", 11, "2024-01-01", "2024-01-02", 100, 0)


def test_exit_missing_position_fails_clearly():
    portfolio = PortfolioManager(1000, 2)
    with pytest.raises(ValueError, match="no open position"):
        portfolio.close_position("AAPL", "2024-01-03", 110, 0, "exit")
