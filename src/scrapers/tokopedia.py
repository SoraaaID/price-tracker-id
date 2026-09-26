"""Tokopedia scraper — GraphQL primary, Playwright fallback.

Strategy:
1. Try GraphQL POST to gql.tokopedia.com/graphql/ProductQuery.
   - Requires a valid operation hash (changes on Tokopedia frontend updates).
   - Hash is configured in config.toml [scraper.tokopedia].operation_hash.
   - If hash is empty or request fails, fall back to Playwright.
2. Fallback: Playwright browser scraping.

Anti-bot notes:
- Tokopedia uses a custom bot-detection layer (not Cloudflare).
- GraphQL endpoint sometimes returns 403 if UA/headers are suspicious.
- Browser fallback uses stealth mode.
- Rate limit: 3 seconds between requests.

Selector verification date: 2026-09-26
Next verification due: 2027-01-01 (every 3 months)
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests

from src.config import get, validate_tokopedia_config
from src.models import Product
from src.scrapers.base import ScraperError
from src.scrapers.browser_base import BrowserBaseScraper
from src.utils import parse_price, parse_sold, setup_logger


class TokopediaScraper(BrowserBaseScraper):
    """Scraper for Tokopedia.com products.

    Primary: GraphQL API.
    Fallback: Playwright DOM scraping.
    """

    marketplace = "tokopedia"

    def __init__(self) -> None:
        super().__init__()
        self._min_interval = 3.0
        self._http_session = requests.Session()
        self._http_session.headers.update(
            {
                "User-Agent": get(
                    "scraper.user_agent",
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36",
                ),
                "Accept": "application/json",
                "Accept-Language": "id-ID,id;q=0.9",
                "Content-Type": "application/json",
                "Origin": "https://www.tokopedia.com",
                "Referer": "https://www.tokopedia.com/",
                # Tokopedia checks these custom headers
                "x-tkpd-akamai": "pdpGetLayout",
                "X-Device": "desktop",
            }
        )

    def _extract_identifiers(self, url: str) -> dict:
        """Extract shop slug and product slug from Tokopedia URL.

        URL patterns:
            https://www.tokopedia.com/{shop_slug}/{product_slug}
            https://www.tokopedia.com/{shop_slug}/{product_slug}?extParam=...
        """
        parsed = urlparse(url)
        path_parts = [p for p in parsed.path.strip("/").split("/") if p]

        if len(path_parts) < 2:
            raise ScraperError(
                f"Cannot extract shop/product from Tokopedia URL: {url}",
                marketplace=self.marketplace,
                url=url,
            )

        return {
            "shop_slug": path_parts[0],
            "product_slug": path_parts[1],
        }

    def fetch_product(self, url: str) -> Product:
        """Scrape a Tokopedia product.

        Tries GraphQL first, falls back to Playwright on failure.
        """
        op_hash = validate_tokopedia_config(url=url)

        self.log.info("Fetching Tokopedia product: %s", url)
        ids = self._extract_identifiers(url)

        # Try GraphQL first
        try:
            return self._fetch_graphql(url, ids, op_hash)
        except Exception as e:
            self.log.warning(
                "GraphQL failed, falling back to Playwright: %s", e
            )

        # Fallback: Playwright
        return self._fetch_browser(url, ids)

    def _fetch_graphql(self, url: str, ids: dict, op_hash: str) -> Product:
        """Fetch product data via Tokopedia's GraphQL endpoint."""
        self.log.debug("Trying GraphQL for %s/%s", ids["shop_slug"], ids["product_slug"])

        gql_url = get(
            "scraper.tokopedia.graphql_url",
            "https://gql.tokopedia.com/graphql/ProductQuery",
        )

        # Tokopedia uses PDPGetLayoutQuery for product detail pages
        payload = [
            {
                "operationName": "PDPGetLayoutQuery",
                "variables": {
                    "shopDomain": ids["shop_slug"],
                    "productKey": ids["product_slug"],
                    "layoutID": "",
                    "apiVersion": 1,
                    "extParam": "",
                },
                "extensions": {
                    "persistedQuery": {
                        "version": 1,
                        "sha256Hash": op_hash,
                    }
                },
            }
        ]

        time.sleep(self._min_interval)
        resp = self._http_session.post(
            gql_url,
            json=payload,
            timeout=get("scraper.timeout", 15),
        )

        if resp.status_code == 403:
            raise ScraperError(
                "GraphQL 403 — bot detection triggered",
                marketplace=self.marketplace,
                url=url,
            )

        if resp.status_code != 200:
            raise ScraperError(
                f"GraphQL returned HTTP {resp.status_code}",
                marketplace=self.marketplace,
                url=url,
            )

        data = resp.json()

        # Response is a list; first element contains the product data
        if isinstance(data, list) and len(data) > 0:
            data = data[0]

        gql_data = data.get("data", {}).get("pdpGetLayout", {})
        if not gql_data:
            raise ScraperError(
                "Empty GraphQL response — operation hash may be outdated",
                marketplace=self.marketplace,
                url=url,
            )

        return self._parse_graphql(gql_data, url, ids)

    def _parse_graphql(self, data: dict, url: str, ids: dict) -> Product:
        """Parse product from GraphQL response structure."""

        # Navigate the nested response to find product components
        basic_info = {}
        price_info = {}

        components = data.get("components", [])
        for comp in components:
            comp_name = comp.get("name", "")
            comp_data = comp.get("data", [])

            if comp_name == "product_content" and comp_data:
                for item in comp_data:
                    if "name" in item:
                        basic_info["name"] = item["name"]
                    if "price" in item:
                        price_info = item.get("price", {})
                    if "campaign" in item:
                        campaign = item.get("campaign", {})
                        if campaign.get("originalPrice"):
                            price_info["originalPrice"] = campaign["originalPrice"]
                        if campaign.get("discountPercentage"):
                            price_info["discountPercentage"] = campaign["discountPercentage"]

            if comp_name == "product_detail" and comp_data:
                for item in comp_data:
                    if "content" in item:
                        for detail in item.get("content", []):
                            if detail.get("title") == "Dikirim dari":
                                basic_info["store_location"] = detail.get("subtitle", "")

        name = basic_info.get("name", ids["product_slug"].replace("-", " ").title())
        price_val = price_info.get("value", 0)
        price = float(price_val) if price_val else 0.0

        if price == 0.0:
            # Try the priceFmt field
            price_fmt = price_info.get("priceFmt", "")
            price = parse_price(price_fmt)

        orig_price_str = price_info.get("originalPrice", "")
        original_price = parse_price(orig_price_str) if orig_price_str else price

        discount_pct = None
        disc_raw = price_info.get("discountPercentage", 0)
        if disc_raw:
            discount_pct = float(str(disc_raw).replace("%", ""))

        # SKU: use shop_slug/product_slug as combined identifier
        sku = f"{ids['shop_slug']}/{ids['product_slug']}"

        return Product(
            sku=sku,
            name=name,
            price=price,
            original_price=original_price,
            stock=None,  # Not reliably available in GraphQL response
            rating=None,
            sold=None,
            store_name=ids["shop_slug"],
            discount_pct=discount_pct,
            shipping_cost=None,
            marketplace=self.marketplace,
            url=url,
            scraped_at=datetime.now(timezone.utc),
        )

    def _fetch_browser(self, url: str, ids: dict) -> Product:
        """Fallback: scrape Tokopedia product page with Playwright."""
        self.log.info("Using Playwright fallback for Tokopedia: %s", url)
        self._rate_limit()
        page = self._new_page()

        try:
            page.goto(url, wait_until="domcontentloaded")

            # Wait for product name or price to appear  # verified 2026-09-26
            try:
                page.wait_for_selector(
                    "[data-testid='lblPDPDetailProductName']",  # verified 2026-09-26
                    timeout=15000,
                )
            except Exception:
                try:
                    page.wait_for_selector("h1", timeout=10000)
                except Exception:
                    self._screenshot_on_error(page, url)
                    raise ScraperError(
                        "Tokopedia page did not load — possible bot detection",
                        marketplace=self.marketplace,
                        url=url,
                    )

            page.wait_for_timeout(2000)
            return self._parse_browser(page, url, ids)

        except ScraperError:
            raise
        except Exception as e:
            self._screenshot_on_error(page, url)
            raise ScraperError(
                f"Playwright scrape failed for Tokopedia: {e}",
                marketplace=self.marketplace,
                url=url,
            ) from e
        finally:
            page.close()

    def _parse_browser(self, page, url: str, ids: dict) -> Product:
        """Extract product data from the Tokopedia DOM."""

        # --- Product name ---
        # Selector: [data-testid='lblPDPDetailProductName']  # verified 2026-09-26
        name = page.evaluate("""() => {
            const el = document.querySelector("[data-testid='lblPDPDetailProductName']")
                || document.querySelector("h1[class*='title']")
                || document.querySelector("h1");
            return el ? el.textContent.trim() : 'Unknown';
        }""")

        # --- Current price ---
        # Selector: [data-testid='lblPDPDetailProductPrice']  # verified 2026-09-26
        price_text = page.evaluate("""() => {
            const el = document.querySelector("[data-testid='lblPDPDetailProductPrice']")
                || document.querySelector("[class*='price']");
            return el ? el.textContent.trim() : '0';
        }""")
        price = parse_price(price_text)

        if price == 0.0:
            self._screenshot_on_error(page, url)
            raise ScraperError(
                "Could not extract price from Tokopedia page",
                marketplace=self.marketplace,
                url=url,
            )

        # --- Original price (slash price) ---
        # Selector: [data-testid='lblPDPDetailOriginalPrice']  # verified 2026-09-26
        orig_text = page.evaluate("""() => {
            const el = document.querySelector("[data-testid='lblPDPDetailOriginalPrice']")
                || document.querySelector("[class*='slash-price']")
                || document.querySelector("[class*='original-price']");
            return el ? el.textContent.trim() : '';
        }""")
        original_price = parse_price(orig_text) if orig_text else price

        # --- Discount ---
        # Selector: [data-testid='lblPDPDetailDiscountPercentage']  # verified 2026-09-26
        discount_text = page.evaluate("""() => {
            const el = document.querySelector("[data-testid='lblPDPDetailDiscountPercentage']")
                || document.querySelector("[class*='discount']");
            return el ? el.textContent.trim() : '';
        }""")
        discount_pct = None
        if discount_text:
            digits = re.sub(r"[^\d]", "", discount_text)
            if digits:
                discount_pct = float(digits)

        # --- Rating ---
        rating_text = page.evaluate("""() => {
            const el = document.querySelector("[data-testid='lblPDPDetailProductRatingNumber']")
                || document.querySelector("[class*='rating']");
            return el ? el.textContent.trim() : '';
        }""")
        rating = None
        if rating_text:
            try:
                rating = float(rating_text)
            except ValueError:
                pass

        # --- Sold ---
        sold_text = page.evaluate("""() => {
            const el = document.querySelector("[data-testid='lblPDPDetailProductSoldCounter']")
                || document.querySelector("[class*='sold']");
            return el ? el.textContent.trim() : '';
        }""")
        sold = parse_sold(sold_text) if sold_text else None

        # --- Store name ---
        store_name = page.evaluate("""() => {
            const el = document.querySelector("[data-testid='llbPDPFooterShopName'] h2")
                || document.querySelector("[class*='shop-name']")
                || document.querySelector("[class*='store-name']");
            return el ? el.textContent.trim() : '';
        }""")
        store_name = store_name if store_name else ids["shop_slug"]

        sku = f"{ids['shop_slug']}/{ids['product_slug']}"

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
