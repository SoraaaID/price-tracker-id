"""Seed the database with sample products for testing.

Inserts 3 example products (1 per marketplace) with fake price history
so you can immediately test compare, chart, and check-fake commands.

Usage:
    python scripts/seed_products.py
"""

from __future__ import annotations

import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import load_config
from src.db import init_db, insert_history, upsert_product
from src.models import Product


SAMPLE_PRODUCTS = [
    Product(
        sku="ps--SAM-70001-00001",
        name="Samsung Galaxy S24 Ultra 256GB",
        price=18_999_000,
        original_price=21_999_000,
        marketplace="blibli",
        url="https://www.blibli.com/p/samsung-galaxy-s24-ultra/ps--SAM-70001-00001",
        store_name="Samsung Official Store",
        stock=150,
        rating=4.9,
        sold=5200,
        discount_pct=14,
        shipping_cost=0,
    ),
    Product(
        sku="i.12345.67890",
        name="iPhone 15 Pro Max 256GB Natural Titanium",
        price=22_499_000,
        original_price=24_999_000,
        marketplace="shopee",
        url="https://shopee.co.id/iPhone-15-Pro-Max-i.12345.67890",
        store_name="Apple Official Shop",
        stock=80,
        rating=4.8,
        sold=3100,
        discount_pct=10,
        shipping_cost=0,
    ),
    Product(
        sku="tokoOfficial/pixel-9-pro",
        name="Google Pixel 9 Pro 128GB Obsidian",
        price=14_999_000,
        original_price=16_999_000,
        marketplace="tokopedia",
        url="https://www.tokopedia.com/tokoOfficial/pixel-9-pro",
        store_name="Google Official Store",
        stock=45,
        rating=4.7,
        sold=890,
        discount_pct=12,
        shipping_cost=0,
    ),
]


def generate_price_history(product: Product, days: int = 30) -> list[Product]:
    """Generate realistic synthetic price history for a product.

    Creates daily data points with slight random variation around the base price.
    Includes one "flash sale" period and gradual price changes.
    """
    history = []
    base_price = product.price
    now = datetime.now(timezone.utc)

    for day in range(days, 0, -1):
        ts = now - timedelta(days=day, hours=random.randint(0, 12))

        # Normal price variation: +/- 3%
        variation = random.uniform(-0.03, 0.03)
        daily_price = base_price * (1 + variation)

        # Simulate a flash sale on days 10-12 (20% off)
        original = product.original_price
        discount = product.discount_pct
        if day in (10, 11, 12):
            daily_price = base_price * 0.80
            discount = 20.0

        p = Product(
            sku=product.sku,
            name=product.name,
            price=round(daily_price, -3),  # Round to nearest thousand
            original_price=original,
            marketplace=product.marketplace,
            url=product.url,
            store_name=product.store_name,
            stock=product.stock,
            rating=product.rating,
            sold=product.sold,
            discount_pct=discount,
            shipping_cost=product.shipping_cost,
            scraped_at=ts,
        )
        history.append(p)

    return history


def main() -> None:
    load_config()
    init_db()

    print("Seeding database with sample products...\n")

    for product in SAMPLE_PRODUCTS:
        print(f"  Adding: {product.name} ({product.marketplace})")
        upsert_product(product)

        # Generate and insert 30 days of price history
        history = generate_price_history(product)
        for h in history:
            insert_history(h)

        print(f"    -> {len(history)} price history entries created")

    # Also insert current prices
    for product in SAMPLE_PRODUCTS:
        insert_history(product)

    print(f"\nDone! {len(SAMPLE_PRODUCTS)} products seeded with price history.")
    print("\nTry these commands:")
    print(f"  python main.py compare {SAMPLE_PRODUCTS[0].sku}")
    print(f"  python main.py chart {SAMPLE_PRODUCTS[0].sku} -m blibli")
    print(f"  python main.py check-fake {SAMPLE_PRODUCTS[0].sku} -m blibli")


if __name__ == "__main__":
    main()
