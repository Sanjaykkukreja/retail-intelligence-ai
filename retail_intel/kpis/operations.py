"""Fulfilment and order-to-cash KPIs."""
from __future__ import annotations

from ..metrics import finance
from ..metrics import funnel_fulfilment as ff
from ..metrics.common import as_of, shift
from ..platform.registry import kpi


def _gap(conn, name):
    return next(g for g in finance.order_to_cash(conn)["gaps"] if g["gap"] == name)


@kpi("paid_not_dispatched", "Paid orders not dispatched", "count", "down")
def paid_not_dispatched(conn):
    return _gap(conn, "paid_not_dispatched")["orders"]


@kpi("paid_not_dispatched_value", "Value of paid orders not dispatched (ex-GST)", "inr", "down")
def paid_not_dispatched_value(conn):
    return _gap(conn, "paid_not_dispatched")["billed_value_ex_gst"]


@kpi("dispatched_not_billed", "Shipments with no invoice", "count", "down")
def dispatched_not_billed(conn):
    return _gap(conn, "dispatched_not_billed")["orders"]


@kpi("avg_dispatch_hours_7d", "Average hours to dispatch, last 7 days", "hours", "down")
def avg_dispatch_hours_7d(conn):
    a = as_of(conn)
    d = ff.daily_fulfilment(conn, shift(a, -6), a)
    return float((d["avg_dispatch_hours"] * d["orders"]).sum() / d["orders"].sum()) if d["orders"].sum() else None
