"""Layer 1 - Ingest. Copy each source file into a raw_* table exactly as it arrived (all text).

Nothing is cleaned here on purpose: if a number looks wrong later, we can always look at what the
client system really sent. Every load replaces the raw tables (full refresh is fine at pilot scale).
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd

from .. import db

# (folder, file stem, raw table)
SOURCES = [
    ("website", "web_items", "raw_web_items"),
    ("website", "web_daily_items", "raw_web_daily_items"),
    ("website", "web_purchases", "raw_web_purchases"),
    ("website", "web_sessions", "raw_web_sessions"),
    ("billing", "billing_invoices", "raw_billing_invoices"),
    ("billing", "billing_refunds", "raw_billing_refunds"),
    ("wms", "wms_products", "raw_wms_products"),
    ("wms", "wms_stock_snapshots", "raw_wms_snapshots"),
    ("wms", "wms_receipts", "raw_wms_receipts"),
    ("wms", "wms_dispatch", "raw_wms_dispatch"),
]


def load_raw(conn, source_dir: str | Path) -> dict[str, int]:
    """Load every source file. Returns rows loaded per table. A missing file is an error, not a silent skip."""
    source_dir = Path(source_dir)
    loaded_at = datetime.now().isoformat(timespec="seconds")
    rows = {}
    for folder, stem, table in SOURCES:
        path = source_dir / folder / f"{stem}.csv"
        if not path.exists():
            raise FileNotFoundError(f"Source file missing: {path}")
        df = pd.read_csv(path, dtype=str, keep_default_na=False)
        df["_source_file"] = f"{folder}/{stem}.csv"
        df["_loaded_at"] = loaded_at
        db.write(conn, df, table)
        rows[table] = len(df)
    conn.commit()
    return rows
