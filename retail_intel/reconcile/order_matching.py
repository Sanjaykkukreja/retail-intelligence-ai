"""Layer 2b - Match every order across the three systems.

One row per order reference with: is it in the website / billing / warehouse, and what is wrong with it.
Billing is the source of truth for revenue; the website is only used for behaviour.
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from .. import db
from .sku_mapping import norm_ref

from ..metrics.common import context


def billing_dedup(conn) -> pd.DataFrame:
    b = db.read(conn, "select * from raw_billing_invoices")
    b = b.drop_duplicates(["invoice_no", "line_no"], keep="first").copy()
    b["order_ref_n"] = norm_ref(b["order_ref"])
    b["order_date"] = b["invoice_ts"].str[:10]
    return b


def build_order_xref(conn, as_of: str | None = None) -> pd.DataFrame:
    b = billing_dedup(conn)
    bo = b.groupby("order_ref_n").agg(order_date=("order_date", "first"), billing_status=("status", "first")).reset_index()
    w = db.read(conn, "select order_ref, status from raw_wms_dispatch")
    w["order_ref_n"] = norm_ref(w["order_ref"])
    rank = {"RTO": 3, "PENDING": 2, "DISPATCHED": 1}
    w["r"] = w["status"].map(rank)
    wo = w.sort_values("r", ascending=False).drop_duplicates("order_ref_n")[["order_ref_n", "status"]]
    wo = wo.rename(columns={"status": "wms_status"})
    wp = db.read(conn, "select distinct transaction_id from raw_web_purchases where transaction_id <> ''")
    wp["order_ref_n"] = norm_ref(wp["transaction_id"])
    x = pd.DataFrame({"order_ref_n": sorted(set(bo["order_ref_n"]) | set(wo["order_ref_n"]) | set(wp["order_ref_n"]))})
    x = x.merge(bo, on="order_ref_n", how="left").merge(wo, on="order_ref_n", how="left")
    x["in_billing"] = x["billing_status"].notna()
    x["in_wms"] = x["wms_status"].notna()
    x["in_web"] = x["order_ref_n"].isin(set(wp["order_ref_n"]))
    if as_of is None:
        as_of = b["order_date"].max()
    stuck_after = int(context(conn)["stuck_after_days"])      # owner-signed rule, see the definitions register
    cutoff = (date.fromisoformat(as_of) - timedelta(days=stuck_after)).isoformat()
    x["paid_not_dispatched"] = (x["billing_status"] == "paid") & (~x["in_wms"] | (x["wms_status"] == "PENDING")) \
        & (x["order_date"] <= cutoff)
    x["dispatched_not_billed"] = x["in_wms"] & ~x["in_billing"]
    x["web_purchase_not_billed"] = x["in_web"] & ~x["in_billing"]
    db.write(conn, x, "order_xref")
    conn.commit()
    return x
