"""CSV export files (the demo data, or any client who can send exports to a folder or Drive).

This is the fallback that always works, even when a vendor tightens its API. Today it feeds the raw layer that the
reconcile and core steps expect; API connectors will land data in the same standard streams.
"""
from __future__ import annotations

from pathlib import Path

from ..ingest.load_raw import SOURCES, load_raw
from ..platform.connector import Connector
from ..platform.registry import connector


@connector
class CsvFiles(Connector):
    name = "csv_files"
    label = "CSV exports (website, billing, warehouse)"
    status = "ready"
    read_only = True
    settings_help = "source_dir: folder with website/, billing/ and wms/ sub-folders of CSV exports"
    streams = {
        "products": "wms/wms_products.csv + website/web_items.csv",
        "orders": "billing/billing_invoices.csv (order level)",
        "order_lines": "billing/billing_invoices.csv",
        "invoices": "billing/billing_invoices.csv",
        "refunds": "billing/billing_refunds.csv",
        "stock_daily": "wms/wms_stock_snapshots.csv",
        "batches": "wms/wms_stock_snapshots.csv (batch, expiry)",
        "receipts": "wms/wms_receipts.csv (goods received)",
        "dispatches": "wms/wms_dispatch.csv",
        "web_sessions": "website/web_sessions.csv + web_daily_items.csv",
        "web_purchases": "website/web_purchases.csv",
    }

    def test(self, settings: dict) -> tuple[bool, str]:
        src = Path(settings.get("source_dir", ""))
        missing = [f"{f}/{s}.csv" for f, s, _ in SOURCES if not (src / f / f"{s}.csv").exists()]
        return (not missing, "all files present" if not missing else "missing: " + ", ".join(missing))

    def sync(self, conn, settings: dict, since: str | None = None) -> dict:
        return load_raw(conn, settings["source_dir"])      # full refresh: fine at pilot scale
