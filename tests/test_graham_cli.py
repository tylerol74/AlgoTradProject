import pytest

import main


def test_graham_cli_parses_requested_threshold_flags():
    args = main.parse_args(
        [
            "evaluate-graham",
            "AAPL",
            "--as-of",
            "2025-06-01",
            "--minimum-margin-of-safety",
            "0.4",
            "--minimum-graham-score",
            "80",
            "--minimum-data-quality-score",
            "75",
            "--minimum-profitable-years",
            "5",
            "--minimum-price",
            "5",
            "--minimum-market-cap",
            "1000000000",
            "--minimum-average-dollar-volume",
            "10000000",
            "--no-exclude-reits",
        ]
    )

    assert args.minimum_margin_of_safety == 0.4
    assert args.minimum_graham_score == 80
    assert args.minimum_data_quality_score == 75
    assert args.minimum_profitable_years == 5
    assert args.minimum_price == 5
    assert args.minimum_market_cap == 1_000_000_000
    assert args.minimum_average_dollar_volume == 10_000_000
    assert args.exclude_financials is True
    assert args.exclude_reits is False


def test_strategy_preset_commands_parse():
    assert main.parse_args(["list-strategy-presets"]).command == "list-strategy-presets"
    assert main.parse_args(["show-strategy-preset", "Moderate Graham"]).name == "Moderate Graham"
    assert main.parse_args(["export-strategy-preset", "Moderate Graham", "--output", "preset.json"]).output == "preset.json"
    assert main.parse_args(["validate-strategy-config", "preset.json"]).path == "preset.json"
