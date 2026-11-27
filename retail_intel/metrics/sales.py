"""Sales and margin numbers. Definitions live in core/build_core.py (recognized line, net revenue, gross margin)."""
from __future__ import annotations

import pandas as pd

from .. import db
from .common import shift


def summary(conn, start: str, end: str) -> dict:
    r = db.read(conn, """
        select count(distinct case when recognized then order_ref end) as orders,
               coalesce(sum(case when recognized then qty end), 0) as units,
               coalesce(sum(case when recognized then gross_value end), 0) as gross_sales,
               coalesce(sum(case when recognized then discount_amt end), 0) as discounts,
               coalesce(sum(case when recognized then refund_ex_tax end), 0) as refunds,
               coalesce(sum(net_revenue), 0) as net_revenue,
               coalesce(sum(cogs), 0) as cogs,
               coalesce(sum(gross_margin), 0) as gross_margin,
               count(distinct case when is_cancelled then order_ref end) as cancelled_orders,
               count(distinct case when is_rto then order_ref end) as rto_orders,
               count(distinct order_ref) as billed_orders
        from fact_order_line where order_date between ? and ?""", (start, end)).iloc[0].to_dict()
    r["aov"] = r["net_revenue"] / r["orders"] if r["orders"] else 0.0
    r["gross_margin_pct"] = 100 * r["gross_margin"] / r["net_revenue"] if r["net_revenue"] else 0.0
    r["discount_pct"] = 100 * r["discounts"] / r["gross_sales"] if r["gross_sales"] else 0.0
    r["start"], r["end"] = start, end
    return {k: (round(float(v), 2) if isinstance(v, (int, float)) else v) for k, v in r.items()}


def daily(conn, start: str, end: str) -> pd.DataFrame:
    return db.read(conn, """
        select order_date as date,
               count(distinct case when recognized then order_ref end) as orders,
               sum(net_revenue) as net_revenue, sum(cogs) as cogs, sum(gross_margin) as gross_margin
        from fact_order_line where order_date between ? and ? group by order_date order by order_date""", (start, end))


def day_vs_baseline(conn, day: str) -> dict:
    """One day against the previous day, the trailing 7-day average and the same weekday over 4 weeks."""
    today = summary(conn, day, day)
    prev = summary(conn, shift(day, -1), shift(day, -1))
    d7 = daily(conn, shift(day, -7), shift(day, -1))
    wk = daily(conn, shift(day, -28), shift(day, -1))
    wk = wk[pd.to_datetime(wk["date"]).dt.dayofweek == pd.Timestamp(day).dayofweek]
    avg7 = d7["net_revenue"].mean() if len(d7) else 0.0
    avgwk = wk["net_revenue"].mean() if len(wk) else 0.0

    def pct(a, b):
        return round(100 * (a - b) / b, 1) if b else None

    return dict(day=day, today=today, previous_day=prev,
                net_revenue_vs_prev_day_pct=pct(today["net_revenue"], prev["net_revenue"]),
                net_revenue_vs_7d_avg_pct=pct(today["net_revenue"], avg7),
                net_revenue_vs_same_weekday_pct=pct(today["net_revenue"], avgwk),
                avg_7d_net_revenue=round(avg7, 2))


def by_sku(conn, start: str, end: str) -> pd.DataFrame:
    df = db.read(conn, """
        select f.master_sku_id, p.name, p.category, sum(case when f.recognized then f.qty else 0 end) as units,
               sum(f.net_revenue) as net_revenue, sum(f.gross_margin) as gross_margin,
               avg(case when f.recognized then f.discount_pct end) as avg_discount_pct
        from fact_order_line f join dim_product p using (master_sku_id)
        where f.order_date between ? and ? group by f.master_sku_id, p.name, p.category""", (start, end))
    df["gross_margin_pct"] = (100 * df["gross_margin"] / df["net_revenue"]).where(df["net_revenue"] > 0)
    return df.sort_values("net_revenue", ascending=False).reset_index(drop=True)


def by_category(conn, start: str, end: str) -> pd.DataFrame:
    df = db.read(conn, """
        select p.category, sum(case when f.recognized then f.qty else 0 end) as units,
               sum(f.net_revenue) as net_revenue, sum(f.gross_margin) as gross_margin
        from fact_order_line f join dim_product p using (master_sku_id)
        where f.order_date between ? and ? group by p.category""", (start, end))
    df["gross_margin_pct"] = (100 * df["gross_margin"] / df["net_revenue"]).where(df["net_revenue"] > 0)
    return df.sort_values("net_revenue", ascending=False).reset_index(drop=True)


def discount_leakage(conn, start: str, end: str, target_pct: float, min_discount_pct: float = 25.0,
                     min_lines: int = 3) -> pd.DataFrame:
    """SKUs whose deeply discounted sales earned less gross margin than the owner's target (before refunds, so
    that returns do not masquerade as discount damage)."""
    df = db.read(conn, """
        select f.master_sku_id, p.name, count(*) as lines, sum(f.qty) as units, avg(f.discount_pct) as avg_discount_pct,
               sum(f.taxable_value) as net_revenue, sum(f.taxable_value - f.cogs) as gross_margin,
               min(f.order_date) as first_date, max(f.order_date) as last_date
        from fact_order_line f join dim_product p using (master_sku_id)
        where f.recognized = 1 and f.discount_pct >= ? and f.order_date between ? and ?
        group by f.master_sku_id, p.name""", (min_discount_pct, start, end))
    df = df[df["lines"] >= min_lines].copy()
    df["gross_margin_pct"] = 100 * df["gross_margin"] / df["net_revenue"]
    df["shortfall_vs_target_inr"] = (target_pct / 100 * df["net_revenue"] - df["gross_margin"]).clip(lower=0)
    return df[df["gross_margin_pct"] < target_pct].sort_values("shortfall_vs_target_inr", ascending=False).reset_index(drop=True)


def returns_by_category(conn, start: str, end: str) -> pd.DataFrame:
    df = db.read(conn, """
        select p.category, f.return_reason, count(*) as returned_lines
        from fact_order_line f join dim_product p using (master_sku_id)
        where f.refund_ex_tax > 0 and f.order_date between ? and ? group by p.category, f.return_reason""", (start, end))
    tot = db.read(conn, """
        select p.category, count(*) as shipped_lines from fact_order_line f join dim_product p using (master_sku_id)
        where f.recognized = 1 and f.order_date between ? and ? group by p.category""", (start, end))
    out = df.merge(tot, on="category")
    out["return_rate_pct"] = 100 * out.groupby("category")["returned_lines"].transform("sum") / out["shipped_lines"]
    out["reason_share_pct"] = 100 * out["returned_lines"] / out.groupby("category")["returned_lines"].transform("sum")
    return out.sort_values(["category", "returned_lines"], ascending=[True, False]).reset_index(drop=True)
