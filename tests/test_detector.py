"""Tests for fake flash sale detector using synthetic data."""

from __future__ import annotations

import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

from src.db import init_db, insert_history
from src.detector import detect_fake_flash_sale
from src.models import Product


@pytest.fixture
def db_path(tmp_path):
    """Create a temporary database for testing."""
    path = str(tmp_path / "test.db")
    init_db(db_path=path)
    return path


def _insert_history_days(
    sku: str,
    marketplace: str,
    prices: list[tuple[float, float, float | None]],
    db_path: str,
) -> None:
    """Insert synthetic price history.

    Args:
        prices: List of (price, original_price, discount_pct) tuples,
                one per day going backwards from today.
    """
    now = datetime.now(timezone.utc)
    for i, (price, orig, disc) in enumerate(prices):
        product = Product(
            sku=sku,
            name="Test Product",
            price=price,
            original_price=orig,
            marketplace=marketplace,
            url=f"https://example.com/{sku}",
            discount_pct=disc,
            scraped_at=now - timedelta(days=len(prices) - i),
        )
        insert_history(product, db_path=db_path)


class TestDetectFakeFlashSale:
    """Test the fake flash sale detection algorithm."""

    def test_genuine_sale(self, db_path, monkeypatch):
        """A real discount where the sale price is significantly below median."""
        # Monkeypatch db functions to use our test db
        import src.detector as det
        import src.db as db_mod

        original_get_history = db_mod.get_history
        original_get_latest = db_mod.get_latest

        def patched_get_history(sku, marketplace, days=30):
            return original_get_history(sku, marketplace, days, db_path=db_path)

        def patched_get_latest(sku):
            return original_get_latest(sku, db_path=db_path)

        monkeypatch.setattr(det, "get_history", patched_get_history)
        monkeypatch.setattr(det, "get_latest", patched_get_latest)

        # 30 days of normal prices around 1_000_000
        prices = [(1_000_000, 1_000_000, None)] * 28
        # Then a genuine flash sale: 50% off
        prices.append((500_000, 1_000_000, 50.0))
        prices.append((500_000, 1_000_000, 50.0))

        _insert_history_days("TEST-GENUINE", "blibli", prices, db_path)

        result = detect_fake_flash_sale("TEST-GENUINE", "blibli")
        assert result.is_fake is False
        assert result.sample_count > 0

    def test_fake_sale(self, db_path, monkeypatch):
        """A fake sale where original price is inflated but actual price is normal."""
        import src.detector as det
        import src.db as db_mod

        original_get_history = db_mod.get_history
        original_get_latest = db_mod.get_latest

        def patched_get_history(sku, marketplace, days=30):
            return original_get_history(sku, marketplace, days, db_path=db_path)

        def patched_get_latest(sku):
            return original_get_latest(sku, db_path=db_path)

        monkeypatch.setattr(det, "get_history", patched_get_history)
        monkeypatch.setattr(det, "get_latest", patched_get_latest)

        # 28 days of normal prices around 1_000_000
        prices = [(1_000_000, 1_000_000, None)] * 28
        # Then a "flash sale" with inflated original price but same actual price
        # original_price = 3_000_000 (>2x median), price = 1_000_000 (= median)
        prices.append((1_000_000, 3_000_000, 67.0))
        prices.append((1_000_000, 3_000_000, 67.0))

        _insert_history_days("TEST-FAKE", "blibli", prices, db_path)

        result = detect_fake_flash_sale("TEST-FAKE", "blibli")
        assert result.is_fake is True
        assert "FAKE" in result.reason
        assert result.median_normal > 0

    def test_no_history(self, db_path, monkeypatch):
        """No price history should return is_fake=False."""
        import src.detector as det
        import src.db as db_mod

        def patched_get_history(sku, marketplace, days=30):
            return db_mod.get_history(sku, marketplace, days, db_path=db_path)

        def patched_get_latest(sku):
            return db_mod.get_latest(sku, db_path=db_path)

        monkeypatch.setattr(det, "get_history", patched_get_history)
        monkeypatch.setattr(det, "get_latest", patched_get_latest)

        result = detect_fake_flash_sale("NONEXISTENT", "blibli")
        assert result.is_fake is False
        assert result.sample_count == 0

    def test_insufficient_data(self, db_path, monkeypatch):
        """Too few data points should return is_fake=False."""
        import src.detector as det
        import src.db as db_mod

        def patched_get_history(sku, marketplace, days=30):
            return db_mod.get_history(sku, marketplace, days, db_path=db_path)

        def patched_get_latest(sku):
            return db_mod.get_latest(sku, db_path=db_path)

        monkeypatch.setattr(det, "get_history", patched_get_history)
        monkeypatch.setattr(det, "get_latest", patched_get_latest)

        # Only 2 data points
        prices = [(1_000_000, 1_000_000, None)] * 2
        _insert_history_days("TEST-FEW", "blibli", prices, db_path)

        result = detect_fake_flash_sale("TEST-FEW", "blibli")
        assert result.is_fake is False
        assert "Insufficient" in result.reason
