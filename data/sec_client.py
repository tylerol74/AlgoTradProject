"""Small SEC EDGAR JSON client with required User-Agent and retries."""

import json
import logging
import time
from typing import Any, Dict, Optional

import requests

from config import settings

logger = logging.getLogger(__name__)


class SECConfigurationError(RuntimeError):
    """Raised when required SEC client configuration is missing."""


class SECRequestError(RuntimeError):
    """Raised when an SEC request fails."""


class SECJSONError(SECRequestError):
    """Raised when an SEC response cannot be parsed as JSON."""


TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}


class SECClient:
    """HTTP client for official SEC JSON endpoints."""

    def __init__(
        self,
        user_agent: Optional[str] = None,
        request_delay_seconds: Optional[float] = None,
        timeout_seconds: float = 20.0,
        max_retries: int = 3,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.user_agent = (user_agent if user_agent is not None else settings.SEC_USER_AGENT).strip()
        if not self.user_agent or "@" not in self.user_agent:
            raise SECConfigurationError(
                "SEC_USER_AGENT is required. Set it to an application/contact string such as "
                "'AlgoTradProject/0.1 contact@example.com'."
            )
        self.request_delay_seconds = (
            settings.SEC_REQUEST_DELAY_SECONDS
            if request_delay_seconds is None
            else request_delay_seconds
        )
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.session = session or requests.Session()

    def headers(self) -> Dict[str, str]:
        """Return SEC-compliant request headers."""
        return {
            "User-Agent": self.user_agent,
            "Accept-Encoding": "gzip, deflate",
            "Accept": "application/json",
        }

    def get_json(self, url: str) -> Dict[str, Any]:
        """GET and parse a JSON URL with pacing and bounded retries."""
        last_error: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            if attempt or self.request_delay_seconds > 0:
                time.sleep(self.request_delay_seconds if attempt == 0 else min(2.0, 0.5 * (2 ** (attempt - 1))))
            try:
                response = self.session.get(url, headers=self.headers(), timeout=self.timeout_seconds)
            except requests.RequestException as exc:
                last_error = exc
                logger.warning("SEC request failed for %s: %s", url, exc)
                if attempt >= self.max_retries:
                    raise SECRequestError(f"SEC request failed for {url}: {exc}") from exc
                continue

            if response.status_code in TRANSIENT_STATUS_CODES and attempt < self.max_retries:
                logger.warning("Transient SEC status %s for %s; retrying", response.status_code, url)
                continue
            if response.status_code >= 400:
                raise SECRequestError(f"SEC request failed for {url}: HTTP {response.status_code}")
            try:
                return response.json()
            except (ValueError, json.JSONDecodeError) as exc:
                raise SECJSONError(f"SEC response from {url} was not valid JSON") from exc

        raise SECRequestError(f"SEC request failed for {url}: {last_error}")

    def get_company_facts(self, cik: str) -> Dict[str, Any]:
        """Return SEC companyfacts JSON for a zero-padded CIK."""
        return self.get_json(settings.SEC_COMPANY_FACTS_URL.format(cik=cik))

    def get_submissions(self, cik: str) -> Dict[str, Any]:
        """Return SEC submissions JSON for a zero-padded CIK."""
        return self.get_json(settings.SEC_SUBMISSIONS_URL.format(cik=cik))

    def get_company_tickers(self) -> Dict[str, Any]:
        """Return SEC ticker-to-CIK mapping JSON."""
        return self.get_json(settings.SEC_COMPANY_TICKERS_URL)
