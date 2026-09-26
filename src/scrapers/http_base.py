"""HTTP-based scraper using requests + BeautifulSoup.

Best for marketplaces that serve HTML without heavy JS rendering (e.g. Blibli).
Features: session reuse, realistic UA, exponential backoff retry, rate limiting.
"""

from __future__ import annotations

import time
from typing import Optional

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from src.config import get
from src.scrapers.base import BaseScraper, ScraperError


class HTTPBaseScraper(BaseScraper):
    """Base class for scrapers that use plain HTTP requests + BeautifulSoup.

    Handles session setup, retries, rate limiting, and common HTTP errors.
    """

    def __init__(self) -> None:
        super().__init__()
        self._session: Optional[requests.Session] = None
        self._last_request_time: float = 0.0
        self._min_interval: float = 2.0  # seconds between requests
        self._setup_session()

    def _setup_session(self) -> None:
        """Configure a requests session with retry and realistic headers."""
        self._session = requests.Session()

        # Exponential backoff retry on transient errors
        retry_strategy = Retry(
            total=get("scraper.retry", 3),
            backoff_factor=1.0,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "HEAD"],
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self._session.mount("https://", adapter)
        self._session.mount("http://", adapter)

        ua = get(
            "scraper.user_agent",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36",
        )

        self._session.headers.update(
            {
                "User-Agent": ua,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
                "Accept-Encoding": "gzip, deflate, br",
                "Connection": "keep-alive",
                "Cache-Control": "no-cache",
            }
        )

    def _rate_limit(self) -> None:
        """Enforce minimum interval between requests."""
        now = time.time()
        elapsed = now - self._last_request_time
        if elapsed < self._min_interval:
            wait = self._min_interval - elapsed
            self.log.debug("Rate limiting: sleeping %.1fs", wait)
            time.sleep(wait)
        self._last_request_time = time.time()

    def _get(self, url: str, **kwargs) -> requests.Response:  # type: ignore[no-untyped-def]
        """Perform a rate-limited GET request with error handling.

        Raises:
            ScraperError: On HTTP 403, 404, 429, or timeout.
        """
        self._rate_limit()
        timeout = get("scraper.timeout", 15)

        try:
            resp = self._session.get(url, timeout=timeout, **kwargs)  # type: ignore[union-attr]
        except requests.exceptions.Timeout:
            raise ScraperError(
                f"Timeout after {timeout}s fetching {url}",
                marketplace=self.marketplace,
                url=url,
            )
        except requests.exceptions.ConnectionError as e:
            raise ScraperError(
                f"Connection error: {e}",
                marketplace=self.marketplace,
                url=url,
            )

        if resp.status_code == 429:
            # Retry-After header is rare; wait 30s as safe default
            retry_after = int(resp.headers.get("Retry-After", "30"))
            self.log.warning("429 Rate limited. Waiting %ds before retry.", retry_after)
            time.sleep(retry_after)
            raise ScraperError(
                f"Rate limited (429) on {url}",
                marketplace=self.marketplace,
                url=url,
            )

        if resp.status_code == 403:
            raise ScraperError(
                f"Forbidden (403) — possible anti-bot block on {url}",
                marketplace=self.marketplace,
                url=url,
            )

        if resp.status_code == 404:
            raise ScraperError(
                f"Product not found (404): {url}",
                marketplace=self.marketplace,
                url=url,
            )

        if resp.status_code >= 400:
            raise ScraperError(
                f"HTTP {resp.status_code} on {url}",
                marketplace=self.marketplace,
                url=url,
            )

        return resp

    def _soup(self, url: str) -> BeautifulSoup:
        """Fetch URL and return parsed BeautifulSoup document."""
        resp = self._get(url)
        return BeautifulSoup(resp.text, "lxml")

    def close(self) -> None:
        """Close the HTTP session."""
        if self._session:
            self._session.close()
            self._session = None
