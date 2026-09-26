"""Data models for the price-tracker application.

All models are plain dataclasses — no ORM coupling.
Database layer (db.py) handles persistence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class Product:
    """Represents a product scraped from a marketplace."""

    sku: str
    name: str
    price: float
    original_price: float
    marketplace: str
    url: str
    stock: Optional[int] = None
    rating: Optional[float] = None
    sold: Optional[int] = None
    store_name: Optional[str] = None
    discount_pct: Optional[float] = None
    shipping_cost: Optional[float] = None
    scraped_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    @property
    def has_discount(self) -> bool:
        return (
            self.original_price is not None
            and self.original_price > 0
            and self.original_price > self.price
        )

    @property
    def computed_discount_pct(self) -> float:
        """Calculate discount percentage from price and original_price."""
        if not self.has_discount:
            return 0.0
        return round((1 - self.price / self.original_price) * 100, 1)


@dataclass
class PricePoint:
    """A single historical price data point for charting."""

    price: float
    original_price: float
    scraped_at: datetime
    stock: Optional[int] = None
    rating: Optional[float] = None
    sold: Optional[int] = None
    discount_pct: Optional[float] = None
    shipping_cost: Optional[float] = None


@dataclass
class Alert:
    """Alert configuration and state."""

    sku: str
    marketplace: str
    old_price: float
    new_price: float
    drop_pct: float
    sent_at: Optional[datetime] = None


@dataclass
class FakeSaleResult:
    """Result of a fake flash-sale detection check."""

    sku: str
    marketplace: str
    is_fake: bool
    reason: str
    median_normal: float
    current_price: float
    current_original_price: float
    sample_count: int
