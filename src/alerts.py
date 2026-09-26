"""Telegram alert system.

Sends price-drop notifications via Telegram Bot API.
Rate-limited: max 1 message per SKU per hour (tracked in DB).
"""

from __future__ import annotations

import requests

from src.config import get
from src.db import can_send_alert, record_alert
from src.models import Product
from src.utils import setup_logger

log = setup_logger("alerts", "data/logs/alerts.log")


class TelegramNotifier:
    """Send alerts via Telegram Bot API."""

    def __init__(self) -> None:
        self._bot_token: str = get("telegram.bot_token", "")
        self._chat_id: str = get("telegram.chat_id", "")
        self._api_base = "https://api.telegram.org"

    @property
    def is_configured(self) -> bool:
        return bool(self._bot_token) and bool(self._chat_id)

    def _api_url(self, method: str) -> str:
        return f"{self._api_base}/bot{self._bot_token}/{method}"

    def send(self, message: str) -> bool:
        """Send a plain text message to the configured chat.

        Args:
            message: Text message to send (supports Telegram Markdown).

        Returns:
            True if sent successfully, False otherwise.
        """
        if not self.is_configured:
            log.warning("Telegram not configured — skipping alert.")
            return False

        try:
            resp = requests.post(
                self._api_url("sendMessage"),
                json={
                    "chat_id": self._chat_id,
                    "text": message,
                    "parse_mode": "Markdown",
                    "disable_web_page_preview": True,
                },
                timeout=10,
            )
            data = resp.json()
            if data.get("ok"):
                log.info("Telegram message sent successfully.")
                return True
            else:
                log.error("Telegram API error: %s", data.get("description", "Unknown"))
                return False
        except requests.exceptions.RequestException as e:
            log.error("Failed to send Telegram message: %s", e)
            return False

    def send_price_drop(
        self,
        product: Product,
        old_price: float,
        new_price: float,
    ) -> bool:
        """Send a price-drop alert for a product.

        Checks rate limit (1 per SKU per hour) before sending.

        Args:
            product: The Product with updated pricing.
            old_price: Previous price.
            new_price: Current (lower) price.

        Returns:
            True if alert was sent, False if rate-limited or failed.
        """
        if not self.is_configured:
            log.warning("Telegram not configured — skipping price drop alert.")
            return False

        # Rate limit check
        if not can_send_alert(product.sku, product.marketplace):
            log.info(
                "Rate limited: already sent alert for %s (%s) within the last hour.",
                product.sku,
                product.marketplace,
            )
            return False

        drop_pct = ((old_price - new_price) / old_price) * 100 if old_price > 0 else 0

        message = (
            f"*Price Drop Alert*\n\n"
            f"*{product.name}*\n"
            f"Marketplace: {product.marketplace.title()}\n"
            f"Store: {product.store_name or 'N/A'}\n\n"
            f"Old Price: Rp {old_price:,.0f}\n"
            f"New Price: *Rp {new_price:,.0f}*\n"
            f"Drop: *{drop_pct:.1f}%*\n\n"
            f"[View Product]({product.url})"
        )

        success = self.send(message)
        if success:
            record_alert(product.sku, product.marketplace)
        return success

    def test_connection(self) -> dict:
        """Test the Telegram bot connection via getMe.

        Returns:
            Bot info dict if successful, or error dict.
        """
        if not self._bot_token:
            return {"ok": False, "description": "Bot token not configured"}

        try:
            resp = requests.get(self._api_url("getMe"), timeout=10)
            return resp.json()
        except requests.exceptions.RequestException as e:
            return {"ok": False, "description": str(e)}

    def send_photo(self, photo_path: str, caption: str = "") -> bool:
        """Send a photo (e.g., chart) to the configured chat.

        Args:
            photo_path: Local path to the image file.
            caption: Optional caption text.

        Returns:
            True if sent successfully, False otherwise.
        """
        if not self.is_configured:
            return False

        try:
            with open(photo_path, "rb") as f:
                resp = requests.post(
                    self._api_url("sendPhoto"),
                    data={
                        "chat_id": self._chat_id,
                        "caption": caption,
                        "parse_mode": "Markdown",
                    },
                    files={"photo": f},
                    timeout=30,
                )
            return resp.json().get("ok", False)
        except Exception as e:
            log.error("Failed to send Telegram photo: %s", e)
            return False
