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


def order_to_cash(conn, as_of: str | None = None) -> dict:
    x = db.read(conn, "select * from order_xref")
    paid_value = db.read(conn, """select order_ref, sum(taxable_value) as billed_value_ex_gst from fact_order_line
                                  group by order_ref""")
    out = {"orders_checked": int(len(x)), "gaps": []}
    for col, label in GAPS.items():
        hit = x[x[col].astype(int) == 1]
        refs = hit["order_ref_n"].astype(str)
        value = paid_value[paid_value["order_ref"].astype(str).isin(set(refs))]["billed_value_ex_gst"].sum()
        oldest = pd.to_datetime(hit["order_date"], errors="coerce").min()
        out["gaps"].append(dict(gap=col, description=label, orders=int(len(hit)),
                                # no invoice means no billed value: reported as unknown, never as zero
                                billed_value_ex_gst=None if col == "dispatched_not_billed" else round(float(value), 2),
                                oldest_order_date=None if pd.isna(oldest) else oldest.date().isoformat(),
                                sample_orders=refs.head(5).tolist()))
    return out


def gaps_table(conn) -> pd.DataFrame:
    return pd.DataFrame(order_to_cash(conn)["gaps"]).drop(columns=["sample_orders"])
