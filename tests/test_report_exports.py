import csv
import json

import pytest

import reporting.exports as exports
from database.repositories import get_backtest_trades
from reporting.backtest_report import create_backtest_report
from reporting.comparison import compare_backtests
from tests.reporting_helpers import seed_backtest


def test_report_json_and_csv_exports(temp_database, tmp_path, monkeypatch):
    monkeypatch.setattr(exports, "_timestamp", lambda: "fixed")
    backtest_id = seed_backtest()
    report = create_backtest_report(backtest_id)
    files = exports.export_backtest_report(report, str(tmp_path))
    assert any(path.name.endswith("summary.json") for path in files)
    assert any(path.name.endswith("attribution.csv") for path in files)
    assert any(path.name.endswith("monthly-returns.csv") for path in files)
    assert any(path.name.endswith("trades.csv") for path in files)
    payload = json.loads([path for path in files if path.name.endswith("summary.json")][0].read_text(encoding="utf-8"))
    assert payload["backtest_id"] == backtest_id


def test_export_does_not_silently_overwrite(temp_database, tmp_path, monkeypatch):
    monkeypatch.setattr(exports, "_timestamp", lambda: "fixed")
    report = create_backtest_report(seed_backtest())
    exports.export_backtest_report(report, str(tmp_path))
    with pytest.raises(FileExistsError):
        exports.export_backtest_report(report, str(tmp_path))


def test_exports_use_stable_column_order(temp_database, tmp_path, monkeypatch):
    monkeypatch.setattr(exports, "_timestamp", lambda: "fixed")
    report = create_backtest_report(seed_backtest())
    files = exports.export_backtest_report(report, str(tmp_path))
    attribution = [path for path in files if path.name.endswith("attribution.csv")][0]
    with attribution.open(encoding="utf-8") as handle:
        header = next(csv.reader(handle))
    assert header[:3] == ["ticker", "completed_trade_count", "winning_trades"]


def test_comparison_export(temp_database, tmp_path, monkeypatch):
    monkeypatch.setattr(exports, "_timestamp", lambda: "fixed")
    comparison = compare_backtests([seed_backtest(), seed_backtest(ending=1050)])
    files = exports.export_comparison(comparison, str(tmp_path))
    assert {path.suffix for path in files} == {".json", ".csv"}


def test_experiment_export(tmp_path, monkeypatch):
    monkeypatch.setattr(exports, "_timestamp", lambda: "fixed")
    summary = {"name":"my experiment", "results":[{"parameter_set_name":"base", "development_backtest_id":1, "validation_backtest_id":2, "development_total_return":0.1, "validation_total_return":0.2, "development_maximum_drawdown":-0.1, "validation_maximum_drawdown":-0.2, "development_trade_count":4, "validation_trade_count":5, "development_win_rate":0.5, "validation_win_rate":0.6, "development_benchmark_return":0.1, "validation_benchmark_return":0.2, "development_excess_return":0, "validation_excess_return":0, "warnings":""}]}
    files = exports.export_experiment(summary, str(tmp_path))
    assert len(files) == 2


