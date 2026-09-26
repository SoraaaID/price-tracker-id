"""Fake flash sale detector.

Detects when a product's "original price" (harga coret) is artificially inflated
to make a normal price look like a deep discount.

Algorithm:
1. Get 30-day price history for the SKU + marketplace.
2. Filter out periods when a discount was active (discount_pct > 0).
   These are "normal price" data points.
3. Compute the median of normal prices.
4. A flash sale is flagged as FAKE if:
   a. current original_price > 2x median normal price, AND
   b. current actual price >= 0.95 * median normal price.
   This means the "sale price" is basically the normal price,
   and the "original price" is inflated to create a false impression.
"""

from __future__ import annotations

from statistics import median

from src.db import get_history, get_latest
from src.models import FakeSaleResult


def detect_fake_flash_sale(sku: str, marketplace: str) -> FakeSaleResult:
    """Detect whether a product's current flash sale is fake.

    Args:
        sku: Product SKU identifier.
        marketplace: Marketplace name (blibli, shopee, etc.).

    Returns:
        FakeSaleResult with detection verdict and evidence.
    """
    # Get 30-day history
    history = get_history(sku, marketplace, days=30)

    if not history:
        return FakeSaleResult(
            sku=sku,
            marketplace=marketplace,
            is_fake=False,
            reason="No price history available (need at least 30 days of data).",
            median_normal=0.0,
            current_price=0.0,
            current_original_price=0.0,
            sample_count=0,
        )

    # Get latest price data
    latest_map = get_latest(sku)
    latest = latest_map.get(marketplace)

    if not latest:
        return FakeSaleResult(
            sku=sku,
            marketplace=marketplace,
            is_fake=False,
            reason="No current price data for this marketplace.",
            median_normal=0.0,
            current_price=0.0,
            current_original_price=0.0,
            sample_count=0,
        )

    current_price = latest.price
    current_original = latest.original_price

    # Filter normal-price points: those without an active discount
    # "Normal" = discount_pct is None/0 or price == original_price
    normal_prices = []
    for point in history:
        is_discounted = (
            point.discount_pct is not None
            and point.discount_pct > 0
        ) or (
            point.original_price > 0
            and point.price < point.original_price * 0.95
        )
        if not is_discounted:
            normal_prices.append(point.price)

    if not normal_prices:
        # All historical data points were during discounts — use all prices
        normal_prices = [p.price for p in history]

    if len(normal_prices) < 3:
        return FakeSaleResult(
            sku=sku,
            marketplace=marketplace,
            is_fake=False,
            reason=f"Insufficient normal-price data points ({len(normal_prices)}). Need at least 3.",
            median_normal=median(normal_prices) if normal_prices else 0.0,
            current_price=current_price,
            current_original_price=current_original,
            sample_count=len(normal_prices),
        )

    median_normal = median(normal_prices)

    # Detection logic:
    # Fake if original_price > 2x median AND sale price >= 0.95 * median
    inflated_original = current_original > (2 * median_normal)
    price_near_normal = current_price >= (0.95 * median_normal)

    is_fake = inflated_original and price_near_normal

    if is_fake:
        reason = (
            f"FAKE SALE DETECTED. "
            f"Original price (Rp {current_original:,.0f}) is >{2}x the median normal price "
            f"(Rp {median_normal:,.0f}), but the sale price (Rp {current_price:,.0f}) is "
            f"within 5% of the normal median. The discount is cosmetic."
        )
    elif inflated_original and not price_near_normal:
        reason = (
            f"Original price is inflated (Rp {current_original:,.0f} > 2x median "
            f"Rp {median_normal:,.0f}), but the actual sale price (Rp {current_price:,.0f}) "
            f"is genuinely below normal. This appears to be a real discount with exaggerated "
            f"original price."
        )
    else:
        reason = (
            f"Sale appears legitimate. Original price (Rp {current_original:,.0f}) is "
            f"reasonable relative to the median normal price (Rp {median_normal:,.0f})."
        )

    return FakeSaleResult(
        sku=sku,
        marketplace=marketplace,
        is_fake=is_fake,
        reason=reason,
        median_normal=median_normal,
        current_price=current_price,
        current_original_price=current_original,
        sample_count=len(normal_prices),
    )
