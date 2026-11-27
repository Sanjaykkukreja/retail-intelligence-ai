"""Layer 2c - Data quality gate. Runs before any number is built.

Severity:  info  = worth knowing         warn = shown in the briefing, pipeline continues
           block = pipeline stops; fix the data first (bad numbers are worse than no numbers)
"""
from __future__ import annotations

import pandas as pd

from .. import db
from .order_matching import billing_dedup
from .sku_mapping import norm_ref

MAX_SOURCE_LAG_DAYS = 2
MAX_UNMAPPED_REVENUE_PCT = 1.0
MAX_UNMAPPED_WEB_VIEWS_PCT = 25.0


def run_checks(conn) -> pd.DataFrame:
    rows: list[dict] = []

    def add(name, severity, count, detail=""):
        rows.append(dict(check_name=name, severity=severity, issue_count=int(round(count)), detail=detail))

    raw_b = db.read(conn, "select invoice_no, line_no, order_ref, qty, mrp, discount_amt, taxable_value, status, "
                          "invoice_ts, barcode from raw_billing_invoices")
    b = billing_dedup(conn)
    add("billing_duplicate_rows", "warn" if len(raw_b) > len(b) else "info", len(raw_b) - len(b),
        "identical invoice lines repeated; extra copies are dropped")
    first = raw_b.drop_duplicates("invoice_no")
    add("billing_messy_order_refs", "info", (first["order_ref"] != first["order_ref"].str.strip().str.upper()).sum(),
        "lower-case or trailing spaces; normalised before matching")
    w = db.read(conn, "select distinct order_ref from raw_wms_dispatch")
    add("wms_messy_order_refs", "info", (w["order_ref"] != w["order_ref"].str.strip().str.upper()).sum(),
        "lower-case or trailing spaces; normalised before matching")

    snaps = db.read(conn, "select snapshot_date, wms_sku, qty_on_hand from raw_wms_snapshots")
    neg = (snaps["qty_on_hand"].astype(int) < 0).sum()
    add("wms_negative_stock_rows", "warn" if neg else "info", neg, "negative stock rows are ignored, not netted")
    n_days = snaps["snapshot_date"].nunique()
    n_skus = snaps["wms_sku"].nunique()
    cover = snaps[snaps["qty_on_hand"].astype(int) >= 0].drop_duplicates(["snapshot_date", "wms_sku"]).shape[0]
    expected = n_days * n_skus
    gap = expected - cover
    add("wms_snapshot_gaps", "warn" if gap else "info", gap, f"{n_skus} SKUs x {n_days} days expected")

    # arithmetic sanity on billing lines
    q, mrp = b["qty"].astype(float), b["mrp"].astype(float)
    disc, taxable = b["discount_amt"].astype(float), b["taxable_value"].astype(float)
    bad_math = ((q * mrp - disc - taxable).abs() > 0.02).sum()
    add("billing_line_arithmetic", "block" if bad_math else "info", bad_math, "qty x MRP - discount must equal taxable value")
    add("billing_negative_values", "block" if ((q <= 0) | (taxable < 0)).sum() else "info",
        ((q <= 0) | (taxable < 0)).sum(), "zero/negative quantity or value")

    # SKU mapping
    m = db.read(conn, "select * from sku_map")
    acc = set(m.loc[m["status"] == "accepted", "source_code"])
    un_b = b[~b["sku_code"].isin(acc)]
    share = 100 * un_b["taxable_value"].astype(float).sum() / max(taxable.sum(), 1)
    add("billing_sku_unmapped_revenue_pct", "block" if share > MAX_UNMAPPED_REVENUE_PCT else "info",
        max(share, 1) if share > MAX_UNMAPPED_REVENUE_PCT else share,
        "% of billed value on SKUs with no accepted master SKU")
    wv = db.read(conn, "select item_id, item_views from raw_web_daily_items")
    wv["v"] = wv["item_views"].astype(float)
    wshare = 100 * wv.loc[~wv["item_id"].isin(acc), "v"].sum() / max(wv["v"].sum(), 1)
    add("web_items_unmapped_views_pct", "block" if wshare > MAX_UNMAPPED_WEB_VIEWS_PCT else ("warn" if wshare else "info"),
        round(wshare, 2), "% of website item views on items with no accepted master SKU")
    add("sku_mapping_review_queue", "warn" if (m["status"] != "accepted").any() else "info",
        (m["status"] != "accepted").sum(), "mappings waiting for a human decision (data/sku_map_overrides.csv)")

    # orders across systems
    x = db.read(conn, "select * from order_xref")
    add("orders_paid_not_dispatched", "warn" if x["paid_not_dispatched"].sum() else "info", x["paid_not_dispatched"].sum(),
        "paid orders with no dispatch after 4 days")
    add("orders_dispatched_not_billed", "warn" if x["dispatched_not_billed"].sum() else "info",
        x["dispatched_not_billed"].sum(), "shipped from the warehouse but no invoice")
    add("web_purchase_not_billed", "warn" if x["web_purchase_not_billed"].sum() else "info",
        x["web_purchase_not_billed"].sum(), "website purchase with no matching invoice")
    paid = x[x["billing_status"] == "paid"]
    gap_pct = 100 * (paid["in_web"] == 0).mean() if len(paid) else 0
    add("billing_orders_missing_in_web_pct", "info", round(gap_pct, 1),
        "% of paid orders the website never tracked (ad blockers, consent, missing transaction id)")

    # freshness
    ends = dict(
        billing=b["order_date"].max(),
        wms=snaps["snapshot_date"].max(),
        web=db.read(conn, "select max(date) d from raw_web_daily_items")["d"][0],
    )
    ds = pd.to_datetime(pd.Series(ends))
    lag = (ds.max() - ds.min()).days
    add("source_freshness_lag_days", "block" if lag > MAX_SOURCE_LAG_DAYS else "info", lag,
        "latest dates: " + ", ".join(f"{k}={v}" for k, v in ends.items()))

    df = pd.DataFrame(rows)
    df.insert(0, "run_at", pd.Timestamp.now().isoformat(timespec="seconds"))
    db.write(conn, df, "dq_issue")
    conn.commit()
    return df


def blockers(df: pd.DataFrame) -> pd.DataFrame:
    return df[(df["severity"] == "block") & (df["issue_count"] > 0)]
