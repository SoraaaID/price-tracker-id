"""Shopee scraper — Internal API primary, Playwright fallback.

Strategy:
1. Try internal API: https://shopee.co.id/api/v4/product/get?itemid={itemid}&shopid={shopid}
   - Extract itemid and shopid from the product URL.
   - Requires specific headers: User-Agent, Referer, x-api-source=pc.
   - Returns JSON with full product data.
2. Fallback: Playwright browser scraping.

Anti-bot notes:
- Shopee aggressively blocks scrapers. API may return 403 or captcha redirect.
- API endpoint has changed multiple times; last major change was 2024.
- Verify endpoint validity every 3 months.
- Browser fallback uses stealth mode but may still be detected.
- Rate limit: 3 seconds between requests (conservative).

Endpoint verification date: 2026-09-26
Previous endpoint change: 2024 (v2 -> v4)
Next verification due: 2027-01-01 (every 3 months)
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests

from src.config import get
from src.models import Product
from src.scrapers.base import ScraperError
from src.scrapers.browser_base import BrowserBaseScraper
from src.utils import parse_price, parse_sold, setup_logger


class ShopeeScraper(BrowserBaseScraper):
    """Scraper for Shopee.co.id products.

    Primary: Internal product API (v4).
    Fallback: Playwright DOM scraping.
    """

    marketplace = "shopee"

    def __init__(self) -> None:
        super().__init__()
        self._min_interval = 3.0  # Conservative rate limit
        self._http_session = requests.Session()
        ua = get(
            "scraper.user_agent",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36",
        )
        self._http_session.headers.update(
            {
                "User-Agent": ua,
                "Accept": "application/json",
                "Accept-Language": "id-ID,id;q=0.9",
                "Referer": "https://shopee.co.id/",
                # Required by Shopee API — without this header, 403 is returned
                "x-api-source": "pc",
                "x-shopee-language": "id",
                "x-requested-with": "XMLHttpRequest",
            }
        )

    def _extract_ids(self, url: str) -> dict:
        """Extract shopid and itemid from Shopee URL.

        URL patterns:
            https://shopee.co.id/Product-Name-i.{shopid}.{itemid}
            https://shopee.co.id/product/{shopid}/{itemid}
            Also handles query params: ?shopid=...&itemid=...
        """
        # Pattern 1: i.{shopid}.{itemid} in path
        m = re.search(r"-i\.(\d+)\.(\d+)", url)
        if m:
            return {"shopid": m.group(1), "itemid": m.group(2)}

        # Pattern 2: /product/{shopid}/{itemid}
        m = re.search(r"/product/(\d+)/(\d+)", url)
        if m:
            return {"shopid": m.group(1), "itemid": m.group(2)}

        # Pattern 3: query params
        parsed = urlparse(url)
        from urllib.parse import parse_qs
        qs = parse_qs(parsed.query)
        if "shopid" in qs and "itemid" in qs:
            return {"shopid": qs["shopid"][0], "itemid": qs["itemid"][0]}

        raise ScraperError(
            f"Cannot extract shopid/itemid from Shopee URL: {url}",
            marketplace=self.marketplace,
            url=url,
        )

    def fetch_product(self, url: str) -> Product:
        """Scrape a Shopee product.

        Tries internal API first, falls back to Playwright on failure.
        """
        self.log.info("Fetching Shopee product: %s", url)
        ids = self._extract_ids(url)

        # Try API first if configured
        use_api = get("scraper.shopee.use_api_first", True)
        if use_api:
            try:
                return self._fetch_api(url, ids)
            except Exception as e:
                self.log.warning("Shopee API failed, falling back to Playwright: %s", e)

        # Fallback: Playwright
        return self._fetch_browser(url, ids)

    def _fetch_api(self, url: str, ids: dict) -> Product:
        """Fetch product data via Shopee's internal API.

        Endpoint: GET /api/v4/product/get?itemid={itemid}&shopid={shopid}
        # Endpoint last verified: 2026-09-26
        # Previous major change: 2024 (v2 -> v4 migration)
        """
        api_base = get("scraper.shopee.api_base", "https://shopee.co.id/api/v4")
        api_url = f"{api_base}/product/get"

        self.log.debug("Trying Shopee API: itemid=%s, shopid=%s", ids["itemid"], ids["shopid"])
        time.sleep(self._min_interval)

        try:
            resp = self._http_session.get(
                api_url,
                params={"itemid": ids["itemid"], "shopid": ids["shopid"]},
                timeout=get("scraper.timeout", 15),
            )
        except requests.exceptions.RequestException as e:
            raise ScraperError(
                f"Shopee API request failed: {e}",
                marketplace=self.marketplace,
                url=url,
            ) from e

        if resp.status_code == 403:
            raise ScraperError(
                "Shopee API returned 403 — anti-bot triggered. "
                "Try again later or use browser fallback.",
                marketplace=self.marketplace,
                url=url,
            )

        if resp.status_code != 200:
            raise ScraperError(
                f"Shopee API returned HTTP {resp.status_code}",
                marketplace=self.marketplace,
                url=url,
            )

        try:
            data = resp.json()
        except json.JSONDecodeError as e:
            raise ScraperError(
                f"Shopee API returned invalid JSON: {e}",
                marketplace=self.marketplace,
                url=url,
            ) from e

        # Shopee API wraps data in {"data": {...}, "error": 0, ...}
        if data.get("error") and data.get("error") != 0:
            error_msg = data.get("error_msg", "Unknown API error")
            raise ScraperError(
                f"Shopee API error: {error_msg}",
                marketplace=self.marketplace,
                url=url,
            )

        item_data = data.get("data", {})
        if not item_data:
            raise ScraperError(
                "Shopee API returned empty data — item may not exist",
                marketplace=self.marketplace,
                url=url,
            )

        return self._parse_api(item_data, url, ids)

    def _parse_api(self, data: dict, url: str, ids: dict) -> Product:
        """Parse product from Shopee API JSON response.

        # JSON paths verified: 2026-09-26
        # Shopee API returns prices in micro-units (price * 100000)
        """
        # Shopee stores prices as price_max / price_min * 100000
        # Decision: use price_min as the current price (shows lowest variant price)
        price_raw = data.get("price_min", data.get("price", 0))
        # Shopee API prices are in units of 100000 (5 zeros)
        price = price_raw / 100_000 if price_raw > 100_000 else float(price_raw)

        orig_raw = data.get("price_before_discount", data.get("price_max_before_discount", 0))
        original_price = orig_raw / 100_000 if orig_raw > 100_000 else float(orig_raw)
        if original_price == 0:
            original_price = price

        name = data.get("name", "Unknown")
        stock = data.get("stock", None)
        sold = data.get("historical_sold", data.get("sold", None))

        # Rating: Shopee stores as item_rating.rating_star (float)
        rating_data = data.get("item_rating", {})
        rating = rating_data.get("rating_star", None)
        if rating is not None:
            rating = round(float(rating), 1)

        # Store name from shop_info
        shop_info = data.get("shop_info", {})
        store_name = shop_info.get("shop_name", shop_info.get("username", None))

        # Discount
        discount_pct = None
        discount_str = data.get("raw_discount", data.get("discount", ""))
        if discount_str:
            try:
                discount_pct = float(str(discount_str).replace("%", ""))
            except ValueError:
                pass

        # Shipping — not directly available in product API; would need separate call
        shipping_cost = None

        sku = f"i.{ids['shopid']}.{ids['itemid']}"

        return Product(
            sku=sku,
            name=name,
            price=price,
            original_price=original_price,
            stock=stock,
            rating=rating,
            sold=sold,
            store_name=store_name,
            discount_pct=discount_pct,
            shipping_cost=shipping_cost,
            marketplace=self.marketplace,
            url=url,
            scraped_at=datetime.now(timezone.utc),
        )

    def _fetch_browser(self, url: str, ids: dict) -> Product:
        """Fallback: scrape Shopee product page with Playwright."""
        self.log.info("Using Playwright fallback for Shopee: %s", url)
        self._rate_limit()
        page = self._new_page()

        try:
            page.goto(url, wait_until="domcontentloaded")

            # Shopee SPA takes time to render  # verified 2026-09-26
            try:
                page.wait_for_selector(
                    ".product-briefing",  # verified 2026-09-26
                    timeout=15000,
                )
            except Exception:
                try:
                    page.wait_for_selector(
                        "[class*='product']",
                        timeout=10000,
                    )
                except Exception:
                    self._screenshot_on_error(page, url)
                    raise ScraperError(
                        "Shopee page did not load — possible bot detection or captcha",
                        marketplace=self.marketplace,
                        url=url,
                    )

            page.wait_for_timeout(3000)  # Extra wait for SPA hydration
            return self._parse_browser(page, url, ids)

        except ScraperError:
            raise
        except Exception as e:
            self._screenshot_on_error(page, url)
            raise ScraperError(
                f"Playwright scrape failed for Shopee: {e}",
                marketplace=self.marketplace,
                url=url,
            ) from e
        finally:
            page.close()

    def _parse_browser(self, page, url: str, ids: dict) -> Product:
        """Extract product data from the Shopee DOM."""

        # --- Product name ---
        # Selector: .product-briefing span[class*="title"]  # verified 2026-09-26
        name = page.evaluate("""() => {
            const el = document.querySelector('.product-briefing span[class*="VfMdOO"]')
                || document.querySelector('.product-briefing h1')
                || document.querySelector('[class*="product-title"]')
                || document.querySelector('h1');
            return el ? el.textContent.trim() : 'Unknown';
        }""")

        # --- Current price ---
        # Shopee shows price in a div with specific class  # verified 2026-09-26
        price_text = page.evaluate("""() => {
            const el = document.querySelector('.product-briefing [class*="pqTWkA"]')
                || document.querySelector('.product-briefing [class*="price"]')
                || document.querySelector('[class*="product-price"]');
            return el ? el.textContent.trim() : '0';
        }""")
        price = parse_price(price_text)

        if price == 0.0:
            self._screenshot_on_error(page, url)
            raise ScraperError(
                "Could not extract price from Shopee page",
                marketplace=self.marketplace,
                url=url,
            )

        # --- Original price ---
        orig_text = page.evaluate("""() => {
            const el = document.querySelector('.product-briefing [class*="original-price"]')
                || document.querySelector('[class*="before-discount"]')
                || document.querySelector('del');
            return el ? el.textContent.trim() : '';
        }""")
        original_price = parse_price(orig_text) if orig_text else price

        # --- Discount ---
        discount_text = page.evaluate("""() => {
            const el = document.querySelector('.product-briefing [class*="discount"]')
                || document.querySelector('[class*="percent"]');
            return el ? el.textContent.trim() : '';
        }""")
        discount_pct = None
        if discount_text:
            digits = re.sub(r"[^\d]", "", discount_text)
            if digits:
                discount_pct = float(digits)

        # --- Rating ---
        rating_text = page.evaluate("""() => {
            const el = document.querySelector('.product-briefing [class*="rating"]')
                || document.querySelector('[class*="product-rating"]');
            return el ? el.textContent.trim() : '';
        }""")
        rating = None
        if rating_text:
            m = re.search(r"[\d.]+", rating_text)
            if m:
                try:
                    rating = float(m.group())
                except ValueError:
                    pass

        # --- Sold ---
        sold_text = page.evaluate("""() => {
            const el = document.querySelector('.product-briefing [class*="sold"]')
                || document.querySelector('[class*="historical-sold"]');
            return el ? el.textContent.trim() : '';
        }""")
        sold = parse_sold(sold_text) if sold_text else None

        # --- Store name ---
        store_name = page.evaluate("""() => {
            const el = document.querySelector('[class*="shop-name"]')
                || document.querySelector('[class*="seller-name"]')
                || document.querySelector('[class*="shop"] a');
            return el ? el.textContent.trim() : '';
        }""")
        store_name = store_name if store_name else None

        sku = f"i.{ids['shopid']}.{ids['itemid']}"

        return Product(
            sku=sku,
            name=name,
            price=price,
            original_price=original_price,
            stock=None,
            rating=rating,
            sold=sold,
            store_name=store_name,
            discount_pct=discount_pct,
            shipping_cost=None,
            marketplace=self.marketplace,
            url=url,
            scraped_at=datetime.now(timezone.utc),
        )

    def close(self) -> None:
        """Close both HTTP session and browser."""
        self._http_session.close()
        super().close()
