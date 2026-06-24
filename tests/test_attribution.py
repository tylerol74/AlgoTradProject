from reporting.attribution import analyze_exit_reasons, calculate_ticker_attribution


def test_ticker_attribution_net_pnl_and_contributions():
    trades = [
        {"ticker":"AAPL","entry_date":"2024-01-01","exit_date":"2024-01-03","pnl":100,"return_pct":0.1},
        {"ticker":"MSFT","entry_date":"2024-01-01","exit_date":"2024-01-04","pnl":-50,"return_pct":-0.05},
    ]
    result = calculate_ticker_attribution(trades)
    assert result["rows"][0]["ticker"] == "AAPL"
    assert result["rows"][0]["net_pnl"] == 100
    assert result["rows"][0]["contribution_to_total_pnl"] == 2


def test_concentration_warnings():
    trades = [
        {"ticker":"AAPL","entry_date":"2024-01-01","exit_date":"2024-01-03","pnl":100,"return_pct":0.1},
        {"ticker":"MSFT","entry_date":"2024-01-01","exit_date":"2024-01-04","pnl":-100,"return_pct":-0.1},
    ]
    warnings = calculate_ticker_attribution(trades)["warnings"]
    assert any("positive" in warning for warning in warnings)
    assert any("losses" in warning for warning in warnings)


def test_exit_reason_grouping_and_win_rates():
    trades = [
        {"ticker":"AAPL","entry_date":"2024-01-01","exit_date":"2024-01-03","pnl":100,"return_pct":0.1,"exit_reason":"target"},
        {"ticker":"MSFT","entry_date":"2024-01-01","exit_date":"2024-01-04","pnl":-50,"return_pct":-0.05,"exit_reason":"target"},
    ]
    rows = analyze_exit_reasons(trades)
    assert rows[0]["exit_reason"] == "target"
    assert rows[0]["trade_count"] == 2
    assert rows[0]["win_rate"] == 0.5
