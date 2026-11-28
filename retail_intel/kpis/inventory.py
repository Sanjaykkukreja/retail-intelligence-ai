"""Inventory and replenishment KPIs."""
from __future__ import annotations

from ..metrics import inventory
from ..platform.registry import kpi


@kpi("low_cover_skus", "SKUs under a week of cover", "count", "down")
def low_cover_skus(conn):
    """Selling SKUs with fewer days of stock than the low-stock rule."""
    return len(inventory.low_stock(conn))


@kpi("ongoing_stockouts", "SKUs out of stock now", "count", "down")
def ongoing_stockouts(conn):
    ev = inventory.stockout_events(conn, min_days=2, min_lost_units=5)
    return int(ev["ongoing"].astype(bool).sum()) if len(ev) else 0


@kpi("stockout_lost_sales", "Sales being lost to current stockouts (est.)", "inr", "down")
def stockout_lost_sales(conn):
    """Estimated revenue lost so far on stockouts that are still ongoing (pre-stockout sales rate x days out)."""
    ev = inventory.stockout_events(conn, min_days=2, min_lost_units=5)
    return float(ev.loc[ev["ongoing"].astype(bool), "est_lost_revenue"].sum()) if len(ev) else 0.0


@kpi("expiry_value_at_risk", "Stock that will expire unsold (at cost)", "inr", "down")
def expiry_value_at_risk(conn):
    ex = inventory.expiry_exposure(conn)
    return float(ex["value_at_risk_at_cost"].sum()) if len(ex) else 0.0


@kpi("slow_mover_stock_value", "Cash tied up in slow movers (at cost)", "inr", "down")
def slow_mover_stock_value(conn):
    sm = inventory.slow_movers(conn)
    return float(sm["stock_value_at_cost"].sum()) if len(sm) else 0.0
