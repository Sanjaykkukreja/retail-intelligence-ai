"""Order-to-cash checks: every paid order should ship, every shipment should be billed, every web sale should reach billing.

Built on order_xref (the three-system order cross-reference). Values use the billed amount where an invoice exists.
"""
from __future__ import annotations

import pandas as pd

from .. import db

GAPS = {
    "paid_not_dispatched": "Paid in billing but not dispatched by the warehouse",
    "dispatched_not_billed": "Dispatched by the warehouse but no invoice in billing",
    "web_purchase_not_billed": "Purchased on the website but no invoice in billing",
}


def _where(r) -> str:
    if not r["in_wms"]:
        return "never reached the warehouse system"
    if not r["in_billing"]:
        return f"in warehouse ({r['wms_status']}), no invoice" + ("" if r["in_web"] else ", not a website order")
    return f"in warehouse, status {r['wms_status']}"


def _profile(conn, hit: pd.DataFrame, as_of: str) -> dict:
    """Where the stuck orders sit, how old they are, how they spread over time, and what they have in common.
    This is what turns "73 orders are stuck" into a cause the team can act on."""
    hit = hit.copy()
    refs = set(hit["order_ref_n"].astype(str))
    if hit["order_date"].isna().all() or (hit["order_date"].astype(str) == "nan").all():   # not billed: use warehouse date
        wd = db.read(conn, "select order_ref, min(order_received_at) as received from raw_wms_dispatch group by order_ref")
        wd["ref"] = wd["order_ref"].astype(str).str.strip().str.upper()
        hit = hit.merge(wd[["ref", "received"]], left_on="order_ref_n", right_on="ref", how="left")
        hit["order_date"] = hit["received"].astype(str).str[:10]
    d = pd.to_datetime(hit["order_date"], errors="coerce")
    age = (pd.Timestamp(as_of) - d).dt.days
    bins = pd.cut(age, [-1, 7, 30, 90, 10_000], labels=["up to 7 days", "8-30 days", "31-90 days", "over 90 days"])
    months = d.dt.strftime("%Y-%m").value_counts().sort_index()
    recent = int((age <= 30).sum())
    out = dict(
        where_stuck=hit.apply(_where, axis=1).value_counts().to_dict(),
        order_number_prefix=hit["order_ref_n"].astype(str).str.extract(r"^([A-Z]+)")[0].value_counts().to_dict(),
        age=bins.value_counts().reindex(bins.cat.categories, fill_value=0).astype(int).to_dict(),
        months_with_cases=int(len(months)), max_in_one_month=int(months.max()) if len(months) else 0,
        last_30_days=recent,
        pattern=("spread over many months (a standing process gap, not a one-off event)" if len(months) >= 6
                 and months.max() <= max(3, 0.25 * len(hit)) else "concentrated in a few months (look for an event)"),
    )
    lines = db.read(conn, "select order_ref, order_date, master_sku_id, payment_mode from fact_order_line")
    mine = lines[lines["order_ref"].astype(str).isin(refs)]
    if len(mine):
        orders = mine.drop_duplicates("order_ref")
        out["payment_mode"] = orders["payment_mode"].value_counts().to_dict()
        out["distinct_products"] = int(mine["master_sku_id"].nunique())
        stock = db.read(conn, "select date, master_sku_id, sum(qty) as qty from fact_inventory_daily group by 1, 2")
        j = mine.merge(stock, left_on=["order_date", "master_sku_id"], right_on=["date", "master_sku_id"], how="left")
        out["orders_with_a_product_out_of_stock_that_day"] = int(j.loc[j["qty"].fillna(0) <= 0, "order_ref"].nunique())
    return out


def order_to_cash(conn, as_of: str | None = None) -> dict:
    x = db.read(conn, "select * from order_xref")
    paid_value = db.read(conn, """select order_ref, sum(taxable_value) as billed_value_ex_gst from fact_order_line
                                  group by order_ref""")
    from .common import as_of as _as_of
    as_of = as_of or _as_of(conn)
    out = {"orders_checked": int(len(x)), "as_of": as_of, "gaps": []}
    for col, label in GAPS.items():
        hit = x[x[col].astype(int) == 1]
        refs = hit["order_ref_n"].astype(str)
        value = paid_value[paid_value["order_ref"].astype(str).isin(set(refs))]["billed_value_ex_gst"].sum()
        oldest = pd.to_datetime(hit["order_date"], errors="coerce").min()
        out["gaps"].append(dict(gap=col, description=label, orders=int(len(hit)),
                                # no invoice means no billed value: reported as unknown, never as zero
                                billed_value_ex_gst=None if col == "dispatched_not_billed" else round(float(value), 2),
                                oldest_order_date=None if pd.isna(oldest) else oldest.date().isoformat(),
                                sample_orders=refs.head(5).tolist(),
                                profile=_profile(conn, hit, as_of) if len(hit) else None))
    return out


def gaps_table(conn) -> pd.DataFrame:
    return pd.DataFrame(order_to_cash(conn)["gaps"]).drop(columns=["sample_orders", "profile"])
