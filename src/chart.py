"""Price history chart generator.

Produces minimalist PNG charts showing price trends with annotated
high/low points. Uses matplotlib with a clean, professional style.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

import matplotlib
matplotlib.use("Agg")  # Non-interactive backend for headless environments
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

from src.config import get, project_root
from src.db import get_history
from src.utils import setup_logger

log = setup_logger("chart", "data/logs/chart.log")


def plot_price_history(
    sku: str,
    marketplace: str,
    days: int = 30,
    output_dir: str | None = None,
) -> Optional[str]:
    """Generate a price history chart as PNG.

    Args:
        sku: Product SKU.
        marketplace: Marketplace name.
        days: Number of days of history to plot.
        output_dir: Override output directory (default from config).

    Returns:
        Absolute path to the generated PNG, or None if no data.
    """
    history = get_history(sku, marketplace, days=days)

    if not history:
        log.warning("No price history for %s on %s (last %d days)", sku, marketplace, days)
        return None

    if len(history) < 2:
        log.warning("Only %d data point(s) for %s — chart needs at least 2", len(history), sku)
        return None

    dates = [p.scraped_at for p in history]
    prices = [p.price for p in history]

    # Find min/max points
    min_price = min(prices)
    max_price = max(prices)
    min_idx = prices.index(min_price)
    max_idx = prices.index(max_price)

    # Chart setup — clean, professional style
    fig, ax = plt.subplots(figsize=(12, 5))
    fig.patch.set_facecolor("#fafafa")
    ax.set_facecolor("#fafafa")

    # Plot line
    ax.plot(
        dates,
        prices,
        color="#2563eb",
        linewidth=1.8,
        marker="o",
        markersize=3,
        markerfacecolor="#2563eb",
        markeredgecolor="#2563eb",
        zorder=3,
    )

    # Fill under the curve (subtle)
    ax.fill_between(dates, prices, alpha=0.08, color="#2563eb")

    # Annotate lowest point
    ax.annotate(
        f"Low: Rp {min_price:,.0f}",
        xy=(dates[min_idx], min_price),
        xytext=(0, -25),
        textcoords="offset points",
        fontsize=8,
        fontweight="bold",
        color="#16a34a",
        ha="center",
        arrowprops=dict(arrowstyle="-", color="#16a34a", lw=0.8),
    )
    ax.scatter([dates[min_idx]], [min_price], color="#16a34a", s=60, zorder=5)

    # Annotate highest point
    ax.annotate(
        f"High: Rp {max_price:,.0f}",
        xy=(dates[max_idx], max_price),
        xytext=(0, 20),
        textcoords="offset points",
        fontsize=8,
        fontweight="bold",
        color="#dc2626",
        ha="center",
        arrowprops=dict(arrowstyle="-", color="#dc2626", lw=0.8),
    )
    ax.scatter([dates[max_idx]], [max_price], color="#dc2626", s=60, zorder=5)

    # Grid — subtle
    ax.grid(True, alpha=0.15, linewidth=0.5)
    ax.set_axisbelow(True)

    # Labels
    ax.set_title(
        f"Price History: {sku} ({marketplace})",
        fontsize=12,
        fontweight="bold",
        pad=15,
    )
    ax.set_ylabel("Price (Rp)", fontsize=10)
    ax.set_xlabel("Date", fontsize=10)

    # Date formatting
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
    ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=4, maxticks=10))
    fig.autofmt_xdate(rotation=30)

    # Price formatting on Y axis
    ax.yaxis.set_major_formatter(
        plt.FuncFormatter(lambda x, p: f"Rp {x:,.0f}")
    )

    # Tight layout
    plt.tight_layout()

    # Save
    if output_dir is None:
        output_dir = get("chart.output_dir", "data/charts")

    out_path = Path(output_dir)
    if not out_path.is_absolute():
        out_path = project_root() / out_path
    out_path.mkdir(parents=True, exist_ok=True)

    date_str = datetime.now().strftime("%Y%m%d")
    # Sanitize SKU for filename
    safe_sku = sku.replace("/", "_").replace(".", "_").replace(" ", "_")[:60]
    filename = f"{safe_sku}_{marketplace}_{date_str}.png"
    filepath = out_path / filename

    fig.savefig(str(filepath), dpi=150, bbox_inches="tight")
    plt.close(fig)

    log.info("Chart saved: %s", filepath)
    return str(filepath)
