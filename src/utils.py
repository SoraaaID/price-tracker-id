"""Utility functions: price parsing, retry decorator, logger setup."""

from __future__ import annotations

import functools
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Callable, TypeVar

F = TypeVar("F", bound=Callable[..., Any])


# ---------------------------------------------------------------------------
# Price / text parsing
# ---------------------------------------------------------------------------

def parse_price(raw: str) -> float:
    """Parse Indonesian-format price strings into float.

    Handles:
        "Rp 1.234.567"   -> 1234567.0
        "Rp1.234.567"    -> 1234567.0
        "1.234.567"      -> 1234567.0
        "1,2jt"          -> 1200000.0
        "1.5jt"          -> 1500000.0
        "Rp 999.000"     -> 999000.0
        "850rb"          -> 850000.0
        "1,5rb"          -> 1500.0
        ""               -> 0.0

    Decision: We treat dots as thousands separators (Indonesian convention)
    unless they appear in a "jt"/"rb" shorthand where comma/dot is decimal.
    """
    if not raw or not raw.strip():
        return 0.0

    text = raw.strip().lower()
    # Strip currency prefix
    text = re.sub(r"^rp\.?\s*", "", text)
    # Strip trailing whitespace and non-numeric suffix except jt/rb
    text = text.strip()

    # Handle "jt" (juta = million) shorthand: "1,2jt" or "1.2jt"
    m = re.match(r"^([\d.,]+)\s*jt(?:a)?$", text)
    if m:
        num_str = m.group(1).replace(",", ".")
        return float(num_str) * 1_000_000

    # Handle "rb" (ribu = thousand) shorthand: "850rb", "1,5rb"
    m = re.match(r"^([\d.,]+)\s*(?:rb|ribu)$", text)
    if m:
        num_str = m.group(1).replace(",", ".")
        return float(num_str) * 1_000

    # Standard Indonesian number: dots as thousands, comma as decimal
    # "1.234.567" -> "1234567", "1.234,50" -> "1234.50"
    text = re.sub(r"[^\d.,]", "", text)

    if not text:
        return 0.0

    # If there's a comma, treat it as decimal separator
    if "," in text:
        # Remove thousands dots, replace comma with dot
        text = text.replace(".", "").replace(",", ".")
    else:
        # All dots are thousands separators
        text = text.replace(".", "")

    try:
        return float(text)
    except ValueError:
        return 0.0


def parse_sold(raw: str) -> int:
    """Parse Indonesian sold-count strings.

    Handles:
        "1,2rb terjual"  -> 1200
        "1.2rb terjual"  -> 1200
        "500+ terjual"   -> 500
        "12,5rb"         -> 12500
        "3jt terjual"    -> 3000000
        "150"            -> 150
    """
    if not raw or not raw.strip():
        return 0

    text = raw.strip().lower()
    text = re.sub(r"\s*terjual.*$", "", text)
    text = text.replace("+", "").strip()

    # "rb" shorthand
    m = re.match(r"^([\d.,]+)\s*(?:rb|ribu)$", text)
    if m:
        num_str = m.group(1).replace(",", ".")
        return int(float(num_str) * 1_000)

    # "jt" shorthand
    m = re.match(r"^([\d.,]+)\s*jt(?:a)?$", text)
    if m:
        num_str = m.group(1).replace(",", ".")
        return int(float(num_str) * 1_000_000)

    # Plain number
    text = re.sub(r"[^\d]", "", text)
    if not text:
        return 0
    return int(text)


# ---------------------------------------------------------------------------
# Retry decorator
# ---------------------------------------------------------------------------

def retry(
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    exceptions: tuple = (Exception,),
) -> Callable[[F], F]:
    """Decorator: retry with exponential backoff.

    Args:
        max_retries: Number of retry attempts.
        base_delay: Initial delay in seconds.
        max_delay: Maximum delay cap in seconds.
        exceptions: Tuple of exception types to catch.
    """

    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exc: Exception | None = None
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as exc:
                    last_exc = exc
                    if attempt == max_retries:
                        break
                    delay = min(base_delay * (2 ** attempt), max_delay)
                    logging.getLogger("retry").warning(
                        "Attempt %d/%d for %s failed: %s. Retrying in %.1fs",
                        attempt + 1,
                        max_retries,
                        func.__name__,
                        exc,
                        delay,
                    )
                    time.sleep(delay)
            raise last_exc  # type: ignore[misc]

        return wrapper  # type: ignore[return-value]

    return decorator


# ---------------------------------------------------------------------------
# Logger setup
# ---------------------------------------------------------------------------

def setup_logger(name: str, log_file: str | None = None) -> logging.Logger:
    """Create a logger with console + optional file handler.

    Args:
        name: Logger name (usually module name).
        log_file: Optional path to log file. Parent dirs will be created.

    Returns:
        Configured Logger instance.
    """
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger  # already configured

    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter(
        "%(asctime)s | %(name)-18s | %(levelname)-7s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler — INFO level
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    # File handler — DEBUG level
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(str(log_path), encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)
        logger.addHandler(fh)

    return logger
