"""Application settings for AlgoTradProject."""

import os
from pathlib import Path
from typing import Optional, Union

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STORAGE_DIR = PROJECT_ROOT / "data"


def resolve_database_path(value: Optional[Union[str, Path]] = None) -> Path:
    """Return the single authoritative SQLite path as an absolute path."""
    configured = Path(value or os.getenv("ALGOTRAD_DATABASE_PATH") or (STORAGE_DIR / "algotrad.db"))
    if not configured.is_absolute():
        configured = PROJECT_ROOT / configured
    return configured.expanduser().resolve()


DATABASE_PATH = resolve_database_path()

DEFAULT_TEST_TICKERS = ["AAPL", "MSFT", "KO", "F", "INTC"]
DEFAULT_PRICE_HISTORY_START_DATE = "2024-01-01"
DEFAULT_BATCH_SIZE = 5
YFINANCE_AUTO_ADJUST = False
LOG_LEVEL = "INFO"

SEC_USER_AGENT = os.getenv("SEC_USER_AGENT", "").strip()
SEC_REQUEST_DELAY_SECONDS = float(os.getenv("SEC_REQUEST_DELAY_SECONDS", "0.2"))
SEC_PROVIDER_REFRESH_INTERVAL_HOURS = int(os.getenv("SEC_PROVIDER_REFRESH_INTERVAL_HOURS", "24"))
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
