"""SQLite database layer.

Schema is created lazily on first call to init_db().
All functions accept an optional db_path override for testing.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from src.config import get, project_root
from src.models import PricePoint, Product
from src.utils import setup_logger

log = setup_logger("db", "data/logs/db.log")

_CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY,
    sku TEXT NOT NULL,
    marketplace TEXT NOT NULL,
    name TEXT,
    url TEXT,
    store_name TEXT,
    UNIQUE(sku, marketplace)
);

CREATE TABLE IF NOT EXISTS price_history (
    id INTEGER PRIMARY KEY,
    product_id INTEGER REFERENCES products(id),
    price REAL,
    original_price REAL,
    stock INTEGER,
    rating REAL,
    sold INTEGER,
    discount_pct REAL,
    shipping_cost REAL,
    scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS alerts_sent (
    id INTEGER PRIMARY KEY,
    sku TEXT,
    marketplace TEXT,
    sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_ph_product_id ON price_history(product_id);
CREATE INDEX IF NOT EXISTS idx_ph_scraped_at ON price_history(scraped_at);
CREATE INDEX IF NOT EXISTS idx_alerts_sku_mp ON alerts_sent(sku, marketplace);
"""


def _db_path(override: str | None = None) -> str:
    if override:
        return override
    raw = get("db.path", "data/price_history.db")
    path = Path(raw)
    if not path.is_absolute():
        path = project_root() / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return str(path)


def _parse_datetime(val: str | datetime) -> datetime:
    """Parse datetime from database and ensure it is timezone-aware (UTC)."""
    if isinstance(val, datetime):
        dt = val
    else:
        dt = datetime.fromisoformat(val)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _connect(db_path: str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: str | None = None) -> None:
    """Create tables if they don't exist."""
    conn = _connect(db_path)
    try:
        conn.executescript(_CREATE_TABLES)
        conn.commit()
        log.info("Database initialized at %s", _db_path(db_path))
    finally:
        conn.close()


def upsert_product(product: Product, db_path: str | None = None) -> int:
    """Insert or update a product record. Returns product_id."""
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            """
            INSERT INTO products (sku, marketplace, name, url, store_name)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(sku, marketplace) DO UPDATE SET
                name = excluded.name,
                url = excluded.url,
                store_name = excluded.store_name
            """,
            (
                product.sku,
                product.marketplace,
                product.name,
                product.url,
                product.store_name,
            ),
        )
        conn.commit()
        # Fetch the id (either newly inserted or existing)
        row = conn.execute(
            "SELECT id FROM products WHERE sku = ? AND marketplace = ?",
            (product.sku, product.marketplace),
        ).fetchone()
        return row["id"]
    finally:
        conn.close()


def insert_history(product: Product, db_path: str | None = None) -> None:
    """Insert a price history entry for a product."""
    product_id = upsert_product(product, db_path)
    conn = _connect(db_path)
    try:
        scraped_at = product.scraped_at
        if scraped_at.tzinfo is None:
            scraped_at = scraped_at.replace(tzinfo=timezone.utc)
        conn.execute(
            """
            INSERT INTO price_history
                (product_id, price, original_price, stock, rating, sold,
                 discount_pct, shipping_cost, scraped_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                product_id,
                product.price,
                product.original_price,
                product.stock,
                product.rating,
                product.sold,
                product.discount_pct or product.computed_discount_pct,
                product.shipping_cost,
                scraped_at.isoformat(),
            ),
        )
        conn.commit()
        log.info(
            "Recorded price %.0f for %s on %s",
            product.price,
            product.sku,
            product.marketplace,
        )
    finally:
        conn.close()


def get_history(
    sku: str,
    marketplace: str,
    days: int = 30,
    db_path: str | None = None,
) -> list[PricePoint]:
    """Get price history for a product within the last N days."""
    conn = _connect(db_path)
    try:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        rows = conn.execute(
            """
            SELECT ph.price, ph.original_price, ph.scraped_at,
                   ph.stock, ph.rating, ph.sold, ph.discount_pct, ph.shipping_cost
            FROM price_history ph
            JOIN products p ON p.id = ph.product_id
            WHERE p.sku = ? AND p.marketplace = ? AND ph.scraped_at >= ?
            ORDER BY ph.scraped_at ASC
            """,
            (sku, marketplace, since),
        ).fetchall()

        return [
            PricePoint(
                price=row["price"],
                original_price=row["original_price"],
                scraped_at=_parse_datetime(row["scraped_at"]),
                stock=row["stock"],
                rating=row["rating"],
                sold=row["sold"],
                discount_pct=row["discount_pct"],
                shipping_cost=row["shipping_cost"],
            )
            for row in rows
        ]
    finally:
        conn.close()


def get_latest(sku: str, db_path: str | None = None) -> dict[str, PricePoint | None]:
    """Get the latest price for a SKU across all marketplaces.

    Returns:
        Dict mapping marketplace -> latest PricePoint (or None).
    """
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT p.marketplace, ph.price, ph.original_price, ph.scraped_at,
                   ph.stock, ph.rating, ph.sold, ph.discount_pct, ph.shipping_cost
            FROM price_history ph
            JOIN products p ON p.id = ph.product_id
            WHERE p.sku = ?
            AND ph.id = (
                SELECT ph2.id FROM price_history ph2
                WHERE ph2.product_id = p.id
                ORDER BY ph2.scraped_at DESC LIMIT 1
            )
            """,
            (sku,),
        ).fetchall()

        result: dict[str, PricePoint | None] = {}
        for row in rows:
            result[row["marketplace"]] = PricePoint(
                price=row["price"],
                original_price=row["original_price"],
                scraped_at=_parse_datetime(row["scraped_at"]),
                stock=row["stock"],
                rating=row["rating"],
                sold=row["sold"],
                discount_pct=row["discount_pct"],
                shipping_cost=row["shipping_cost"],
            )
        return result
    finally:
        conn.close()


def get_previous_price(
    sku: str, marketplace: str, db_path: str | None = None
) -> Optional[float]:
    """Get the second-latest price for change detection."""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT ph.price
            FROM price_history ph
            JOIN products p ON p.id = ph.product_id
            WHERE p.sku = ? AND p.marketplace = ?
            ORDER BY ph.scraped_at DESC
            LIMIT 2
            """,
            (sku, marketplace),
        ).fetchall()
        if len(rows) >= 2:
            return rows[1]["price"]
        return None
    finally:
        conn.close()


def can_send_alert(
    sku: str, marketplace: str, db_path: str | None = None
) -> bool:
    """Check if we can send an alert (rate limit: max 1 per SKU per hour)."""
    conn = _connect(db_path)
    try:
        one_hour_ago = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        row = conn.execute(
            """
            SELECT COUNT(*) as cnt FROM alerts_sent
            WHERE sku = ? AND marketplace = ? AND sent_at >= ?
            """,
            (sku, marketplace, one_hour_ago),
        ).fetchone()
        return row["cnt"] == 0
    finally:
        conn.close()


def record_alert(sku: str, marketplace: str, db_path: str | None = None) -> None:
    """Record that an alert was sent."""
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT INTO alerts_sent (sku, marketplace, sent_at) VALUES (?, ?, ?)",
            (sku, marketplace, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


def get_all_products(db_path: str | None = None) -> list[dict]:
    """Get all tracked products."""
    conn = _connect(db_path)
    try:
        rows = conn.execute("SELECT * FROM products ORDER BY marketplace, sku").fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def get_product(
    sku: str, marketplace: str, db_path: str | None = None
) -> Optional[dict]:
    """Get a single product record."""
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM products WHERE sku = ? AND marketplace = ?",
            (sku, marketplace),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()
