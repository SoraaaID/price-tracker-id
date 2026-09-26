"""Blibli scraper — Playwright-based with JSON response interception.

Blibli renders product data via JavaScript (React SPA). Raw HTML does not
contain prices. Instead, we open the page with a real browser and intercept
the internal API response from /backend/product-detail/products/{sku}/price-summary.

Selector verification date: 2026-09-26
Next verification due: 2026-12-26 (every 3 months)
"""

from __future__ import annotations

from datetime import datetime, timezone

from src.models import Product
from src.scrapers.base import ScraperError
from src.scrapers.browser_base import BrowserBaseScraper


class BlibliScraper(BrowserBaseScraper):
    """Scraper for Blibli.com products via JSON interception."""

    marketplace = "blibli"

    # Endpoint pattern — berubah kalau Blibli ganti struktur API
    # verified 2026-09-26
    PRICE_SUMMARY_PATTERN = "/backend/product-detail/products/"

    def __init__(self) -> None:
        super().__init__()
        self._min_interval = 3.0  # 1 navigation per 3 seconds

    def _extract_sku(self, url: str) -> str:
        """Extract SKU from Blibli URL.

        URL pattern: https://www.blibli.com/p/{slug}/{sku}
        Example: .../p/shinpo-container-box/is--RAR-70163-01416-00003
        """
        path = url.rstrip("/").split("?")[0]
        parts = path.split("/")
        if len(parts) >= 2:
            return parts[-1]
        raise ScraperError(
            f"Cannot extract SKU from Blibli URL: {url}",
            marketplace=self.marketplace,
            url=url,
        )

    def fetch_product(self, url: str) -> Product:
        """Scrape a Blibli product page via JSON interception."""
        self.log.info("Fetching Blibli product: %s", url)
        sku = self._extract_sku(url)
        self._rate_limit()

        data = self._goto_and_capture_json(
            url,
            url_pattern=self.PRICE_SUMMARY_PATTERN,
            timeout=30000,
        )

        if not data:
            raise ScraperError(
                "Could not capture price-summary JSON from Blibli page",
                marketplace=self.marketplace,
                url=url,
            )

        try:
            product = self._parse_json(data, url, sku)
            self.log.info(
                "Scraped %s: %s — Rp %.0f",
                sku, product.name, product.price,
            )
            return product
        except ScraperError:
            raise
        except Exception as e:
            raise ScraperError(
                f"Failed to parse Blibli JSON: {e}",
                marketplace=self.marketplace,
                url=url,
            ) from e

    def _parse_json(self, data: dict, url: str, sku: str) -> Product:
        """Extract product data from price-summary JSON."""
        payload = data.get("data", {})
        price_block = payload.get("price", {})
        item_block = payload.get("item", {})

        # --- Harga ---
        price = float(price_block.get("offered", 0) or 0)
        original_price = float(price_block.get("listed", 0) or 0)
        discount_pct = float(price_block.get("totalDiscount", 0) or 0)

        if price == 0:
            raise ScraperError(
                "Price is 0 in Blibli JSON — endpoint may have changed",
                marketplace=self.marketplace,
                url=url,
            )

        # --- Stok ---
        stock = item_block.get("totalStock")
        if stock is None:
            stock = item_block.get("selectedItem", {}).get("stock")

        # --- Nama produk ---
        # Tidak ada di price-summary. Blibli punya endpoint terpisah untuk detail.
        # Fallback: derive dari URL slug (sementara).
        # TODO: intercept endpoint product-detail untuk nama lengkap.
        slug = url.split("/p/")[-1].split("/")[0]
        name = slug.replace("-", " ").title()

        return Product(
            sku=sku,
            name=name,
            price=price,
            original_price=original_price,
            stock=stock,
            rating=None,       # perlu endpoint terpisah
            sold=None,         # perlu endpoint terpisah
            store_name=None,   # perlu endpoint terpisah
            discount_pct=discount_pct,
            shipping_cost=None,  # perlu endpoint terpisah
            marketplace=self.marketplace,
            url=url,
            scraped_at=datetime.now(timezone.utc),
        )