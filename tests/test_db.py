"""Tests for database operations: init, upsert, insert, query."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.db import (
    can_send_alert,
    get_all_products,
    get_history,
    get_latest,
    get_previous_price,
    get_product,
    init_db,
    insert_history,
    record_alert,
    upsert_product,
)
from src.models import Product


@pytest.fixture
def db_path(tmp_path):
    """Create a temporary database for testing."""
    path = str(tmp_path / "test.db")
    init_db(db_path=path)
    return path


@pytest.fixture
def sample_product() -> Product:
    """Create a sample product for testing."""
    return Product(
        sku="TEST-SKU-001",
        name="Test Product",
        price=500_000,
        original_price=750_000,
        marketplace="blibli",
        url="https://www.blibli.com/p/test/TEST-SKU-001",
        store_name="Test Store",
        stock=100,
        rating=4.5,
        sold=1200,
        discount_pct=33.3,
        shipping_cost=0,
        scraped_at=datetime.now(timezone.utc),
    )


class TestInitDb:
    def test_creates_tables(self, db_path):
        """init_db should create all required tables."""
        import sqlite3

        conn = sqlite3.connect(db_path)
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names = {t[0] for t in tables}
        conn.close()

        assert "products" in table_names
        assert "price_history" in table_names
        assert "alerts_sent" in table_names

    def test_idempotent(self, db_path):
        """Calling init_db twice should not error."""
        init_db(db_path=db_path)  # second call
        # If we get here, it's idempotent


class TestUpsertProduct:
    def test_insert_new(self, db_path, sample_product):
        """Inserting a new product should return a valid ID."""
        pid = upsert_product(sample_product, db_path=db_path)
        assert pid > 0

    def test_upsert_updates(self, db_path, sample_product):
        """Upserting with same SKU+marketplace should update, not duplicate."""
        pid1 = upsert_product(sample_product, db_path=db_path)

        sample_product.name = "Updated Name"
        pid2 = upsert_product(sample_product, db_path=db_path)

        assert pid1 == pid2

        product = get_product("TEST-SKU-001", "blibli", db_path=db_path)
        assert product is not None
        assert product["name"] == "Updated Name"


class TestInsertHistory:
    def test_insert(self, db_path, sample_product):
        """Inserting price history should work."""
        insert_history(sample_product, db_path=db_path)

        history = get_history("TEST-SKU-001", "blibli", days=30, db_path=db_path)
        assert len(history) == 1
        assert history[0].price == 500_000

    def test_multiple_entries(self, db_path, sample_product):
        """Multiple history entries for the same product should accumulate."""
        insert_history(sample_product, db_path=db_path)

        sample_product.price = 450_000
        sample_product.scraped_at = datetime.now(timezone.utc)
        insert_history(sample_product, db_path=db_path)

        history = get_history("TEST-SKU-001", "blibli", days=30, db_path=db_path)
        assert len(history) == 2


class TestGetHistory:
    def test_filters_by_days(self, db_path):
        """get_history should only return entries within the specified day range."""
        now = datetime.now(timezone.utc)

        # Insert entry from 5 days ago
        p1 = Product(
            sku="HIST-1", name="P1", price=100, original_price=100,
            marketplace="blibli", url="https://x.com/1",
            scraped_at=now - timedelta(days=5),
        )
        insert_history(p1, db_path=db_path)

        # Insert entry from 60 days ago
        p2 = Product(
            sku="HIST-1", name="P1", price=200, original_price=200,
            marketplace="blibli", url="https://x.com/1",
            scraped_at=now - timedelta(days=60),
        )
        insert_history(p2, db_path=db_path)

        # 30-day query should only return the recent one
        history = get_history("HIST-1", "blibli", days=30, db_path=db_path)
        assert len(history) == 1
        assert history[0].price == 100

        # 90-day query should return both
        history = get_history("HIST-1", "blibli", days=90, db_path=db_path)
        assert len(history) == 2


class TestGetLatest:
    def test_returns_latest(self, db_path):
        """get_latest should return the most recent price for each marketplace."""
        now = datetime.now(timezone.utc)

        p1 = Product(
            sku="LATEST-1", name="P", price=100, original_price=100,
            marketplace="blibli", url="https://x.com/1",
            scraped_at=now - timedelta(hours=2),
        )
        insert_history(p1, db_path=db_path)

        p2 = Product(
            sku="LATEST-1", name="P", price=90, original_price=100,
            marketplace="blibli", url="https://x.com/1",
            scraped_at=now,
        )
        insert_history(p2, db_path=db_path)

        latest = get_latest("LATEST-1", db_path=db_path)
        assert "blibli" in latest
        assert latest["blibli"].price == 90


class TestAlertRateLimit:
    def test_can_send_initially(self, db_path):
        """Should be able to send alert for a new SKU."""
        assert can_send_alert("ALERT-1", "blibli", db_path=db_path) is True

    def test_blocked_after_send(self, db_path):
        """Should be blocked after recording an alert."""
        record_alert("ALERT-1", "blibli", db_path=db_path)
        assert can_send_alert("ALERT-1", "blibli", db_path=db_path) is False

    def test_different_sku_allowed(self, db_path):
        """Different SKU should still be allowed."""
        record_alert("ALERT-1", "blibli", db_path=db_path)
        assert can_send_alert("ALERT-2", "blibli", db_path=db_path) is True


class TestGetPreviousPrice:
    def test_returns_none_for_single_entry(self, db_path, sample_product):
        """No previous price if there's only one entry."""
        insert_history(sample_product, db_path=db_path)
        prev = get_previous_price("TEST-SKU-001", "blibli", db_path=db_path)
        assert prev is None

    def test_returns_previous(self, db_path, sample_product):
        """Should return the second-latest price."""
        insert_history(sample_product, db_path=db_path)

        sample_product.price = 450_000
        sample_product.scraped_at = datetime.now(timezone.utc)
        insert_history(sample_product, db_path=db_path)

        prev = get_previous_price("TEST-SKU-001", "blibli", db_path=db_path)
        assert prev == 500_000
