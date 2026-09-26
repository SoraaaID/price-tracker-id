"""Browser-based scraper using Playwright + stealth.

Required for marketplaces with heavy JS rendering or anti-bot.
Uses playwright-stealth (v2+) to bypass basic bot detection.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Optional

from src.config import get, project_root
from src.scrapers.base import BaseScraper, ScraperError

_playwright_imported = False


def _ensure_playwright():
    global _playwright_imported
    if not _playwright_imported:
        try:
            from playwright.sync_api import sync_playwright  # noqa: F401
            from playwright_stealth import Stealth  # noqa: F401
            _playwright_imported = True
        except ImportError as e:
            raise ImportError(
                "Playwright not installed. Run: pip install playwright playwright-stealth && playwright install chromium"
            ) from e


class BrowserBaseScraper(BaseScraper):
    """Base class for scrapers that need a real browser."""

    def __init__(self) -> None:
        super().__init__()
        _ensure_playwright()
        self._playwright = None
        self._browser = None
        self._context = None
        self._last_request_time: float = 0.0
        self._min_interval: float = 3.0
        self._setup_browser()

    def _setup_browser(self) -> None:
        """Launch a Chromium browser with stealth settings."""
        from playwright.sync_api import sync_playwright

        headless = get("scraper.browser.headless", False)
        timeout = get("scraper.browser.timeout", 60000)

        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(
            headless=headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )
        self._context = self._browser.new_context(
            viewport={"width": 1366, "height": 768},
            locale="id-ID",
            timezone_id="Asia/Jakarta",
            user_agent=get(
                "scraper.user_agent",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36",
            ),
        )
        self._context.set_default_timeout(timeout)

    def _new_page(self):
        """Create a new page with stealth mode enabled (playwright-stealth v2+)."""
        from playwright_stealth import Stealth

        if not self._context:
            raise ScraperError(
                "Browser context not initialized", marketplace=self.marketplace
            )

        page = self._context.new_page()
        Stealth().apply_stealth_sync(page)
        return page

    def _rate_limit(self) -> None:
        """Enforce minimum interval between page navigations."""
        now = time.time()
        elapsed = now - self._last_request_time
        if elapsed < self._min_interval:
            wait = self._min_interval - elapsed
            self.log.debug("Rate limiting: sleeping %.1fs", wait)
            time.sleep(wait)
        self._last_request_time = time.time()

    def _screenshot_on_error(self, page, url: str) -> Optional[str]:
        """Save a screenshot when scraping fails."""
        if not get("scraper.browser.screenshot_on_error", True):
            return None

        try:
            screenshots_dir = project_root() / "data" / "screenshots"
            screenshots_dir.mkdir(parents=True, exist_ok=True)

            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            safe_name = url.split("//")[-1].split("?")[0].replace("/", "_")[:80]
            filename = f"{self.marketplace}_{safe_name}_{ts}.png"
            filepath = screenshots_dir / filename

            page.screenshot(path=str(filepath), full_page=True)
            self.log.info("Error screenshot saved: %s", filepath)
            return str(filepath)
        except Exception as e:
            self.log.warning("Failed to save error screenshot: %s", e)
            return None

    def _goto_and_capture_json(self, url: str, url_pattern: str,
                               timeout: int = 30000) -> Optional[dict]:
        """Open URL and capture JSON response matching url_pattern."""
        page = self._new_page()
        captured: list[dict] = []

        def on_response(response):
            if url_pattern in response.url:
                try:
                    body = response.json()
                    captured.append(body)
                    self.log.debug("Captured JSON from %s", response.url)
                except Exception:
                    pass

        page.on("response", on_response)

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout)
            deadline = time.time() + (timeout / 1000)
            while time.time() < deadline and not captured:
                page.wait_for_timeout(500)
        except Exception as e:
            self.log.warning("Navigation failed: %s", e)
        finally:
            page.close()

        return captured[0] if captured else None

    def close(self) -> None:
        """Shut down browser and Playwright."""
        try:
            if self._context:
                self._context.close()
                self._context = None
            if self._browser:
                self._browser.close()
                self._browser = None
            if self._playwright:
                self._playwright.stop()
                self._playwright = None
        except Exception as e:
            self.log.warning("Error during browser cleanup: %s", e)