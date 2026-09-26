"""Price Tracker CLI — main entry point.

Subcommands:
    add <url>               Add a product URL to tracking
    track                   Scrape all tracked products, update DB, send alerts
    compare <sku>           Compare prices for a SKU across marketplaces
    chart <sku>             Generate price history chart
    check-fake <sku>        Check if a product's flash sale is fake
    alert-test              Test Telegram bot connection

Usage:
    python main.py add https://www.blibli.com/p/product-name/ps--SKU123
    python main.py track
    python main.py compare ps--SKU123
    python main.py chart ps--SKU123 --marketplace blibli --days 30
    python main.py check-fake ps--SKU123 --marketplace blibli
    python main.py alert-test
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from typing import Optional

from rich.console import Console
from rich.table import Table

from src.alerts import TelegramNotifier
from src.chart import plot_price_history
from src.config import get, load_config
from src.db import (
    get_all_products,
    get_latest,
    get_previous_price,
    init_db,
    insert_history,
    upsert_product,
)
from src.detector import detect_fake_flash_sale
from src.models import Product
from src.scrapers import SCRAPER_MAP
from src.scrapers.base import ScraperError
from src.utils import setup_logger

console = Console()
log = setup_logger("main", "data/logs/main.log")


def _detect_marketplace(url: str) -> Optional[str]:
    """Detect marketplace from URL domain."""
    url_lower = url.lower()
    if "blibli.com" in url_lower:
        return "blibli"
    if "lazada.co.id" in url_lower:
        return "lazada"
    if "tokopedia.com" in url_lower:
        return "tokopedia"
    if "shopee.co.id" in url_lower:
        return "shopee"
    return None


# --------------------------------------------------------------------------
# Subcommand: add
# --------------------------------------------------------------------------


def cmd_add(args: argparse.Namespace) -> None:
    """Add a product URL to tracking."""
    url = args.url
    marketplace = _detect_marketplace(url)

    if not marketplace:
        console.print(f"[red]Cannot detect marketplace from URL:[/red] {url}")
        console.print("Supported: blibli.com, lazada.co.id, tokopedia.com, shopee.co.id")
        sys.exit(1)

    console.print(f"Detected marketplace: [cyan]{marketplace}[/cyan]")
    console.print(f"Fetching product info from [blue]{url}[/blue] ...")

    scraper_cls = SCRAPER_MAP.get(marketplace)
    if not scraper_cls:
        console.print(f"[red]No scraper available for {marketplace}[/red]")
        sys.exit(1)

    try:
        with scraper_cls() as scraper:
            product = scraper.fetch_product(url)
    except ScraperError as e:
        console.print(f"[red]Scraping failed:[/red] {e}")
        sys.exit(1)

    upsert_product(product)
    insert_history(product)

    console.print(f"\n[green]Product added and first price recorded:[/green]")
    console.print(f"  SKU:   {product.sku}")
    console.print(f"  Name:  {product.name}")
    console.print(f"  Price: Rp {product.price:,.0f}")
    if product.has_discount:
        console.print(f"  Original: Rp {product.original_price:,.0f}")
        console.print(f"  Discount: {product.computed_discount_pct}%")


# --------------------------------------------------------------------------
# Subcommand: track
# --------------------------------------------------------------------------


def cmd_track(args: argparse.Namespace) -> None:
    """Scrape all tracked products, update DB, send alerts on price drops."""
    products = get_all_products()

    if not products:
        console.print("[yellow]No products tracked. Use 'add <url>' first.[/yellow]")
        return

    console.print(f"Tracking [cyan]{len(products)}[/cyan] product(s)...\n")

    threshold_pct = get("alert.price_drop_threshold_pct", 5.0)
    notifier = TelegramNotifier()
    results = []

    for prod in products:
        marketplace = prod["marketplace"]
        url = prod["url"]
        sku = prod["sku"]

        scraper_cls = SCRAPER_MAP.get(marketplace)
        if not scraper_cls:
            console.print(f"  [red]No scraper for {marketplace}[/red] — skipping {sku}")
            continue

        try:
            with scraper_cls() as scraper:
                product = scraper.fetch_product(url)
        except ScraperError as e:
            console.print(f"  [red]Failed[/red] {sku} ({marketplace}): {e}")
            log.error("Scrape failed for %s (%s): %s", sku, marketplace, e)
            results.append((sku, marketplace, "FAILED", str(e)))
            continue

        # Get previous price before inserting new one
        old_price = get_previous_price(sku, marketplace)
        insert_history(product)

        # Check for price drop
        if old_price is not None and old_price > 0:
            drop_pct = ((old_price - product.price) / old_price) * 100
            if drop_pct >= threshold_pct:
                console.print(
                    f"  [green]Price drop![/green] {sku} ({marketplace}): "
                    f"Rp {old_price:,.0f} -> Rp {product.price:,.0f} "
                    f"(-{drop_pct:.1f}%)"
                )
                notifier.send_price_drop(product, old_price, product.price)
            else:
                console.print(
                    f"  [dim]{sku} ({marketplace}): Rp {product.price:,.0f}[/dim]"
                )
        else:
            console.print(
                f"  [dim]{sku} ({marketplace}): Rp {product.price:,.0f} (first reading)[/dim]"
            )

        results.append((sku, marketplace, "OK", f"Rp {product.price:,.0f}"))

    # Summary
    console.print(f"\n[bold]Tracking complete.[/bold] {len(results)}/{len(products)} processed.")


# --------------------------------------------------------------------------
# Subcommand: compare
# --------------------------------------------------------------------------


def cmd_compare(args: argparse.Namespace) -> None:
    """Compare prices for a SKU across all marketplaces."""
    sku = args.sku
    latest = get_latest(sku)

    if not latest:
        console.print(f"[yellow]No price data found for SKU: {sku}[/yellow]")
        console.print("Make sure you've tracked this product with 'add <url>' first.")
        return

    table = Table(title=f"Price Comparison: {sku}", show_header=True, header_style="bold cyan")
    table.add_column("Marketplace", style="bold")
    table.add_column("Current Price", justify="right")
    table.add_column("Original Price", justify="right")
    table.add_column("Discount", justify="right")
    table.add_column("Last Updated")

    # Sort by price
    sorted_entries = sorted(latest.items(), key=lambda x: x[1].price if x[1] else float("inf"))
    lowest_price = sorted_entries[0][1].price if sorted_entries else 0

    for marketplace, point in sorted_entries:
        if point is None:
            table.add_row(marketplace, "N/A", "N/A", "N/A", "N/A")
            continue

        price_str = f"Rp {point.price:,.0f}"
        orig_str = f"Rp {point.original_price:,.0f}" if point.original_price else "-"
        disc_str = f"{point.discount_pct:.0f}%" if point.discount_pct else "-"
        time_str = point.scraped_at.strftime("%Y-%m-%d %H:%M")

        # Highlight the lowest price
        if point.price == lowest_price:
            price_str = f"[green]{price_str} (LOWEST)[/green]"

        table.add_row(marketplace, price_str, orig_str, disc_str, time_str)

    console.print(table)


# --------------------------------------------------------------------------
# Subcommand: chart
# --------------------------------------------------------------------------


def cmd_chart(args: argparse.Namespace) -> None:
    """Generate a price history chart."""
    sku = args.sku
    marketplace = args.marketplace
    days = args.days

    if not marketplace:
        # Generate charts for all marketplaces that have data
        latest = get_latest(sku)
        if not latest:
            console.print(f"[yellow]No data for SKU: {sku}[/yellow]")
            return

        for mp in latest:
            path = plot_price_history(sku, mp, days=days)
            if path:
                console.print(f"[green]Chart saved:[/green] {path}")
            else:
                console.print(f"[yellow]Not enough data for {mp}[/yellow]")
    else:
        path = plot_price_history(sku, marketplace, days=days)
        if path:
            console.print(f"[green]Chart saved:[/green] {path}")
        else:
            console.print(f"[yellow]Not enough data for {marketplace}[/yellow]")


# --------------------------------------------------------------------------
# Subcommand: check-fake
# --------------------------------------------------------------------------


def cmd_check_fake(args: argparse.Namespace) -> None:
    """Check if a product's flash sale is fake."""
    sku = args.sku
    marketplace = args.marketplace

    if not marketplace:
        # Check all marketplaces
        latest = get_latest(sku)
        if not latest:
            console.print(f"[yellow]No data for SKU: {sku}[/yellow]")
            return
        marketplaces = list(latest.keys())
    else:
        marketplaces = [marketplace]

    for mp in marketplaces:
        result = detect_fake_flash_sale(sku, mp)

        if result.is_fake:
            console.print(f"\n[red bold]FAKE SALE DETECTED[/red bold] — {mp}")
        else:
            console.print(f"\n[green]Sale appears legitimate[/green] — {mp}")

        console.print(f"  Reason: {result.reason}")
        console.print(f"  Median normal price: Rp {result.median_normal:,.0f}")
        console.print(f"  Current price: Rp {result.current_price:,.0f}")
        console.print(f"  Current original price: Rp {result.current_original_price:,.0f}")
        console.print(f"  Sample count: {result.sample_count}")


# --------------------------------------------------------------------------
# Subcommand: alert-test
# --------------------------------------------------------------------------


def cmd_alert_test(args: argparse.Namespace) -> None:
    """Test Telegram bot connection."""
    notifier = TelegramNotifier()

    if not notifier.is_configured:
        console.print("[red]Telegram not configured.[/red]")
        console.print("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in config.toml or .env")
        console.print("Or run: python scripts/setup_telegram.py")
        sys.exit(1)

    console.print("Testing Telegram connection...")

    bot_info = notifier.test_connection()
    if bot_info.get("ok"):
        bot = bot_info["result"]
        console.print(f"[green]Connected![/green] Bot: @{bot['username']} ({bot['first_name']})")

        # Send test message
        success = notifier.send(
            "*Price Tracker Test*\n\n"
            "This is a test message from your price tracker.\n"
            f"Timestamp: {datetime.now(timezone.utc).isoformat()}"
        )
        if success:
            console.print("[green]Test message sent successfully![/green]")
        else:
            console.print("[red]Failed to send test message.[/red]")
    else:
        console.print(f"[red]Connection failed:[/red] {bot_info.get('description', 'Unknown error')}")
        sys.exit(1)


# --------------------------------------------------------------------------
# CLI Parser
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="price-tracker",
        description="Track and compare product prices across Indonesian marketplaces.",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # add
    p_add = subparsers.add_parser("add", help="Add a product URL to tracking")
    p_add.add_argument("url", help="Product URL (Blibli, Lazada, Tokopedia, or Shopee)")

    # track
    subparsers.add_parser("track", help="Scrape all tracked products and update prices")

    # compare
    p_compare = subparsers.add_parser("compare", help="Compare prices for a SKU across marketplaces")
    p_compare.add_argument("sku", help="Product SKU to compare")

    # chart
    p_chart = subparsers.add_parser("chart", help="Generate price history chart")
    p_chart.add_argument("sku", help="Product SKU")
    p_chart.add_argument("--marketplace", "-m", default=None, help="Specific marketplace (default: all)")
    p_chart.add_argument("--days", "-d", type=int, default=30, help="Days of history (default: 30)")

    # check-fake
    p_fake = subparsers.add_parser("check-fake", help="Check if a flash sale is fake")
    p_fake.add_argument("sku", help="Product SKU")
    p_fake.add_argument("--marketplace", "-m", default=None, help="Specific marketplace (default: all)")

    # alert-test
    subparsers.add_parser("alert-test", help="Test Telegram bot connection")

    return parser


def main() -> None:
    load_config()
    init_db()

    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    commands = {
        "add": cmd_add,
        "track": cmd_track,
        "compare": cmd_compare,
        "chart": cmd_chart,
        "check-fake": cmd_check_fake,
        "alert-test": cmd_alert_test,
    }

    handler = commands.get(args.command)
    if handler:
        handler(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
