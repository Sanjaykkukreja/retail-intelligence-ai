"""Website funnel and order fulfilment speed. Window detectors live in findings.py."""
from __future__ import annotations

import pandas as pd

from .. import db
from .common import context


def daily_funnel(conn, start: str, end: str) -> pd.DataFrame:
    f = db.read(conn, """
        select date, sum(views) as item_views, sum(atc) as add_to_cart, sum(checkouts) as checkout_units,
               sum(purchases) as purchase_units
        from fact_web_daily where date between ? and ? group by date order by date""", (start, end))
    s = db.read(conn, "select date, sessions from fact_web_sessions where date between ? and ?", (start, end))
    f = f.merge(s, on="date", how="left")
    f["checkout_to_purchase"] = (f["purchase_units"] / f["checkout_units"]).where(f["checkout_units"] > 0)
    return f


def daily_fulfilment(conn, start: str, end: str) -> pd.DataFrame:
    """Per order date: how long orders waited for dispatch, cancellations and returned-to-origin (RTO) rates."""
    sla = context(conn)["dispatch_sla_hours"]
    o = db.read(conn, """
        select order_ref, order_date, max(dispatch_hours) as hours, max(is_cancelled) as cancelled, max(is_rto) as rto
        from fact_order_line where order_date between ? and ? group by order_ref, order_date""", (start, end))
    g = o.groupby("order_date")
    out = pd.DataFrame({
        "orders": g["order_ref"].count(),
        "avg_dispatch_hours": g["hours"].mean().round(1),
        "pct_over_sla": g["hours"].apply(lambda s: round(100 * (s.dropna() > sla).mean(), 1) if s.notna().any() else None),
        "cancel_rate_pct": (100 * g["cancelled"].mean()).round(1),
        "rto_rate_pct": (100 * g["rto"].mean()).round(1),
    }).reset_index().rename(columns={"order_date": "date"})
    return out
