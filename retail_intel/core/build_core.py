"""Layer 3 - Core model. Clean, typed, joined tables that every metric reads from.

Definitions (written once, used everywhere):
  recognized line   = invoice status 'paid' AND warehouse status is not 'RTO' (returned to origin)
  net revenue       = taxable value (after discount, before GST) of recognized lines, minus refunds (ex-GST)
  COGS              = units x landed unit cost of the batch actually shipped
  gross margin      = net revenue - COGS (before shipping, marketing, overheads)
  cancelled / RTO lines never count as revenue; manual (unbilled) shipments are excluded
"""
from __future__ import annotations

import pandas as pd

from .. import db
from ..config import DEFAULT_CONTEXT
from ..reconcile.order_matching import billing_dedup
from ..reconcile.sku_mapping import norm_ref


def _map(conn, system: str) -> dict:
    m = db.read(conn, "select source_code, master_sku_id from sku_map where system=? and status='accepted'", (system,))
    return dict(zip(m["source_code"], m["master_sku_id"]))


def build_core(conn) -> dict[str, int]:
    bill_map, wms_map, web_map = _map(conn, "billing"), _map(conn, "wms"), _map(conn, "website")

    # ---- business context (owner-editable rules) ---------------------------------
    ctx = db.read(conn, "select * from business_context") if db.table_exists(conn, "business_context") else None
    if ctx is None:
        db.write(conn, pd.DataFrame([dict(key=k, value=str(v)) for k, v in DEFAULT_CONTEXT.items()]), "business_context")

    # ---- receipts -> cost per batch ------------------------------------------------
    rc = db.read(conn, "select wms_sku, batch_no, expiry_date, qty, unit_cost, receipt_date from raw_wms_receipts")
    rc["master_sku_id"] = rc["wms_sku"].map(wms_map)
    rc["unit_cost"] = rc["unit_cost"].astype(float)
    rc["qty"] = rc["qty"].astype(int)
    cost_by_batch = rc.groupby(["wms_sku", "batch_no"])["unit_cost"].mean().to_dict()

    # ---- warehouse dispatches, aggregated per order + SKU ---------------------------
    w = db.read(conn, "select order_ref, wms_sku, batch_no, qty, order_received_at, dispatched_at, status "
                      "from raw_wms_dispatch")
    w["order_ref_n"] = norm_ref(w["order_ref"])
    w["master_sku_id"] = w["wms_sku"].map(wms_map)
    w["qty"] = w["qty"].astype(int)
    w["unit_cost"] = [cost_by_batch.get((s, b)) for s, b in zip(w["wms_sku"], w["batch_no"])]
    w["cogs"] = w["qty"] * w["unit_cost"]
    rank = {"RTO": 3, "PENDING": 2, "DISPATCHED": 1}
    w["r"] = w["status"].map(rank)
    wl = (w.sort_values("r", ascending=False)
          .groupby(["order_ref_n", "master_sku_id"], dropna=False)
          .agg(wms_status=("status", "first"), wms_qty=("qty", "sum"), cogs=("cogs", "sum"),
               received_at=("order_received_at", "min"), dispatched_at=("dispatched_at", "max"))
          .reset_index())

    # ---- billing lines ---------------------------------------------------------------
    b = billing_dedup(conn)
    b["master_sku_id"] = b["sku_code"].map(bill_map)
    for c in ("qty", "mrp", "discount_amt", "taxable_value", "tax_amt", "total_amt", "tax_rate"):
        b[c] = b[c].astype(float)
    b["gross_value"] = b["qty"] * b["mrp"]
    ol = b.merge(wl, on=["order_ref_n", "master_sku_id"], how="left")

    # ---- refunds (ex-GST) ----------------------------------------------------------------
    rf = db.read(conn, "select order_ref, sku_code, refund_amt, reason, refund_date from raw_billing_refunds")
    rf["order_ref_n"] = norm_ref(rf["order_ref"])
    rf["master_sku_id"] = rf["sku_code"].map(bill_map)
    rf["refund_amt"] = rf["refund_amt"].astype(float)
    rf_agg = rf.groupby(["order_ref_n", "master_sku_id"]).agg(
        refund_incl_tax=("refund_amt", "sum"), return_reason=("reason", "first")).reset_index()
    ol = ol.merge(rf_agg, on=["order_ref_n", "master_sku_id"], how="left")
    ol["refund_incl_tax"] = ol["refund_incl_tax"].fillna(0.0)
    ol["refund_ex_tax"] = ol["refund_incl_tax"] / (1 + ol["tax_rate"] / 100)

    ol["is_cancelled"] = ol["status"] == "cancelled"
    ol["is_rto"] = ol["wms_status"] == "RTO"
    ol["recognized"] = (ol["status"] == "paid") & ~ol["is_rto"]
    ol["net_revenue"] = (ol["taxable_value"] - ol["refund_ex_tax"]).where(ol["recognized"], 0.0)
    # cost of units shipped; if the warehouse row is missing fall back to the SKU's average landed cost
    avg_cost = rc.groupby("master_sku_id")["unit_cost"].mean()
    fallback = ol["master_sku_id"].map(avg_cost) * ol["qty"]
    ol["cogs"] = ol["cogs"].where(ol["cogs"].notna(), fallback).where(ol["recognized"], 0.0)
    ol["gross_margin"] = ol["net_revenue"] - ol["cogs"]
    ol["discount_pct"] = (100 * ol["discount_amt"] / ol["gross_value"]).round(1)
    ts_r = pd.to_datetime(ol["received_at"], errors="coerce")
    ts_d = pd.to_datetime(ol["dispatched_at"].replace("", pd.NA), errors="coerce")
    ol["dispatch_hours"] = ((ts_d - ts_r).dt.total_seconds() / 3600).round(2)
    cols = ["order_ref_n", "invoice_no", "line_no", "order_date", "customer_id", "master_sku_id", "qty", "mrp",
            "gross_value", "discount_amt", "discount_pct", "taxable_value", "tax_amt", "status", "wms_status",
            "is_cancelled", "is_rto", "recognized", "refund_ex_tax", "return_reason", "net_revenue", "cogs",
            "gross_margin", "received_at", "dispatched_at", "dispatch_hours", "payment_mode"]
    ol = ol[cols].rename(columns={"order_ref_n": "order_ref"})
    db.write(conn, ol, "fact_order_line")

    # ---- inventory by day / batch ----------------------------------------------------------
    s = db.read(conn, "select snapshot_date, wms_sku, batch_no, expiry_date, qty_on_hand from raw_wms_snapshots")
    s["qty"] = s["qty_on_hand"].astype(int)
    s = s[s["qty"] >= 0].copy()           # negative rows are flagged by the quality gate and dropped
    s["master_sku_id"] = s["wms_sku"].map(wms_map)
    s["unit_cost"] = [cost_by_batch.get((a, c)) for a, c in zip(s["wms_sku"], s["batch_no"])]
    s = s.rename(columns={"snapshot_date": "date"})[["date", "master_sku_id", "batch_no", "expiry_date", "qty", "unit_cost"]]
    db.write(conn, s, "fact_inventory_daily")

    # ---- website funnel ----------------------------------------------------------------------
    wd = db.read(conn, "select date, item_id, item_views, add_to_cart, begin_checkout, purchases from raw_web_daily_items")
    wd["master_sku_id"] = wd["item_id"].map(web_map)
    for c in ("item_views", "add_to_cart", "begin_checkout", "purchases"):
        wd[c] = wd[c].astype(int)
    wd = wd.rename(columns={"item_views": "views", "add_to_cart": "atc", "begin_checkout": "checkouts"})
    db.write(conn, wd[["date", "master_sku_id", "item_id", "views", "atc", "checkouts", "purchases"]], "fact_web_daily")
    ss = db.read(conn, "select date, sessions from raw_web_sessions")
    ss["sessions"] = ss["sessions"].astype(int)
    db.write(conn, ss, "fact_web_sessions")

    # ---- product dimension -----------------------------------------------------------------------
    dim = db.read(conn, "select * from dim_product_base")
    dim = dim.merge(rc.groupby("master_sku_id")["unit_cost"].mean().round(2).rename("avg_unit_cost").reset_index(),
                    on="master_sku_id", how="left")
    mrp = ol.groupby("master_sku_id")["mrp"].median().rename("mrp").reset_index()
    dim = dim.merge(mrp, on="master_sku_id", how="left")
    db.write(conn, dim, "dim_product")
    conn.commit()
    return {"fact_order_line": len(ol), "fact_inventory_daily": len(s), "fact_web_daily": len(wd), "dim_product": len(dim)}


def check_core_reconciles(conn, tolerance_pct: float = 0.01) -> pd.DataFrame:
    """Rebuild net revenue straight from the raw billing + refund tables and compare with the core table."""
    b = billing_dedup(conn)
    b["tv"] = b["taxable_value"].astype(float)
    wr = db.read(conn, "select order_ref, status from raw_wms_dispatch")
    wr["ref"] = norm_ref(wr["order_ref"])
    rto = set(wr.loc[wr["status"] == "RTO", "ref"])
    rec = b[(b["status"] == "paid") & ~b["order_ref_n"].isin(rto)]
    rf = db.read(conn, "select order_ref, refund_amt from raw_billing_refunds")
    rf["ref"] = norm_ref(rf["order_ref"])
    rf_rec = rf[~rf["ref"].isin(rto)]
    raw_rev = rec["tv"].sum() - rf_rec["refund_amt"].astype(float).sum() / 1.18
    core_rev = db.read(conn, "select sum(net_revenue) v from fact_order_line")["v"][0]
    diff_pct = 100 * abs(raw_rev - core_rev) / max(abs(raw_rev), 1)
    out = pd.DataFrame([dict(check_name="core_vs_raw_net_revenue", raw=round(raw_rev, 2), core=round(core_rev, 2),
                             diff_pct=round(diff_pct, 5), ok=diff_pct <= tolerance_pct)])
    return out
