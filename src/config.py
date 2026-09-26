"""Configuration loader.

Loads config.toml using tomli (Python <3.11) or tomllib (3.11+).
Falls back to config.example.toml if config.toml doesn't exist.
Also reads environment variables for secrets (TELEGRAM_BOT_TOKEN, etc.).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

# tomllib is stdlib in 3.11+; use tomli as fallback
if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ImportError:
        raise ImportError("Install tomli: pip install tomli (Python <3.11)")


_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_CONFIG_PATH = _PROJECT_ROOT / "config.toml"
_CONFIG_EXAMPLE_PATH = _PROJECT_ROOT / "config.example.toml"

_config: dict[str, Any] | None = None


def _load_raw() -> dict[str, Any]:
    """Read and parse the TOML config file."""
    path = _CONFIG_PATH if _CONFIG_PATH.exists() else _CONFIG_EXAMPLE_PATH
    with open(path, "rb") as f:
        return tomllib.load(f)


def _apply_env_overrides(cfg: dict[str, Any]) -> None:
    """Override config values with environment variables when set."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if token:
        cfg.setdefault("telegram", {})["bot_token"] = token

    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if chat_id:
        cfg.setdefault("telegram", {})["chat_id"] = chat_id


def _apply_defaults(cfg: dict[str, Any]) -> None:
    """Ensure all required sections exist with sensible defaults."""
    defaults: dict[str, Any] = {
        "telegram": {"bot_token": "", "chat_id": ""},
        "scraper": {
            "timeout": 15,
            "retry": 3,
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "shopee": {"use_api_first": True, "api_base": "https://shopee.co.id/api/v4"},
            "tokopedia": {
                "graphql_url": "https://gql.tokopedia.com/graphql/ProductQuery",
                "operation_hash": "",
            },
            "browser": {
                "headless": False,
                "timeout": 60000,
                "screenshot_on_error": True,
            },
        },
        "alert": {"price_drop_threshold_pct": 5.0, "max_alerts_per_hour": 10},
        "db": {"path": "data/price_history.db"},
        "chart": {"output_dir": "data/charts"},
    }

    def _merge(target: dict, source: dict) -> None:
        for key, value in source.items():
            if key not in target:
                target[key] = value
            elif isinstance(value, dict) and isinstance(target.get(key), dict):
                _merge(target[key], value)

    _merge(cfg, defaults)


def load_config() -> dict[str, Any]:
    """Load, validate, and cache the application config.

    Returns:
        Merged configuration dict.
    """
    global _config
    if _config is not None:
        return _config

    cfg = _load_raw()
    _apply_defaults(cfg)
    _apply_env_overrides(cfg)
    _config = cfg
    return _config


def reload_config() -> dict[str, Any]:
    """Force-reload config from disk (useful for tests)."""
    global _config
    _config = None
    return load_config()


def get(key: str, default: Any = None) -> Any:
    """Get a dot-separated config key.

    Example:
        get("scraper.timeout")  -> 15
        get("telegram.bot_token") -> ""
    """
    cfg = load_config()
    parts = key.split(".")
    node: Any = cfg
    for part in parts:
        if isinstance(node, dict):
            node = node.get(part)
        else:
            return default
        if node is None:
            return default
    return node


def project_root() -> Path:
    """Return the project root directory."""
    return _PROJECT_ROOT


def validate_tokopedia_config(url: str = "") -> str:
    """Validate Tokopedia scraper configuration.

    Raises:
        ScraperError: If operation_hash is missing or empty.
    """
    op_hash = get("scraper.tokopedia.operation_hash", "")
    if not op_hash or not str(op_hash).strip():
        from src.scrapers.base import ScraperError

        raise ScraperError(
            "Tokopedia operation_hash belum di-set di config.toml — "
            "cek Network tab browser di halaman produk Tokopedia, cari "
            "request graphql/ProductQuery, copy hash-nya",
            marketplace="tokopedia",
            url=url,
        )
    return str(op_hash).strip()
