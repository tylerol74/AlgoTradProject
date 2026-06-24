"""Application settings for AlgoTradProject."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STORAGE_DIR = PROJECT_ROOT / "data"
DATABASE_PATH = STORAGE_DIR / "algotrad.db"

DEFAULT_TEST_TICKERS = ["AAPL", "MSFT", "KO", "F", "INTC"]
DEFAULT_PRICE_HISTORY_START_DATE = "2024-01-01"
DEFAULT_BATCH_SIZE = 5
YFINANCE_AUTO_ADJUST = False
LOG_LEVEL = "INFO"
