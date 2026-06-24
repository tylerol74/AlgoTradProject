import pytest

from backtesting.models import BacktestResult, PortfolioSnapshot, Trade
from experiments.models import ExperimentConfig, ParameterSet
from experiments.runner import experiment_summary, run_experiment


def pset(name="base"):
    return ParameterSet(name, 20, 0.05, 0.10, 60, 0.20, 5, 0.001, 0)


def config(parameter_sets=None):
    return ExperimentConfig("exp", "moving_average_reversion", ["AAPL", "MSFT"], "2024-01-01", "2024-06-30", "2024-07-01", "2024-12-31", 100000, "AAPL", parameter_sets or [pset()], False)


def test_experiment_configuration_validation():
    config().validate()
    with pytest.raises(ValueError, match="overlap"):
        ExperimentConfig("bad", "s", ["AAPL"], "2024-01-01", "2024-07-01", "2024-07-01", "2024-12-31", 1, None, [pset()]).validate()
    with pytest.raises(ValueError, match="unique"):
        config([pset("x"), pset("x")]).validate()
    with pytest.raises(ValueError, match="too many"):
        config([pset(str(i)) for i in range(21)]).validate()
    with pytest.raises(ValueError, match="duplicate"):
        ExperimentConfig("bad", "s", ["AAPL", "aapl"], "2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04", 1, None, [pset()]).validate()


def fake_result(backtest_id, total_return, excess_return, trades=4):
    metrics = {
        "total_return_pct": total_return,
        "maximum_drawdown": -0.1,
        "completed_trade_count": trades,
        "win_rate": 0.5,
        "benchmark": {"return_pct": total_return - excess_return},
    }
    trade_list = [Trade("AAPL", "s", "2024-01-01", "2024-01-02", 100, "2024-01-03", 110, 1, 10, 10, 0.1, "exit") for _ in range(trades)]
    return BacktestResult(backtest_id, None, trade_list, [PortfolioSnapshot("2024-01-01", 1, 0, 1, 0)], metrics)


def test_experiment_runner_preserves_order_and_parameters(monkeypatch):
    calls = []
    results = [fake_result(1, 0.2, 0.1), fake_result(2, 0.1, 0.05), fake_result(3, 0.3, 0.2), fake_result(4, 0.2, 0.1)]
    def fake_run(config_arg, strategy, benchmark_ticker=None, persist=True):
        calls.append((config_arg.start_date, strategy.moving_average_window))
        return results[len(calls)-1]
    monkeypatch.setattr("experiments.runner.run_backtest", fake_run)
    cfg = config([pset("first"), ParameterSet("second", 30, 0.08, 0.12, 90, 0.2, 5, 0.001, 0)])
    output = run_experiment(cfg)
    assert [item.parameter_set_name for item in output] == ["first", "second"]
    assert calls == [("2024-01-01", 20), ("2024-07-01", 20), ("2024-01-01", 30), ("2024-07-01", 30)]


def test_experiment_warnings(monkeypatch):
    results = [fake_result(1, 0.2, 0.1, 4), fake_result(2, -0.1, -0.1, 1)]
    monkeypatch.setattr("experiments.runner.run_backtest", lambda *args, **kwargs: results.pop(0))
    output = run_experiment(config())
    warnings = output[0].warnings
    assert any("positive" in warning for warning in warnings)
    assert any("excess" in warning for warning in warnings)
    assert any("low" in warning for warning in warnings)


def test_experiment_summary_is_export_friendly(monkeypatch):
    results = [fake_result(1, 0.2, 0.1), fake_result(2, 0.1, 0.05)]
    monkeypatch.setattr("experiments.runner.run_backtest", lambda *args, **kwargs: results.pop(0))
    cfg = config()
    summary = experiment_summary(cfg, run_experiment(cfg))
    assert summary["results"][0]["parameter_set_name"] == "base"
    assert summary["results"][0]["development_total_return"] == 0.2
