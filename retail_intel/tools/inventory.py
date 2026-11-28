"""Stock, stockout, expiry and markdown tools."""
from __future__ import annotations

from .. import db
from ..metrics import inventory
from ..platform.registry import tool

LIMIT = {"limit": {"type": "integer"}}


def resolve_sku(conn, sku: str) -> str:
    r = db.read(conn, "select master_sku_id from dim_product where master_sku_id = ? or lower(name) like ?",
                (sku, f"%{sku.lower()}%"))
    if r.empty:
        raise ValueError(f"no product matches '{sku}'. Use stock_position to list product names.")
    return r["master_sku_id"].iloc[0]


@tool("stock_position", "Units on hand, 28-day run-rate and days of cover per SKU, lowest cover first. "
      "Optional product-name filter.", {"name_contains": {"type": "string"}, **LIMIT})
def stock_position(conn, name_contains="", limit=15):
    p = inventory.position(conn)
    p = p[p["name"].str.lower().str.contains((name_contains or "").lower(), regex=False)]
    return p.sort_values("days_of_supply").head(limit)


@tool("stockout_events", "Periods when a SKU had zero stock (ongoing ones flagged), with estimated lost units and revenue.",
      {"min_days": {"type": "integer"}, **LIMIT})
def stockout_events(conn, min_days=2, limit=15):
    return inventory.stockout_events(conn, min_days=min_days, min_lost_units=5).head(limit)


@tool("expiry_exposure", "Batches expiring within the horizon and units that will not sell before expiry.", LIMIT)
def expiry_exposure(conn, limit=15):
    return inventory.expiry_exposure(conn).head(limit)


@tool("markdown_scenarios", "Outcome of 0-40% discounts on an expiring batch. Uses an ASSUMED price elasticity.",
      {"sku": {"type": "string", "description": "master SKU id or product name fragment"}, "batch_no": {"type": "string"}},
      ["sku"])
def markdown_scenarios(conn, sku, batch_no=None):
    return inventory.markdown_scenarios(conn, resolve_sku(conn, sku), batch_no)


@tool("slow_movers", "SKUs holding more than the slow-mover threshold of days of supply.", LIMIT)
def slow_movers(conn, limit=15):
    return inventory.slow_movers(conn).head(limit)
