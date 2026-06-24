"""Application settings for AlgoTradProject."""

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STORAGE_DIR = PROJECT_ROOT / "data"
DATABASE_PATH = STORAGE_DIR / "algotrad.db"

DEFAULT_TEST_TICKERS = ["AAPL", "MSFT", "KO", "F", "INTC"]
DEFAULT_PRICE_HISTORY_START_DATE = "2024-01-01"
DEFAULT_BATCH_SIZE = 5
YFINANCE_AUTO_ADJUST = False
LOG_LEVEL = "INFO"

SEC_USER_AGENT = os.getenv("SEC_USER_AGENT", "").strip()
SEC_REQUEST_DELAY_SECONDS = float(os.getenv("SEC_REQUEST_DELAY_SECONDS", "0.2"))
SEC_BASE_URL = os.getenv("SEC_BASE_URL", "https://www.sec.gov").rstrip("/")
SEC_COMPANY_FACTS_URL = os.getenv(
    "SEC_COMPANY_FACTS_URL",
    "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json",
)
SEC_SUBMISSIONS_URL = os.getenv(
    "SEC_SUBMISSIONS_URL",
    "https://data.sec.gov/submissions/CIK{cik}.json",
)
SEC_COMPANY_TICKERS_URL = os.getenv(
    "SEC_COMPANY_TICKERS_URL",
    "https://www.sec.gov/files/company_tickers.json",
)
FUNDAMENTALS_DEFAULT_YEARS = int(os.getenv("FUNDAMENTALS_DEFAULT_YEARS", "5"))
FUNDAMENTALS_BATCH_SIZE = int(os.getenv("FUNDAMENTALS_BATCH_SIZE", "5"))
