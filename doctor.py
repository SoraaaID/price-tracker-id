"""Self-diagnostic for price-tracker pipeline.

Performs automated health checks across environment, database, cookies,
scrapers, Telegram alerts, chart generation, Task Scheduler, and log files.
"""

from __future__ import annotations

import argparse
import importlib
import json
import logging
import os
import re
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

# Ensure UTF-8 output on Windows consoles to prevent UnicodeEncodeError
if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Use rich if available, with graceful fallback
try:
    from rich.console import Console
    from rich.table import Table
    from rich.text import Text
    _HAS_RICH = True
except ImportError:
    _HAS_RICH = False

_PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_PROJECT_ROOT))


@dataclass
class CheckResult:
    name: str
    status: str  # "ok" | "warn" | "fail"
    message: str
    details: dict = field(default_factory=dict)
    duration_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "message": self.message,
            "details": self.details,
            "duration_ms": self.duration_ms,
        }


def _format_time_ago(dt: datetime, now: datetime) -> str:
    """Format timedelta into human-readable Indonesian string."""
    diff = now - dt
    total_seconds = int(diff.total_seconds())

    if total_seconds < 0:
        return "baru saja"
    if total_seconds < 60:
        return f"{total_seconds} detik lalu"
    minutes = total_seconds // 60
    if minutes < 60:
        return f"{minutes} menit lalu"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} jam lalu"
    days = hours // 24
    if days < 30:
        return f"{days} hari lalu"
    months = days // 30
    return f"{months} bulan lalu"


def _parse_timestamp(ts_str: str) -> datetime | None:
    """Parse various timestamp formats from DB or logs."""
    if not ts_str:
        return None
    clean = ts_str.strip().replace("Z", "+00:00")
    for fmt in (
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S.%f",
    ):
        try:
            dt = datetime.strptime(clean[:26], fmt)
            if dt.tzinfo is not None:
                return dt.astimezone(timezone.utc).replace(tzinfo=None)
            return dt
        except ValueError:
            continue
    try:
        dt = datetime.fromisoformat(clean)
        if dt.tzinfo is not None:
            return dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except Exception:
        return None


# =====================================================================
# 1. Environment Check
# =====================================================================
def check_env() -> CheckResult:
    """Check Python version, venv status, dependencies, and required folders."""
    t0 = time.perf_counter()
    details: dict[str, Any] = {}
    fails: list[str] = []
    warns: list[str] = []

    # Python version
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    details["python_version"] = py_ver
    if sys.version_info < (3, 11):
        fails.append(f"Python {py_ver} < 3.11")

    # Venv check
    is_venv = sys.prefix != sys.base_prefix
    details["venv_active"] = is_venv
    if not is_venv:
        warns.append("venv tidak aktif")

    # Dependency imports
    packages = {
        "requests": True,
        "bs4": True,
        "matplotlib": True,
        "rich": True,
        "tomli": sys.version_info < (3, 11),  # tomllib built-in in 3.11+
        "playwright": False,  # playwright or patchright
        "patchright": False,
        "playwright_stealth": False,
    }

    import_status: dict[str, bool] = {}
    for pkg in packages:
        try:
            importlib.import_module(pkg)
            import_status[pkg] = True
        except ImportError:
            import_status[pkg] = False

    details["imports"] = import_status

    # Validate browser engine (either playwright or patchright)
    if not import_status.get("playwright") and not import_status.get("patchright"):
        fails.append("playwright / patchright tidak terpasang")

    # Critical packages
    for pkg in ("requests", "bs4", "matplotlib", "rich"):
        if not import_status.get(pkg):
            fails.append(f"{pkg} missing")

    if sys.version_info < (3, 11) and not import_status.get("tomli"):
        fails.append("tomli missing (Python < 3.11)")

    # Files and folders
    required_paths = [
        (_PROJECT_ROOT / "config.toml", "file", True),
        (_PROJECT_ROOT / "input", "dir", True),
        (_PROJECT_ROOT / "data", "dir", True),
        (_PROJECT_ROOT / "data" / "logs", "dir", True),
        (_PROJECT_ROOT / "input" / "lazada_cookies.json", "file", False),
        (_PROJECT_ROOT / "input" / "proxies.txt", "file", False),
    ]

    path_status: dict[str, bool] = {}
    for p, ptype, required in required_paths:
        exists = p.is_dir() if ptype == "dir" else p.is_file()
        path_status[p.name] = exists
        if not exists and required:
            fails.append(f"{p.name} tidak ditemukan")

    details["paths"] = path_status

    dur = int((time.perf_counter() - t0) * 1000)

    if fails:
        status = "fail"
        msg = f"FAIL — {', '.join(fails)}"
    elif warns:
        status = "warn"
        msg = f"Python {sys.version_info.major}.{sys.version_info.minor}, {', '.join(warns)}"
    else:
        status = "ok"
        msg = f"Python {sys.version_info.major}.{sys.version_info.minor}, venv aktif"

    return CheckResult(
        name="env",
        status=status,
        message=msg,
        details=details,
        duration_ms=dur,
    )


# =====================================================================
# 2. Database Check
# =====================================================================
def check_db() -> CheckResult:
    """Check database existence, schema completeness, and scraping recency."""
    t0 = time.perf_counter()
    db_path = _PROJECT_ROOT / "data" / "price_history.db"

    if not db_path.exists():
        return CheckResult(
            name="db",
            status="fail",
            message="Database file tidak ditemukan (data/price_history.db)",
            duration_ms=int((time.perf_counter() - t0) * 1000),
        )

    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        # Check tables
        tables_res = cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        existing_tables = {row["name"] for row in tables_res}
        required_tables = {"products", "price_history", "alerts_sent"}
        missing_tables = required_tables - existing_tables
        if missing_tables:
            conn.close()
            return CheckResult(
                name="db",
                status="fail",
                message=f"Tabel database hilang: {', '.join(missing_tables)}",
                details={"existing": list(existing_tables)},
                duration_ms=int((time.perf_counter() - t0) * 1000),
            )

        # Count products per marketplace
        mkt_counts = cur.execute(
            "SELECT marketplace, COUNT(*) as cnt FROM products GROUP BY marketplace"
        ).fetchall()
        by_mkt = {row["marketplace"]: row["cnt"] for row in mkt_counts}
        total_products = sum(by_mkt.values())

        # Count data points and find last scrape date
        product_stats = cur.execute(
            """
            SELECT p.id, p.sku, p.marketplace, COUNT(h.id) as points, MAX(h.scraped_at) as last
            FROM products p
            LEFT JOIN price_history h ON h.product_id = p.id
            GROUP BY p.id
            """
        ).fetchall()

        total_points = sum(row["points"] for row in product_stats)

        # Evaluate last scrape
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        all_last_dts: list[datetime] = []
        stale_products: list[str] = []

        for row in product_stats:
            last_raw = row["last"]
            if last_raw:
                dt = _parse_timestamp(last_raw)
                if dt:
                    all_last_dts.append(dt)
                    if (now - dt) > timedelta(hours=24):
                        stale_products.append(f"{row['sku']} ({row['marketplace']})")
            else:
                stale_products.append(f"{row['sku']} ({row['marketplace']})")

        conn.close()

        latest_scrape_str = "belum pernah"
        if all_last_dts:
            latest_dt = max(all_last_dts)
            latest_scrape_str = _format_time_ago(latest_dt, now)

        dur = int((time.perf_counter() - t0) * 1000)
        details = {
            "total_products": total_products,
            "total_points": total_points,
            "by_marketplace": by_mkt,
            "stale_products_count": len(stale_products),
            "stale_products": stale_products[:5],
        }

        if total_products == 0:
            return CheckResult(
                name="db",
                status="warn",
                message="Database kosong — 0 produk terdaftar",
                details=details,
                duration_ms=dur,
            )

        if stale_products:
            return CheckResult(
                name="db",
                status="warn",
                message=(
                    f"{total_products} produk, {total_points} data point, "
                    f"last scrape {latest_scrape_str} ({len(stale_products)} produk > 24 jam)"
                ),
                details=details,
                duration_ms=dur,
            )

        return CheckResult(
            name="db",
            status="ok",
            message=f"{total_products} produk, {total_points} data point, last scrape {latest_scrape_str}",
            details=details,
            duration_ms=dur,
        )

    except Exception as e:
        return CheckResult(
            name="db",
            status="fail",
            message=f"Database error: {e}",
            duration_ms=int((time.perf_counter() - t0) * 1000),
        )


# =====================================================================
# 3. Cookies Check
# =====================================================================
def check_cookies() -> CheckResult:
    """Check Lazada cookie file existence, JSON validity, key cookies, and expiry."""
    t0 = time.perf_counter()
    cookie_file = _PROJECT_ROOT / "input" / "lazada_cookies.json"

    if not cookie_file.exists():
        return CheckResult(
            name="cookies",
            status="fail",
            message="File input/lazada_cookies.json tidak ditemukan",
            duration_ms=int((time.perf_counter() - t0) * 1000),
        )

    try:
        with open(cookie_file, "r", encoding="utf-8") as f:
            cookies = json.load(f)
    except Exception as e:
        return CheckResult(
            name="cookies",
            status="fail",
            message=f"JSON lazada_cookies.json tidak valid: {e}",
            duration_ms=int((time.perf_counter() - t0) * 1000),
        )

    if not isinstance(cookies, list):
        return CheckResult(
            name="cookies",
            status="fail",
            message="Format lazada_cookies.json harus berupa JSON array (list)",
            duration_ms=int((time.perf_counter() - t0) * 1000),
        )

    cookie_map: dict[str, dict] = {
        c.get("name"): c for c in cookies if isinstance(c, dict) and "name" in c
    }

    key_names = ["_m_h5_tk", "_m_h5_tk_enc", "cna"]
    found_keys = [k for k in key_names if k in cookie_map]
    missing_keys = [k for k in key_names if k not in cookie_map]

    # _m_h5_tk is the primary required cookie
    primary = cookie_map.get("_m_h5_tk")
    if not primary:
        return CheckResult(
            name="cookies",
            status="fail",
            message=f"Cookie kunci _m_h5_tk tidak ditemukan (ada: {', '.join(found_keys) or 'kosong'})",
            details={"cookie_count": len(cookies), "found_keys": found_keys},
            duration_ms=int((time.perf_counter() - t0) * 1000),
        )

    exp = primary.get("expirationDate")
    now_utc = datetime.now(timezone.utc)
    dur = int((time.perf_counter() - t0) * 1000)

    if exp is not None:
        try:
            exp_dt = datetime.fromtimestamp(float(exp), tz=timezone.utc)
            delta = exp_dt - now_utc

            if delta.total_seconds() <= 0:
                return CheckResult(
                    name="cookies",
                    status="fail",
                    message=f"Cookie _m_h5_tk sudah expired pada {exp_dt.strftime('%Y-%m-%d %H:%M')}",
                    details={"expiration": exp_dt.isoformat()},
                    duration_ms=dur,
                )

            days_left = delta.days
            if days_left > 60:
                months_left = days_left // 30
                time_str = f"{months_left} bulan lagi"
            elif days_left > 0:
                time_str = f"{days_left} hari lagi"
            else:
                hours_left = int(delta.total_seconds() // 3600)
                time_str = f"{hours_left} jam lagi"

            if days_left <= 7:
                return CheckResult(
                    name="cookies",
                    status="warn",
                    message=f"{len(cookies)} cookie, _m_h5_tk expired segera ({time_str})",
                    details={"expiration": exp_dt.isoformat(), "days_left": days_left},
                    duration_ms=dur,
                )

            # Check if other keys are missing
            if missing_keys:
                return CheckResult(
                    name="cookies",
                    status="warn",
                    message=f"{len(cookies)} cookie, _m_h5_tk valid {time_str}, tapi {', '.join(missing_keys)} tidak ada",
                    details={"missing_keys": missing_keys},
                    duration_ms=dur,
                )

            return CheckResult(
                name="cookies",
                status="ok",
                message=f"{len(cookies)} cookie, _m_h5_tk valid {time_str}",
                details={"cookie_count": len(cookies), "expiration": exp_dt.isoformat()},
                duration_ms=dur,
            )

        except Exception as e:
            return CheckResult(
                name="cookies",
                status="warn",
                message=f"{len(cookies)} cookie, gagal parse expiration: {e}",
                duration_ms=dur,
            )

    return CheckResult(
        name="cookies",
        status="ok",
        message=f"{len(cookies)} cookie ditemukan (tanpa field expirationDate)",
        duration_ms=dur,
    )


# =====================================================================
# 4. Scrapers Check (Sandbox mode)
# =====================================================================
def check_scrapers() -> list[CheckResult]:
    """Test fetch_product for each marketplace in sandbox mode (does not save to DB)."""
    db_path = _PROJECT_ROOT / "data" / "price_history.db"
    results: list[CheckResult] = []

    marketplaces = ["blibli", "lazada", "tokopedia", "shopee"]
    samples: dict[str, dict] = {}

    if db_path.exists():
        try:
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            for mkt in marketplaces:
                row = cur.execute(
                    "SELECT sku, marketplace, name, url FROM products WHERE marketplace = ? LIMIT 1",
                    (mkt,),
                ).fetchone()
                if row:
                    samples[mkt] = dict(row)
            conn.close()
        except Exception:
            pass

    for mkt_name in ("scraper.blibli", "scraper.lazada", "scraper.tokopedia", "scraper.shopee", "db"):
        logging.getLogger(mkt_name).setLevel(logging.WARNING)

    from src.scrapers import SCRAPER_MAP

    for mkt in marketplaces:
        t0 = time.perf_counter()
        sample = samples.get(mkt)

        if not sample:
            results.append(
                CheckResult(
                    name=mkt,
                    status="warn",
                    message="Tidak ada produk di database untuk diuji",
                    duration_ms=0,
                )
            )
            continue

        scraper_cls = SCRAPER_MAP.get(mkt)
        if not scraper_cls:
            results.append(
                CheckResult(
                    name=mkt,
                    status="fail",
                    message=f"Scraper class untuk {mkt} tidak ditemukan di SCRAPER_MAP",
                    duration_ms=0,
                )
            )
            continue

        scraper = None
        try:
            scraper = scraper_cls()
            product = scraper.fetch_product(sample["url"])
            dur_sec = time.perf_counter() - t0
            dur_ms = int(dur_sec * 1000)

            price_int = int(round(product.price))
            price_formatted = f"Rp {price_int:,}".replace(",", ".")
            results.append(
                CheckResult(
                    name=mkt,
                    status="ok",
                    message=f"OK — {price_formatted} dalam {dur_sec:.1f}s",
                    details={
                        "sku": product.sku,
                        "name": product.name,
                        "price": product.price,
                        "stock": product.stock,
                    },
                    duration_ms=dur_ms,
                )
            )
        except Exception as e:
            dur_ms = int((time.perf_counter() - t0) * 1000)
            err_line = str(e).split("\n")[0].strip()
            if len(err_line) > 80:
                err_line = err_line[:77] + "..."
            results.append(
                CheckResult(
                    name=mkt,
                    status="fail",
                    message=f"FAIL — {err_line}",
                    details={"sku": sample.get("sku"), "error": str(e)},
                    duration_ms=dur_ms,
                )
            )
        finally:
            if scraper and hasattr(scraper, "close"):
                try:
                    scraper.close()
                except Exception:
                    pass

    return results


# =====================================================================
# 5. Alert Check (Telegram)
# =====================================================================
def check_alert() -> CheckResult:
    """Check Telegram bot configuration, verify getMe, and send a test message."""
    t0 = time.perf_counter()
    from src.config import get

    bot_token = get("telegram.bot_token", "")
    chat_id = get("telegram.chat_id", "")

    if not bot_token or not chat_id:
        return CheckResult(
            name="alert",
            status="warn",
            message="Telegram bot_token atau chat_id belum diisi di config.toml",
            duration_ms=int((time.perf_counter() - t0) * 1000),
        )

    import requests

    base_url = f"https://api.telegram.org/bot{bot_token}"

    # 1. getMe
    try:
        me_resp = requests.get(f"{base_url}/getMe", timeout=10)
        me_data = me_resp.json()
        if not me_data.get("ok"):
            desc = me_data.get("description", "Unauthorized")
            return CheckResult(
                name="alert",
                status="fail",
                message=f"FAIL — getMe ditolak: {desc}",
                duration_ms=int((time.perf_counter() - t0) * 1000),
            )
        bot_username = me_data.get("result", {}).get("username", "bot")
    except Exception as e:
        return CheckResult(
            name="alert",
            status="fail",
            message=f"FAIL — Gagal hubungi Telegram API: {e}",
            duration_ms=int((time.perf_counter() - t0) * 1000),
        )

    # 2. sendMessage
    try:
        send_resp = requests.post(
            f"{base_url}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": "[Doctor] Health check test — Telegram integration verified.",
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
        send_data = send_resp.json()
        dur = int((time.perf_counter() - t0) * 1000)

        if send_data.get("ok"):
            return CheckResult(
                name="alert",
                status="ok",
                message=f"OK — Telegram bot @{bot_username} aktif, test message terkirim",
                details={"bot_username": bot_username, "chat_id": chat_id},
                duration_ms=dur,
            )
        else:
            desc = send_data.get("description", "Gagal kirim pesan")
            return CheckResult(
                name="alert",
                status="fail",
                message=f"FAIL — sendMessage gagal: {desc}",
                duration_ms=dur,
            )
    except Exception as e:
        return CheckResult(
            name="alert",
            status="fail",
            message=f"FAIL — sendMessage timeout/error: {e}",
            duration_ms=int((time.perf_counter() - t0) * 1000),
        )


# =====================================================================
# 6. Chart Check
# =====================================================================
def check_chart() -> CheckResult:
    """Generate a test chart for a product with history, verify size > 1KB, and clean up."""
    t0 = time.perf_counter()
    db_path = _PROJECT_ROOT / "data" / "price_history.db"

    if not db_path.exists():
        return CheckResult(
            name="chart",
            status="warn",
            message="Database belum ada untuk uji generate chart",
            duration_ms=int((time.perf_counter() - t0) * 1000),
        )

    try:
        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()
        row = cur.execute(
            """
            SELECT p.sku, p.marketplace, COUNT(h.id) as pts
            FROM products p
            JOIN price_history h ON h.product_id = p.id
            GROUP BY p.id
            HAVING pts >= 2
            LIMIT 1
            """
        ).fetchone()
        conn.close()

        if not row:
            return CheckResult(
                name="chart",
                status="warn",
                message="Tidak ada produk dengan >= 2 data point di database",
                duration_ms=int((time.perf_counter() - t0) * 1000),
            )

        sku, marketplace, pts = row[0], row[1], row[2]

        from src.chart import plot_price_history, log as chart_log
        chart_log.setLevel(logging.WARNING)

        chart_path = plot_price_history(sku, marketplace, days=30)
        dur = int((time.perf_counter() - t0) * 1000)

        if not chart_path or not Path(chart_path).exists():
            return CheckResult(
                name="chart",
                status="fail",
                message="Gagal generate chart: file PNG tidak terbuat",
                duration_ms=dur,
            )

        chart_file = Path(chart_path)
        size_bytes = chart_file.stat().st_size
        size_kb = size_bytes / 1024

        # Cleanup test chart
        try:
            chart_file.unlink(missing_ok=True)
        except Exception:
            pass

        if size_bytes > 1024:
            return CheckResult(
                name="chart",
                status="ok",
                message=f"OK — PNG generated ({size_kb:.0f}KB) & verified",
                details={"sku": sku, "size_kb": round(size_kb, 1)},
                duration_ms=dur,
            )
        else:
            return CheckResult(
                name="chart",
                status="fail",
                message=f"File chart terlalu kecil ({size_bytes} bytes)",
                duration_ms=dur,
            )

    except Exception as e:
        return CheckResult(
            name="chart",
            status="fail",
            message=f"FAIL — Error generate chart: {e}",
            duration_ms=int((time.perf_counter() - t0) * 1000),
        )


# =====================================================================
# 7. Scheduler Check (Windows Task Scheduler)
DEFAULT_TASK_CANDIDATES = [
    "Price Tracker - Track",
    "Price Tracker",
    "PriceTracker",
    "PriceTracker - Track",
    "Price Tracker Track",
]


def _find_similar_tasks() -> list[str]:
    """Search Windows Task Scheduler for any tasks containing 'Price' or 'Tracker'."""
    if sys.platform != "win32":
        return []
    try:
        proc = subprocess.run(
            ["schtasks", "/query", "/fo", "LIST"],
            capture_output=True,
            text=True,
            timeout=8,
        )
        if proc.returncode != 0:
            return []
        matches: list[str] = []
        for line in proc.stdout.splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                k_clean = k.strip().lower()
                if "taskname" in k_clean or "nama tugas" in k_clean:
                    name = v.strip().lstrip("\\")
                    lower = name.lower()
                    if "price" in lower or "tracker" in lower:
                        if name not in matches:
                            matches.append(name)
        return matches
    except Exception:
        return []


def _save_task_name_to_config(task_name: str) -> bool:
    """Save task_name to config.toml under [scheduler] section, preserving comments."""
    config_path = _PROJECT_ROOT / "config.toml"
    if not config_path.exists():
        return False

    try:
        content = config_path.read_text(encoding="utf-8")

        # Check if [scheduler] section already exists
        scheduler_pattern = re.compile(r"(\[scheduler\][^\[]*)", re.DOTALL)
        match = scheduler_pattern.search(content)

        if match:
            section_text = match.group(1)
            # Check if task_name already exists in [scheduler]
            if re.search(r"^\s*task_name\s*=", section_text, re.MULTILINE):
                new_section = re.sub(
                    r"^\s*task_name\s*=.*$",
                    f'task_name = "{task_name}"',
                    section_text,
                    flags=re.MULTILINE,
                )
            else:
                new_section = section_text.rstrip() + f'\ntask_name = "{task_name}"\n'
            new_content = content[:match.start()] + new_section + content[match.end():]
        else:
            # Append [scheduler] section
            new_content = content.rstrip() + f'\n\n[scheduler]\ntask_name = "{task_name}"\n'

        if new_content != content:
            config_path.write_text(new_content, encoding="utf-8")
            return True
        return False
    except Exception:
        return False


def check_scheduler(task_name: str | None = None) -> CheckResult:
    """Check if Windows Task Scheduler task exists and is active.

    Args:
        task_name: Optional explicit task name override. If None, checks
            config.toml [scheduler].task_name. If not configured, auto-probes
            DEFAULT_TASK_CANDIDATES in order.
    """
    t0 = time.perf_counter()

    if sys.platform != "win32":
        return CheckResult(
            name="scheduler",
            status="warn",
            message="Task Scheduler hanya tersedia di Windows",
            duration_ms=int((time.perf_counter() - t0) * 1000),
        )

    from src.config import get

    # Priority:
    # 1. Explicit CLI argument (--task-name)
    # 2. Config from config.toml ([scheduler] task_name) -> skip auto-detect
    # 3. Auto-detect from DEFAULT_TASK_CANDIDATES
    configured_name = get("scheduler.task_name", "").strip()

    if task_name:
        candidates = [task_name]
    elif configured_name:
        candidates = [configured_name]
    else:
        candidates = DEFAULT_TASK_CANDIDATES

    matched_name: str | None = None
    proc_output = ""
    last_error_details: dict[str, Any] = {}

    for candidate in candidates:
        try:
            proc = subprocess.run(
                ["schtasks", "/query", "/tn", candidate, "/fo", "LIST", "/v"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if proc.returncode == 0:
                matched_name = candidate
                proc_output = proc.stdout
                break
            else:
                last_error_details[candidate] = proc.stderr.strip()
        except Exception as e:
            last_error_details[candidate] = str(e)

    dur = int((time.perf_counter() - t0) * 1000)

    if not matched_name:
        similar = _find_similar_tasks()

        msg_lines: list[str] = []
        if task_name:
            msg_lines.append(f"Task '{task_name}' tidak ditemukan di Task Scheduler.")
        else:
            msg_lines.append("Task tidak ditemukan dengan nama apapun dari kandidat:")
            for c in DEFAULT_TASK_CANDIDATES:
                msg_lines.append(f"  - {c}")

        if similar:
            msg_lines.append("Task mirip yang ditemukan di sistem:")
            for s in similar:
                msg_lines.append(f"  - {s}")

        msg_lines.append('Saran: jalankan `schtasks /query /fo LIST | findstr /i "Price Tracker"`')
        msg_lines.append('untuk lihat nama task yang sebenarnya.')
        msg_lines.append('Atau jalankan: python doctor.py scheduler --task-name "Nama Task Anda"')
        msg_lines.append('Atau daftarkan otomatis: python doctor.py fix-scheduler')

        return CheckResult(
            name="scheduler",
            status="fail",
            message="\n".join(msg_lines),
            details={
                "tried_candidates": candidates,
                "similar_tasks": similar,
                "errors": last_error_details,
            },
            duration_ms=dur,
        )

    # Persist detected/valid task name to config.toml under [scheduler]
    _save_task_name_to_config(matched_name)

    output = proc_output
    parsed: dict[str, str] = {}
    for line in output.splitlines():
        if ":" in line:
            parts = line.split(":", 1)
            k = parts[0].strip()
            v = parts[1].strip()
            parsed[k.lower()] = v

    # Extract bilingual status & run times (English & Indonesian locale)
    status_val = ""
    last_run_val = ""
    next_run_val = ""
    last_result_val = ""

    for k, v in parsed.items():
        k_clean = k.lower().strip()
        # Status / Status Tugas / Status tugas
        if "status" in k_clean:
            status_val = v.strip()
        # Last Run Time / Waktu Jalankan Terakhir / Waktu Mulai Terakhir
        elif any(term in k_clean for term in ("last run", "terakhir", "mulai terakhir", "jalankan terakhir")):
            if not last_run_val:
                last_run_val = v.strip()
        # Next Run Time / Waktu Jalankan Berikutnya / Waktu Mulai Berikutnya
        elif any(term in k_clean for term in ("next run", "berikutnya", "mulai berikutnya", "jalankan berikutnya")):
            if not next_run_val:
                next_run_val = v.strip()
        # Last Result / Hasil Terakhir
        elif any(term in k_clean for term in ("last result", "hasil terakhir")):
            last_result_val = v.strip()

    details = {
        "task_name": matched_name,
        "status": status_val,
        "last_run": last_run_val,
        "next_run": next_run_val,
        "last_result": last_result_val,
    }

    # Evaluate status (Ready, Running, Siap, Berjalan, Disabled, Dinonaktifkan)
    norm_status = status_val.lower()
    is_ready = norm_status in ("ready", "running", "siap", "berjalan")
    is_disabled = norm_status in ("disabled", "dinonaktifkan")

    if is_disabled:
        return CheckResult(
            name="scheduler",
            status="warn",
            message=f"Task '{matched_name}' dinonaktifkan ({status_val})",
            details=details,
            duration_ms=dur,
        )

    if not is_ready:
        return CheckResult(
            name="scheduler",
            status="warn",
            message=f"Task '{matched_name}' status: {status_val or 'Tidak diketahui'}",
            details=details,
            duration_ms=dur,
        )

    # Check last run time
    if not last_run_val or last_run_val.lower() in ("n/a", "never", "tidak pernah"):
        return CheckResult(
            name="scheduler",
            status="warn",
            message=f"Task '{matched_name}' aktif ({status_val}), tapi belum pernah dijalankan (Last Run: N/A)",
            details=details,
            duration_ms=dur,
        )

    # Try parsing last run date
    last_dt = None
    for fmt in ("%d/%m/%Y %H:%M:%S", "%m/%d/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            last_dt = datetime.strptime(last_run_val, fmt)
            break
        except ValueError:
            continue

    if last_dt:
        now = datetime.now()
        if (now - last_dt) > timedelta(hours=12):
            time_ago_str = _format_time_ago(last_dt, now)
            return CheckResult(
                name="scheduler",
                status="warn",
                message=f"Task '{matched_name}' ditemukan tapi last run {time_ago_str} (> 12 jam lalu)",
                details=details,
                duration_ms=dur,
            )

    return CheckResult(
        name="scheduler",
        status="ok",
        message=f"Task '{matched_name}' aktif ({status_val}), next run: {next_run_val or 'N/A'}",
        details=details,
        duration_ms=dur,
    )


def fix_scheduler_cli(args: argparse.Namespace) -> None:
    """Interactive / non-interactive assistant to register Windows Task Scheduler task."""
    console = Console() if _HAS_RICH else None

    def print_msg(text: str, style: str = "") -> None:
        if _HAS_RICH and console:
            console.print(f"[{style}]{text}[/{style}]" if style else text)
        else:
            print(text)

    if sys.platform != "win32":
        print_msg("⚠ Task Scheduler hanya tersedia di Windows.", "bold yellow")
        sys.exit(1)

    task_name = args.name or "Price Tracker"
    start_time = args.start or "08:00"
    interval_hours = args.interval if args.interval is not None else 6
    batch_path = args.batch or str((_PROJECT_ROOT / "run_track.bat").resolve())
    non_interactive = args.yes

    if not non_interactive:
        print_msg("\n=== Setup Windows Task Scheduler ===", "bold cyan")
        print_msg("Tekan [Enter] untuk menggunakan nilai default dalam tanda kurung [].\n", "dim")

        # 1. Task name
        ans_name = input(f"Nama task [{task_name}]: ").strip()
        if ans_name:
            task_name = ans_name

        # 2. Start time
        ans_start = input(f"Jam mulai (format HH:MM) [{start_time}]: ").strip()
        if ans_start:
            start_time = ans_start

        # 3. Interval in hours
        ans_int = input(f"Interval antar eksekusi dalam jam [{interval_hours}]: ").strip()
        if ans_int:
            try:
                interval_hours = int(ans_int)
            except ValueError:
                print_msg(f"Nilai interval tidak valid, menggunakan default {interval_hours} jam.", "yellow")

        # 4. Batch path
        ans_batch = input(f"Path run_track.bat [{batch_path}]: ").strip()
        if ans_batch:
            batch_path = ans_batch

        # Check batch file existence
        if not Path(batch_path).exists():
            print_msg(f"\n⚠ Peringatan: File batch '{batch_path}' tidak ditemukan!", "bold yellow")
            conf_batch = input("Tetap lanjutkan dengan path ini? (y/n) [y]: ").strip().lower()
            if conf_batch and conf_batch != "y":
                print_msg("Operasi dibatalkan oleh user.", "yellow")
                sys.exit(0)

        # Check if task already exists
        chk_proc = subprocess.run(["schtasks", "/query", "/tn", task_name], capture_output=True, text=True)
        if chk_proc.returncode == 0 and not args.overwrite:
            print_msg(f"\nTask '{task_name}' sudah terdaftar di Task Scheduler.", "yellow")
            ow = input("Apakah Anda ingin menimpa (overwrite) task tersebut? (y/n) [y]: ").strip().lower()
            if ow and ow != "y":
                print_msg("Operasi dibatalkan oleh user.", "yellow")
                sys.exit(0)

        interval_min = interval_hours * 60
        cmd_preview = f'schtasks /create /tn "{task_name}" /tr "\\"{batch_path}\\"" /sc daily /st {start_time} /ri {interval_min} /du 24:00 /f'
        print_msg("\nPreview perintah schtasks yang akan dijalankan:", "bold")
        print_msg(f"  {cmd_preview}\n", "cyan")

        confirm = input("Jalankan pendaftaran task sekarang? (y/n) [y]: ").strip().lower()
        if confirm and confirm != "y":
            print_msg("Operasi dibatalkan oleh user.", "yellow")
            sys.exit(0)

    interval_min = interval_hours * 60
    cmd = [
        "schtasks", "/create",
        "/tn", task_name,
        "/tr", f'"{batch_path}"',
        "/sc", "daily",
        "/st", start_time,
        "/ri", str(interval_min),
        "/du", "24:00",
        "/f",
    ]

    proc = subprocess.run(cmd, capture_output=True, text=True)

    if proc.returncode != 0:
        err_msg = proc.stderr.strip() or proc.stdout.strip()
        print_msg(f"\n✗ Gagal mendaftarkan task: {err_msg}", "bold red")

        if "Access is denied" in err_msg or "Akses ditolak" in err_msg:
            print_msg("\n[Saran Administrator]", "bold yellow")
            print_msg("  Perintah schtasks membutuhkan hak akses Administrator.")
            print_msg("  1. Buka Command Prompt atau PowerShell dengan klik kanan -> 'Run as Administrator'.")
            print_msg("  2. Jalankan perintah pembuatan task secara manual:\n")
            print_msg(f'     schtasks /create /tn "{task_name}" /tr "\\"{batch_path}\\"" /sc daily /st {start_time} /ri {interval_min} /du 24:00 /f\n', "bold cyan")
        elif "already exists" in err_msg or "sudah ada" in err_msg:
            print_msg("\n[Saran Overwrite]", "bold yellow")
            print_msg("  Gunakan flag --overwrite atau tambahkan parameter /f pada schtasks.")
        sys.exit(2)

    # Verification after create
    v_proc = subprocess.run(["schtasks", "/query", "/tn", task_name, "/fo", "LIST", "/v"], capture_output=True, text=True)
    status_val = "Ready"
    next_run_val = "-"
    if v_proc.returncode == 0:
        for line in v_proc.stdout.splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                k_clean = k.lower().strip()
                if "status" in k_clean and not status_val:
                    status_val = v.strip()
                elif any(term in k_clean for term in ("next run", "berikutnya")):
                    next_run_val = v.strip()

    # Save to config.toml
    _save_task_name_to_config(task_name)

    print_msg(f"\n✓ Task '{task_name}' berhasil didaftarkan dan diverifikasi!", "bold green")
    print_msg(f"  • Status: {status_val}", "cyan")
    print_msg(f"  • Next Run: {next_run_val}", "cyan")
    print_msg(f"  • Konfigurasi: Disimpan ke config.toml di section [scheduler] (task_name = \"{task_name}\")\n", "green")


# =====================================================================
# 8. Logs Check
# =====================================================================
def check_logs() -> CheckResult:
    """Parse log files for errors and warnings in the last 24 hours."""
    t0 = time.perf_counter()
    logs_dir = _PROJECT_ROOT / "data" / "logs"
    target_files = [logs_dir / "track.log", logs_dir / "main.log"]

    now = datetime.now()
    cutoff = now - timedelta(hours=24)

    total_errors = 0
    total_warns = 0
    last_error_messages: list[str] = []

    # Format: YYYY-MM-DD HH:MM:SS | name | LEVEL | message
    line_pattern = re.compile(
        r"^(\d{4}-\d{2}-\d{2}\s\d{2}:\d{2}:\d{2})\s*\|\s*([^|]+)\s*\|\s*([A-Z]+)\s*\|\s*(.*)$"
    )

    for log_path in target_files:
        if not log_path.exists():
            continue
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    match = line_pattern.match(line.strip())
                    if not match:
                        continue
                    ts_str, _, level, msg = match.groups()
                    try:
                        log_dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
                    except ValueError:
                        continue

                    if log_dt >= cutoff:
                        if level == "ERROR" or level == "CRITICAL":
                            total_errors += 1
                            if len(last_error_messages) < 5:
                                last_error_messages.append(f"{ts_str}: {msg}")
                        elif level == "WARNING":
                            total_warns += 1
        except Exception:
            pass

    dur = int((time.perf_counter() - t0) * 1000)
    details = {
        "errors_24h": total_errors,
        "warnings_24h": total_warns,
        "sample_errors": last_error_messages,
    }

    if total_errors > 5:
        return CheckResult(
            name="logs",
            status="fail",
            message=f"{total_errors} ERROR dalam 24h terakhir (lihat: data/logs/track.log)",
            details=details,
            duration_ms=dur,
        )
    elif total_errors >= 1:
        return CheckResult(
            name="logs",
            status="warn",
            message=f"{total_errors} ERROR, {total_warns} WARNING dalam 24h terakhir",
            details=details,
            duration_ms=dur,
        )
    else:
        return CheckResult(
            name="logs",
            status="ok",
            message=f"0 ERROR, {total_warns} WARNING dalam 24h terakhir",
            details=details,
            duration_ms=dur,
        )


# =====================================================================
# Registry & Runner
# =====================================================================
CHECKS: dict[str, Callable[[], CheckResult | list[CheckResult]]] = {
    "env": check_env,
    "db": check_db,
    "cookies": check_cookies,
    "scrapers": check_scrapers,
    "alert": check_alert,
    "chart": check_chart,
    "scheduler": check_scheduler,
    "logs": check_logs,
}


def run_all(
    selected: list[str] | None = None,
    task_name: str | None = None,
) -> list[CheckResult]:
    """Execute selected or all checks in order."""
    keys_to_run = selected if selected else list(CHECKS.keys())
    results: list[CheckResult] = []

    for key in keys_to_run:
        fn = CHECKS.get(key)
        if not fn:
            results.append(
                CheckResult(
                    name=key,
                    status="fail",
                    message=f"Unknown check: '{key}'",
                )
            )
            continue

        try:
            if key == "scheduler":
                res = check_scheduler(task_name=task_name)
            else:
                res = fn()
            if isinstance(res, list):
                results.extend(res)
            else:
                results.append(res)
        except Exception as e:
            results.append(
                CheckResult(
                    name=key,
                    status="fail",
                    message=f"Crash during check: {e}",
                )
            )

    return results


def print_summary(results: list[CheckResult]) -> None:
    """Print results using rich table and clean summary lines."""
    if _HAS_RICH:
        console = Console()
        table = Table(title="Price Tracker — Doctor Health Check", show_header=True)
        table.add_column("Status", width=6, justify="center")
        table.add_column("Check", style="bold cyan", width=14)
        table.add_column("Message", style="white")
        table.add_column("Time", justify="right", style="dim", width=8)

        for r in results:
            if r.status == "ok":
                st = Text("✓ OK", style="bold green")
            elif r.status == "warn":
                st = Text("⚠ WARN", style="bold yellow")
            else:
                st = Text("✗ FAIL", style="bold red")

            dur_str = f"{r.duration_ms}ms" if r.duration_ms > 0 else "<1ms"
            table.add_row(st, r.name, r.message, dur_str)

        console.print()
        console.print(table)
        console.print()

        console.print("[bold]Ringkasan akhir:[/bold]")
        for r in results:
            if r.status == "ok":
                icon = "[bold green]✓[/bold green]"
            elif r.status == "warn":
                icon = "[bold yellow]⚠[/bold yellow]"
            else:
                icon = "[bold red]✗[/bold red]"
            name_padded = f"{r.name:<13}"
            console.print(f"  {icon} {name_padded} {r.message}")
        console.print()
    else:
        # Plain text fallback
        print("\n=== Price Tracker — Doctor Health Check ===\n")
        print("Ringkasan akhir:")
        for r in results:
            if r.status == "ok":
                icon = "[✓]"
            elif r.status == "warn":
                icon = "[!]"
            else:
                icon = "[X]"
            print(f"  {icon} {r.name:<13} {r.message}")
        print()


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "fix-scheduler":
        fix_parser = argparse.ArgumentParser(
            prog="doctor.py fix-scheduler",
            description="Setup dan daftarkan Windows Task Scheduler secara otomatis via schtasks.",
        )
        fix_parser.add_argument(
            "--name",
            type=str,
            default="Price Tracker",
            help='Nama task di Task Scheduler (default: "Price Tracker")',
        )
        fix_parser.add_argument(
            "--start",
            type=str,
            default="08:00",
            help='Jam mulai format HH:MM (default: "08:00")',
        )
        fix_parser.add_argument(
            "--interval",
            type=int,
            default=6,
            help="Interval antar eksekusi dalam jam (default: 6)",
        )
        fix_parser.add_argument(
            "--batch",
            type=str,
            default=None,
            help="Path lengkap file run_track.bat (default: path di root project)",
        )
        fix_parser.add_argument(
            "--yes",
            "-y",
            action="store_true",
            help="Non-interactive mode, langsung jalankan tanpa konfirmasi prompt",
        )
        fix_parser.add_argument(
            "--overwrite",
            action="store_true",
            help="Otomatis overwrite (timpa) jika task dengan nama tersebut sudah ada",
        )
        fix_args = fix_parser.parse_args(sys.argv[2:])
        fix_scheduler_cli(fix_args)
        return

    parser = argparse.ArgumentParser(
        description="Doctor — Self-diagnostic health check for price-tracker pipeline."
    )
    parser.add_argument(
        "check",
        nargs="?",
        choices=[
            "env",
            "db",
            "cookies",
            "scrapers",
            "alert",
            "chart",
            "scheduler",
            "logs",
        ],
        help="Run a specific check (default: run all)",
    )
    parser.add_argument(
        "--task-name",
        type=str,
        default=None,
        help="Override Windows Task Scheduler task name explicitly",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output check results as pure JSON",
    )

    args = parser.parse_args()

    # Mute routine console loggers so doctor output is clean
    import logging
    if args.json:
        logging.disable(logging.CRITICAL)
    else:
        for log_name in ("scraper", "scraper.blibli", "scraper.lazada", "scraper.tokopedia", "scraper.shopee", "db", "chart", "alerts"):
            lg = logging.getLogger(log_name)
            lg.setLevel(logging.WARNING)
            for h in lg.handlers:
                if type(h) is logging.StreamHandler:
                    h.setLevel(logging.WARNING)

    selected = [args.check] if args.check else None
    results = run_all(selected=selected, task_name=args.task_name)

    if args.json:
        out = [r.to_dict() for r in results]
        print(json.dumps(out, indent=2, ensure_ascii=False))
    else:
        print_summary(results)

    # Determine exit code:
    # 0 = all ok, 1 = any warn, 2 = any fail
    has_fail = any(r.status == "fail" for r in results)
    has_warn = any(r.status == "warn" for r in results)

    if has_fail:
        sys.exit(2)
    elif has_warn:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
