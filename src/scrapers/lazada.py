"""Lazada scraper — Playwright required with cookie injection.

Lazada pakai Cloudflare + reCAPTCHA challenge. Playwright biasa kena block.
Solusi: inject cookie session dari browser manual (yang sudah lewat CAPTCHA).

Setup cookie:
1. Buka https://www.lazada.co.id di Chrome manual
2. Selesaikan CAPTCHA sekali
3. Export cookies pakai extension Cookie-Editor (Chrome Web Store)
4. Simpan sebagai input/lazada_cookies.json

Anti-bot notes:
- Cloudflare challenge + reCAPTCHA muncul intermittently.
- Cookie dari browser manual bypass CAPTCHA karena sesi sudah verified.
- Rate limit: minimum 3 detik antar page load.
- Price element kadang butuh >20s untuk render.

Selector verification date: 2026-09-26
Next verification due: 2026-12-26 (every 3 months)
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from src.models import Product
from src.scrapers.base import ScraperError
from src.scrapers.browser_base import BrowserBaseScraper
from src.utils import parse_price, parse_sold


COOKIES_PATH = Path("input/lazada_cookies.json")


class LazadaScraper(BrowserBaseScraper):
    """Scraper for Lazada.co.id products using Playwright + cookie injection."""

    marketplace = "lazada"

    # Selector wait — v2 revamp, verified 2026-09-26
    PRICE_WAIT_SELECTOR = ".pdp-v2-product-price-content-salePrice-amount"

    # Timeout (ms)
    FIRST_WAIT_TIMEOUT = 45000
    RETRY_WAIT_TIMEOUT = 30000

    def __init__(self) -> None:
        super().__init__()
        self._min_interval = 3.0
        self._cookies = self._load_cookies()

    def _load_cookies(self) -> list[dict]:
        """Load cookies dari input/lazada_cookies.json kalau ada.

        Normalize field supaya compatible dengan Playwright:
        - sameSite: harus Strict | Lax | None
        - secure, httpOnly: bool
        - domain, path: string
        """
        if not COOKIES_PATH.exists():
            self.log.warning(
                "No Lazada cookies found at %s — CAPTCHA likely to appear",
                COOKIES_PATH,
            )
            return []

        try:
            data = json.loads(COOKIES_PATH.read_text(encoding="utf-8"))
            if not isinstance(data, list):
                self.log.warning("Cookies file is not a JSON array")
                return []

            out: list[dict] = []
            for c in data:
                if not isinstance(c, dict):
                    continue
                name = c.get("name")
                value = c.get("value")
                if not name or value is None:
                    continue

                # sameSite: normalize ke Strict | Lax | None
                ss = c.get("sameSite")
                if ss not in ("Strict", "Lax", "None"):
                    ss = "Lax"

                out.append({
                    "name": name,
                    "value": str(value),
                    "domain": c.get("domain", ".lazada.co.id"),
                    "path": c.get("path", "/"),
                    "secure": bool(c.get("secure", True)),
                    "httpOnly": bool(c.get("httpOnly", False)),
                    "sameSite": ss,
                })

            self.log.info("Loaded %d Lazada cookies", len(out))
            return out

        except Exception as e:
            self.log.warning("Failed to load Lazada cookies: %s", e)
            return []

    def _extract_sku(self, url: str) -> str:
        """Extract SKU/product ID from Lazada URL."""
        m = re.search(r"-i(\d+)", url)
        if m:
            return m.group(1)
        path = urlparse(url).path.rstrip("/").split("/")[-1]
        return path.split(".")[0] if path else url

    def fetch_product(self, url: str) -> Product:
        """Scrape a Lazada product page using Playwright + cookies."""
        self.log.info("Fetching Lazada product: %s", url)
        self._rate_limit()
        sku = self._extract_sku(url)

        # Inject cookies ke context sebelum page dibuat
        if self._cookies:
            try:
                self._context.add_cookies(self._cookies)
                self.log.info("Injected %d Lazada cookies", len(self._cookies))
            except Exception as e:
                self.log.warning("Failed to inject cookies: %s", e)

        page = self._new_page()

        try:
            page.goto(url, wait_until="domcontentloaded")

            # --- Attempt 1: wait for price element ---
            found = self._wait_for_price(page)

            # --- Attempt 2: reload once if not found ---
            if not found:
                self.log.warning(
                    "Price element not found after %ds, reloading...",
                    self.FIRST_WAIT_TIMEOUT // 1000,
                )
                try:
                    page.reload(wait_until="domcontentloaded")
                except Exception as e:
                    self.log.warning("Reload failed: %s", e)
                found = self._wait_for_price(page, timeout=self.RETRY_WAIT_TIMEOUT)

            # --- Give up if still not found ---
            if not found:
                self._screenshot_on_error(page, url)
                raise ScraperError(
                    "Lazada price element not found after retry — "
                    "possible CAPTCHA challenge or page layout change",
                    marketplace=self.marketplace,
                    url=url,
                )

            # Small pause to let dynamic content settle
            page.wait_for_timeout(1500)

            product = self._parse_page(page, url, sku)
            self.log.info(
                "Scraped %s: %s — Rp %.0f",
                sku, product.name, product.price,
            )
            return product

        except ScraperError:
            raise
        except Exception as e:
            self._screenshot_on_error(page, url)
            raise ScraperError(
                f"Failed to scrape Lazada product: {e}",
                marketplace=self.marketplace,
                url=url,
            ) from e
        finally:
            page.close()

    def _wait_for_price(self, page, timeout: int | None = None) -> bool:
        """Wait for price element to appear. Return True if found."""
        if timeout is None:
            timeout = self.FIRST_WAIT_TIMEOUT
        try:
            page.wait_for_selector(self.PRICE_WAIT_SELECTOR, timeout=timeout)
            return True
        except Exception:
            return False

    def _parse_page(self, page, url: str, sku: str) -> Product:
        """Extract product data from Lazada page DOM (v2 revamp, 2026-09)."""

        # --- Nama produk ---
        name = page.evaluate("""() => {
            const el = document.querySelector('h1.pdp-mod-product-badge-title-v2')
                || document.querySelector('.pdp-mod-product-badge-title')
                || document.querySelector('h1');
            return el ? el.textContent.trim() : 'Unknown';
        }""")

        # --- Harga jual ---
        price_text = page.evaluate("""() => {
            const el = document.querySelector('.pdp-v2-product-price-content-salePrice-amount');
            return el ? el.textContent.trim() : '';
        }""")
        price = parse_price(price_text)

        if price == 0.0:
            self._screenshot_on_error(page, url)
            raise ScraperError(
                "Could not extract price from Lazada page",
                marketplace=self.marketplace,
                url=url,
            )

        # --- Harga asli ---
        orig_text = page.evaluate("""() => {
            const el = document.querySelector('.pdp-v2-product-price-content-originalPrice-amount');
            return el ? el.textContent.trim() : '';
        }""")
        original_price = parse_price(orig_text) if orig_text else price

        # --- Diskon ---
        discount_text = page.evaluate("""() => {
            const el = document.querySelector('.pdp-v2-product-price-content-originalPrice-discount');
            return el ? el.textContent.trim() : '';
        }""")
        discount_pct = None
        if discount_text:
            disc_digits = re.sub(r"[^\d]", "", discount_text)
            if disc_digits:
                discount_pct = float(disc_digits)

        # --- Rating ---
        rating_text = page.evaluate("""() => {
            const el = document.querySelector('.container-star-v2-score');
            return el ? el.textContent.trim() : '';
        }""")
        rating = None
        if rating_text:
            try:
                rating = float(rating_text)
            except ValueError:
                pass

        # --- Toko ---
        store_name = page.evaluate("""() => {
            const el = document.querySelector('.seller-name-v2__detail-name');
            return el ? el.textContent.trim() : '';
        }""")
        store_name = store_name if store_name else None

        # --- Stok & sold ---
        stock = None
        sold = None

        sold_text = page.evaluate("""() => {
            const els = document.querySelectorAll('.selling-tag-text');
            for (const el of els) {
                const t = el.textContent.trim();
                if (t.includes('Terjual')) return t;
            }
            return '';
        }""")
        if sold_text:
            sold = parse_sold(sold_text)

        # --- Shipping ---
        shipping_text = page.evaluate("""() => {
            const el = document.querySelector('.delivery__option-desc');
            return el ? el.textContent.trim() : '';
        }""")
        shipping_cost = None
        if shipping_text:
            if "gratis" in shipping_text.lower() or "free" in shipping_text.lower():
                shipping_cost = 0.0
            else:
                shipping_cost = parse_price(shipping_text)

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