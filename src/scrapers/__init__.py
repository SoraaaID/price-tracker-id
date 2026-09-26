from src.scrapers.base import BaseScraper, ScraperError
from src.scrapers.http_base import HTTPBaseScraper
from src.scrapers.browser_base import BrowserBaseScraper
from src.scrapers.blibli import BlibliScraper
from src.scrapers.lazada import LazadaScraper
from src.scrapers.tokopedia import TokopediaScraper
from src.scrapers.shopee import ShopeeScraper

SCRAPER_MAP = {
    "blibli": BlibliScraper,
    "lazada": LazadaScraper,
    "tokopedia": TokopediaScraper,
    "shopee": ShopeeScraper,
}

__all__ = [
    "BaseScraper",
    "ScraperError",
    "HTTPBaseScraper",
    "BrowserBaseScraper",
    "BlibliScraper",
    "LazadaScraper",
    "TokopediaScraper",
    "ShopeeScraper",
    "SCRAPER_MAP",
]
