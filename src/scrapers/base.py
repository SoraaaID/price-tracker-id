"""Abstract base class for all scrapers.

Every marketplace scraper inherits from BaseScraper and implements fetch_product().
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from src.models import Product
from src.utils import setup_logger


class ScraperError(Exception):
    """Raised when a scraper fails to fetch or parse product data."""

    def __init__(self, message: str, marketplace: str = "", url: str = ""):
        self.marketplace = marketplace
        self.url = url
        super().__init__(message)


class BaseScraper(ABC):
    """Abstract base for all marketplace scrapers.

    Supports context manager protocol for resource cleanup.
    """

    marketplace: str = ""

    def __init__(self) -> None:
        self.log = setup_logger(
            f"scraper.{self.marketplace}", f"data/logs/{self.marketplace}.log"
        )

    @abstractmethod
    def fetch_product(self, url: str) -> Product:
        """Scrape a single product page and return structured data.

        Args:
            url: Full product URL on the marketplace.

        Returns:
            Product dataclass with all available fields populated.

        Raises:
            ScraperError: If the page cannot be fetched or parsed.
        """
        ...

    def close(self) -> None:
        """Release resources (sessions, browsers, etc.). Override in subclass."""
        pass

    def __enter__(self) -> "BaseScraper":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:  # type: ignore[no-untyped-def]
        self.close()

    def _detect_marketplace(self, url: str) -> str:
        """Infer marketplace name from URL domain."""
        url_lower = url.lower()
        if "blibli.com" in url_lower:
            return "blibli"
        if "lazada.co.id" in url_lower:
            return "lazada"
        if "tokopedia.com" in url_lower:
            return "tokopedia"
        if "shopee.co.id" in url_lower:
            return "shopee"
        return "unknown"
