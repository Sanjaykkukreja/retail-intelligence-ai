"""Sales and margin KPIs over the last 28 days."""
from __future__ import annotations

from ..metrics import sales
from ..metrics.common import as_of, context, shift
from ..platform.registry import kpi


def _summary(conn) -> dict:
    a = as_of(conn)
    return sales.summary(conn, shift(a, -27), a)


@kpi("net_revenue_28d", "Net sales, last 28 days (ex-GST)", "inr", "up")
def net_revenue_28d(conn):
    return _summary(conn)["net_revenue"]


@kpi("gross_margin_pct_28d", "Gross margin, last 28 days", "pct", "up")
def gross_margin_pct_28d(conn):
    return _summary(conn)["gross_margin_pct"]


@kpi("avg_discount_pct_28d", "Average discount, last 28 days", "pct", "down")
def avg_discount_pct_28d(conn):
    return _summary(conn)["discount_pct"]


@kpi("refunds_28d", "Refunds, last 28 days (ex-GST)", "inr", "down")
def refunds_28d(conn):
    return _summary(conn)["refunds"]


@kpi("leakage_shortfall_28d", "Margin lost to heavy discounts, last 28 days", "inr", "down")
def leakage_shortfall_28d(conn):
    """Shortfall versus the target gross margin on SKUs sold at 25%+ discount."""
    a = as_of(conn)
    lk = sales.discount_leakage(conn, shift(a, -27), a, context(conn)["gross_margin_target_pct"])
    return float(lk["shortfall_vs_target_inr"].sum()) if len(lk) else 0.0
