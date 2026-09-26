"""Database migration script.

Handles schema updates when the database structure changes.
Uses a simple version-based migration system stored in a meta table.

Usage:
    python scripts/migrate_db.py
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import get, project_root

MIGRATIONS = [
    # (version, description, sql)
    (
        1,
        "Initial schema",
        """
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
        """,
    ),
    (
        2,
        "Add created_at to products",
        """
        ALTER TABLE products ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;
        """,
    ),
]


def get_db_path() -> str:
    raw = get("db.path", "data/price_history.db")
    path = Path(raw)
    if not path.is_absolute():
        path = project_root() / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return str(path)


def get_current_version(conn: sqlite3.Connection) -> int:
    """Get the current schema version from the meta table."""
    try:
        row = conn.execute(
            "SELECT MAX(version) FROM schema_migrations"
        ).fetchone()
        return row[0] if row[0] is not None else 0
    except sqlite3.OperationalError:
        # Table doesn't exist yet
        return 0


def ensure_meta_table(conn: sqlite3.Connection) -> None:
    """Create the schema_migrations tracking table."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            description TEXT,
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.commit()


def migrate() -> None:
    db_path = get_db_path()
    print(f"Database: {db_path}")

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")

    ensure_meta_table(conn)
    current = get_current_version(conn)
    print(f"Current schema version: {current}")

    pending = [(v, d, s) for v, d, s in MIGRATIONS if v > current]

    if not pending:
        print("Database is up to date. No migrations needed.")
        conn.close()
        return

    for version, description, sql in pending:
        print(f"  Applying migration v{version}: {description}...")
        try:
            conn.executescript(sql)
            conn.execute(
                "INSERT INTO schema_migrations (version, description) VALUES (?, ?)",
                (version, description),
            )
            conn.commit()
            print(f"  [OK] v{version} applied.")
        except sqlite3.OperationalError as e:
            # Handle "duplicate column" errors gracefully (idempotent migrations)
            if "duplicate column" in str(e).lower():
                print(f"  [SKIP] v{version}: column already exists.")
                conn.execute(
                    "INSERT OR IGNORE INTO schema_migrations (version, description) VALUES (?, ?)",
                    (version, description),
                )
                conn.commit()
            else:
                print(f"  [ERROR] v{version} failed: {e}")
                conn.close()
                sys.exit(1)

    final = get_current_version(conn)
    print(f"\nMigration complete. Schema version: {final}")
    conn.close()


if __name__ == "__main__":
    from src.config import load_config
    load_config()
    migrate()
