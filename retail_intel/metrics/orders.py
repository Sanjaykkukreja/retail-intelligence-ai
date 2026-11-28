"""Order-level and stock-productivity metrics. These back two question families:

  order_list    one tool for many order questions: delayed, cancelled, returned-to-origin, not dispatched,
                taken while the product was out of stock; filtered by age, payment mode and period; listed or grouped
  sell_through  sell-through, stock turn, cash in stock and GMROI by SKU or category for a period
"""
from __future__ import annotations

import pandas as pd

from .. import db
from .common import as_of as _as_of, context

STATUSES = ["all", "delayed", "dispatched_late", "not_dispatched", "cancelled", "rto", "out_of_stock_at_order"]
GROUPS = [None, "payment_mode", "month", "status", "category", "day"]


def parse_order_question(q: str) -> dict:
    """Settings for order_list read from the question's own words (the model can still choose others)."""
    import re
    ql = q.lower()
    status = ("rto" if re.search(r"\brto\b|returned to origin", ql) else
              "cancelled" if re.search(r"cancel", ql) else
              "out_of_stock_at_order" if re.search(r"out of stock|backorder|no (stock|inventory)|inventory (was )?not available", ql) else
              "not_dispatched" if re.search(r"not (yet )?(dispatched|shipped)|pending|unshipped", ql) else
              "delayed" if re.search(r"delay|late\b", ql) else "all")
    m = re.search(r"(\d+)\s*(day|hour)", ql) if status == "delayed" else None
    delay = None if not m else float(m.group(1)) * (24 if m.group(2) == "day" else 1)
    pay = next((p for p in ("COD", "UPI", "Card") if re.search(r"\b" + p.lower() + r"\b", ql)), None)
    group = next((g for w, g in (("by payment", "payment_mode"), ("by month", "month"), ("by category", "category"),
                                 ("by day", "day"), ("per day", "day"), ("daily", "day")) if w in ql), None)
    return dict(order_status=status, delay_hours=delay, payment_mode=pay, order_group=group)


def order_frame(conn) -> pd.DataFrame:
    """One row per order, with the facts every order question needs."""
    lines = db.read(conn, """select l.order_ref, l.order_date, l.master_sku_id, l.qty, l.taxable_value, l.payment_mode,
                                    l.is_cancelled, l.is_rto, l.wms_status, l.received_at, l.dispatched_at,
                                    l.dispatch_hours, p.category
                             from fact_order_line l left join dim_product p using (master_sku_id)""")
    stock = db.read(conn, "select date, master_sku_id, sum(qty) as on_hand from fact_inventory_daily group by 1, 2")
    lines = lines.merge(stock, left_on=["order_date", "master_sku_id"], right_on=["date", "master_sku_id"], how="left")
    lines["oos"] = lines["on_hand"].fillna(0) <= 0
    o = lines.groupby("order_ref").agg(
        order_date=("order_date", "first"), payment_mode=("payment_mode", "first"), value_ex_gst=("taxable_value", "sum"),
        units=("qty", "sum"), lines=("qty", "size"), cancelled=("is_cancelled", "max"), rto=("is_rto", "max"),
        wms_status=("wms_status", "first"), dispatched_at=("dispatched_at", "max"), dispatch_hours=("dispatch_hours", "max"),
        category=("category", "first"), out_of_stock_at_order=("oos", "any")).reset_index()
    o["cancelled"] = o["cancelled"].astype(bool)
    o["rto"] = o["rto"].astype(bool)
    o["not_dispatched"] = ~o["cancelled"] & (o["wms_status"].isna() | (o["wms_status"] == "PENDING"))
    return o


def _status_label(r) -> str:
    if r["cancelled"]:
        return "cancelled"
    if r["rto"]:
        return "returned to origin"
    if r["not_dispatched"]:
        return "not dispatched"
    return "dispatched"


def order_list(conn, status: str = "all", min_age_days: int | None = None, max_age_days: int | None = None,
               payment_mode: str | None = None, start: str | None = None, end: str | None = None,
               delay_over_hours: float | None = None, group_by: str | None = None, limit: int = 20) -> dict:
    """Filter orders, then either list them (newest first) or count them by a grouping. Always returns totals."""
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    if group_by not in GROUPS:
        raise ValueError(f"group_by must be one of {[g for g in GROUPS if g]}")
    as_of = _as_of(conn)
    sla = float(delay_over_hours if delay_over_hours is not None else context(conn)["dispatch_sla_hours"])
    o = order_frame(conn)
    o["age_days"] = (pd.Timestamp(as_of) - pd.to_datetime(o["order_date"])).dt.days
    late = o["dispatch_hours"].fillna(0) > sla
    waiting_too_long = o["not_dispatched"] & (o["age_days"] * 24 > sla)
    keep = {"all": pd.Series(True, index=o.index), "dispatched_late": late & ~o["not_dispatched"],
            "delayed": late | waiting_too_long, "not_dispatched": o["not_dispatched"], "cancelled": o["cancelled"],
            "rto": o["rto"], "out_of_stock_at_order": o["out_of_stock_at_order"]}[status]
    if min_age_days is not None:
        keep &= o["age_days"] >= int(min_age_days)
    if max_age_days is not None:
        keep &= o["age_days"] <= int(max_age_days)
    if payment_mode:
        keep &= o["payment_mode"].str.lower() == payment_mode.lower()
    if start:
        keep &= o["order_date"] >= start
    if end:
        keep &= o["order_date"] <= end
    hit = o[keep].copy()
    hit["status"] = hit.apply(_status_label, axis=1)
    hit["month"] = hit["order_date"].str[:7]
    hit["day"] = hit["order_date"]
    out = dict(filters=dict(status=status, min_age_days=min_age_days, max_age_days=max_age_days, payment_mode=payment_mode,
                            start=start, end=end, delay_threshold_hours=sla if status in ("delayed", "dispatched_late") else None),
               definition=_DEFINITIONS[status].format(sla=f"{sla:g}"), as_of=as_of,
               orders=int(len(hit)), value_ex_gst=round(float(hit["value_ex_gst"].sum()), 2),
               share_of_all_orders_pct=round(100 * len(hit) / max(len(o), 1), 2))
    if group_by:
        g = hit.groupby(group_by).agg(orders=("order_ref", "size"), value_ex_gst=("value_ex_gst", "sum")).reset_index()
        out["groups"] = g.sort_values(group_by if group_by in ("month", "day") else "orders",
                                      ascending=group_by in ("month", "day")).round(2)
    else:
        cols = ["order_ref", "order_date", "age_days", "status", "payment_mode", "value_ex_gst", "units",
                "dispatch_hours", "out_of_stock_at_order"]
        out["orders_list"] = hit.sort_values("order_date", ascending=False)[cols].head(limit).round(2)
        out["list_truncated"] = len(hit) > limit
    return out


_DEFINITIONS = {
    "all": "All orders in billing.",
    "delayed": "Dispatched more than {sla} hours after the order was received, or still not dispatched {sla}+ hours after ordering (cancelled orders excluded).",
    "dispatched_late": "Dispatched, but more than {sla} hours after the order was received.",
    "not_dispatched": "Not cancelled, and the warehouse has not dispatched it (missing in the warehouse or PENDING).",
    "cancelled": "Cancelled in billing.",
    "rto": "Returned to origin by the courier (delivery failed).",
    "out_of_stock_at_order": "At least one product in the order showed zero stock in the warehouse's end-of-day count on the order day (this includes the day it sold out, so it overstates true backorders).",
}


def sell_through(conn, start: str, end: str, group_by: str = "category", limit: int = 20) -> pd.DataFrame:
    """Per category (or SKU) for the period:
       sell_through_pct = units sold / (units sold + units on hand at period end)
       stock_turn_annual = cost of goods sold / average stock at cost, scaled to a year
       gmroi_annual     = gross margin / average stock at cost, scaled to a year  (margin earned per rupee of stock)
       stock_value_end  = stock on hand at cost at period end (cash tied up)"""
    if group_by not in ("category", "sku"):
        raise ValueError("group_by must be category or sku")
    key = "category" if group_by == "category" else "master_sku_id"
    sales = db.read(conn, """select l.master_sku_id, p.name, p.category, sum(l.qty) units_sold, sum(l.cogs) cogs,
                                    sum(l.gross_margin) gross_margin, sum(l.net_revenue) net_revenue
                             from fact_order_line l join dim_product p using (master_sku_id)
                             where l.recognized = 1 and l.order_date between ? and ? group by 1, 2, 3""", (start, end))
    inv = db.read(conn, """select i.date, i.master_sku_id, p.name, p.category, sum(i.qty) qty, sum(i.qty * i.unit_cost) value
                           from fact_inventory_daily i join dim_product p using (master_sku_id)
                           where i.date between ? and ? group by 1, 2, 3, 4""", (start, end))
    days = max((pd.Timestamp(end) - pd.Timestamp(start)).days + 1, 1)
    last = inv["date"].max()
    end_stock = inv[inv["date"] == last].groupby(key).agg(units_on_hand_end=("qty", "sum"), stock_value_end=("value", "sum"))
    avg_value = inv.groupby([key, "date"])["value"].sum().groupby(level=0).mean().rename("avg_stock_value")
    s = sales.groupby(key).agg(units_sold=("units_sold", "sum"), net_revenue=("net_revenue", "sum"), cogs=("cogs", "sum"),
                               gross_margin=("gross_margin", "sum"))
    df = s.join(end_stock, how="outer").join(avg_value, how="outer").fillna(0)
    df["sell_through_pct"] = 100 * df["units_sold"] / (df["units_sold"] + df["units_on_hand_end"]).replace(0, pd.NA)
    df["stock_turn_annual"] = (df["cogs"] / df["avg_stock_value"].replace(0, pd.NA)) * 365 / days
    df["gmroi_annual"] = (df["gross_margin"] / df["avg_stock_value"].replace(0, pd.NA)) * 365 / days
    df = df.reset_index()
    if key == "master_sku_id":
        names = db.read(conn, "select master_sku_id, name, category from dim_product")
        df = df.merge(names, on="master_sku_id", how="left")
    cols = ([key] if key == "category" else ["master_sku_id", "name", "category"]) + \
        ["units_sold", "units_on_hand_end", "sell_through_pct", "net_revenue", "gross_margin", "stock_value_end",
         "avg_stock_value", "stock_turn_annual", "gmroi_annual"]
    return df[cols].sort_values("gmroi_annual", ascending=False).head(limit if key != "category" else 100) \
        .astype({c: float for c in cols[-8:]}).round(2)
