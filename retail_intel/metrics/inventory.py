"""Stock position, stock-outs, expiry exposure, slow movers, markdown scenarios (beauty: shelf life matters)."""
from __future__ import annotations

import pandas as pd

from .. import db
from .common import as_of as _as_of, context, shift


def run_rate(conn, as_of: str, days: int = 28) -> pd.Series:
    """Average units demanded per day over the last `days` (paid lines, cancellations excluded)."""
    df = db.read(conn, """
        select master_sku_id, sum(qty) as units from fact_order_line
        where status = 'paid' and order_date between ? and ? group by master_sku_id""", (shift(as_of, -days + 1), as_of))
    return (df.set_index("master_sku_id")["units"] / days).rename("run_rate")


def position(conn, as_of: str | None = None) -> pd.DataFrame:
    as_of = as_of or _as_of(conn)
    c = context(conn)
    inv = db.read(conn, """
        select master_sku_id, sum(qty) as on_hand, sum(qty * coalesce(unit_cost, 0)) as stock_value_at_cost
        from fact_inventory_daily where date = ? group by master_sku_id""", (as_of,))
    p = db.read(conn, "select master_sku_id, name, category, avg_unit_cost, mrp from dim_product")
    rr = run_rate(conn, as_of, int(c["lookback_days"]))
    df = p.merge(inv, on="master_sku_id", how="left").merge(rr, on="master_sku_id", how="left")
    df[["on_hand", "stock_value_at_cost", "run_rate"]] = df[["on_hand", "stock_value_at_cost", "run_rate"]].fillna(0)
    df["days_of_supply"] = (df["on_hand"] / df["run_rate"]).where(df["run_rate"] > 0)
    return df


def stockout_events(conn, as_of: str | None = None, min_days: int = 2, min_lost_units: float = 1.0) -> pd.DataFrame:
    """Runs of days with zero stock. Lost sales are estimated from the SKU's in-stock run-rate before the event."""
    as_of = as_of or _as_of(conn)
    tot = db.read(conn, "select master_sku_id, date, sum(qty) as q from fact_inventory_daily where date <= ? "
                        "group by master_sku_id, date", (as_of,))
    sold = db.read(conn, """
        select master_sku_id, order_date as date, sum(qty) as units, sum(taxable_value) as value
        from fact_order_line where status = 'paid' and order_date <= ? group by master_sku_id, order_date""", (as_of,))
    web = db.read(conn, "select master_sku_id, date, views from fact_web_daily where date <= ?", (as_of,))
    names = db.read(conn, "select master_sku_id, name from dim_product").set_index("master_sku_id")["name"].to_dict()
    sold_i = sold.set_index(["master_sku_id", "date"])
    web_i = web.set_index(["master_sku_id", "date"])["views"]
    out = []
    for sku, g in tot.sort_values("date").groupby("master_sku_id"):
        dates, q = g["date"].tolist(), g["q"].tolist()
        run_start = None
        for k, (d, qty) in enumerate(zip(dates, q)):
            if qty == 0 and run_start is None:
                run_start = k
            last = k == len(dates) - 1
            if run_start is not None and (qty != 0 or last):
                run_end = k if (qty == 0 and last) else k - 1
                n_days = run_end - run_start + 1
                if n_days >= min_days:
                    s, e = dates[run_start], dates[run_end]
                    instock = [x for x, qq in zip(dates, q) if shift(s, -28) <= x < s and qq > 0]
                    u = v = 0.0
                    for x in instock:
                        if (sku, x) in sold_i.index:
                            u += sold_i.loc[(sku, x), "units"]; v += sold_i.loc[(sku, x), "value"]
                    rate = u / len(instock) if instock else 0.0
                    price = v / u if u else 0.0
                    before = [web_i.get((sku, x), 0) for x in instock]
                    during = [web_i.get((sku, x), 0) for x in dates[run_start:run_end + 1]]
                    out.append(dict(master_sku_id=sku, name=names.get(sku, ""), start=s, end=e, days=n_days,
                                    ongoing=e == as_of, pre_event_units_per_day=round(rate, 2),
                                    est_lost_units=round(rate * n_days, 1), est_lost_revenue=round(rate * n_days * price, 2),
                                    web_views_per_day_before=round(sum(before) / len(before), 1) if before else None,
                                    web_views_per_day_during=round(sum(during) / len(during), 1) if during else None))
                run_start = None
    df = pd.DataFrame(out, columns=["master_sku_id", "name", "start", "end", "days", "ongoing", "pre_event_units_per_day",
                                    "est_lost_units", "est_lost_revenue", "web_views_per_day_before",
                                    "web_views_per_day_during"])
    return df[df["est_lost_units"] >= min_lost_units].sort_values("est_lost_revenue", ascending=False).reset_index(drop=True)


def expiry_exposure(conn, as_of: str | None = None, horizon_days: int | None = None) -> pd.DataFrame:
    """Batches expiring inside the horizon and how many units will NOT sell before expiry at the current run-rate."""
    as_of = as_of or _as_of(conn)
    c = context(conn)
    horizon = int(horizon_days or c["expiry_horizon_days"])
    inv = db.read(conn, """
        select master_sku_id, batch_no, expiry_date, qty, coalesce(unit_cost, 0) as unit_cost
        from fact_inventory_daily where date = ? and qty > 0 and expiry_date <> ''""", (as_of,))
    if inv.empty:
        return inv
    inv["days_left"] = (pd.to_datetime(inv["expiry_date"]) - pd.Timestamp(as_of)).dt.days
    rr = run_rate(conn, as_of, int(c["lookback_days"]))
    names = db.read(conn, "select master_sku_id, name, mrp from dim_product").set_index("master_sku_id")
    rows = []
    for sku, g in inv.sort_values("expiry_date").groupby("master_sku_id"):
        rate = float(rr.get(sku, 0.0))
        prior = 0
        for _, b in g.iterrows():
            capacity = rate * max(b["days_left"], 0)
            sell = min(max(capacity - prior, 0), b["qty"])
            prior += b["qty"]
            if b["days_left"] <= horizon:
                at_risk = b["qty"] - sell
                rows.append(dict(master_sku_id=sku, name=names.loc[sku, "name"], batch_no=b["batch_no"],
                                 expiry_date=b["expiry_date"], days_left=int(b["days_left"]), qty=int(b["qty"]),
                                 run_rate=round(rate, 2), expected_sell_before_expiry=round(sell, 1),
                                 units_at_risk=round(at_risk, 1), unit_cost=b["unit_cost"],
                                 value_at_risk_at_cost=round(at_risk * b["unit_cost"], 2),
                                 mrp=float(names.loc[sku, "mrp"])))
    return pd.DataFrame(rows).sort_values("value_at_risk_at_cost", ascending=False).reset_index(drop=True) if rows else pd.DataFrame()


def slow_movers(conn, as_of: str | None = None, threshold_days: float | None = None) -> pd.DataFrame:
    c = context(conn)
    th = threshold_days or c["slow_mover_days_of_supply"]
    p = position(conn, as_of)
    slow = p[(p["on_hand"] > 0) & ((p["days_of_supply"] > th) | (p["run_rate"] == 0))].copy()
    return slow.sort_values("stock_value_at_cost", ascending=False).reset_index(drop=True)


def low_stock(conn, as_of: str | None = None, min_run_rate: float = 0.3) -> pd.DataFrame:
    c = context(conn)
    p = position(conn, as_of)
    low = p[(p["on_hand"] > 0) & (p["run_rate"] >= min_run_rate) & (p["days_of_supply"] < c["low_stock_days_of_supply"])]
    return low.sort_values("days_of_supply").reset_index(drop=True)


def markdown_scenarios(conn, master_sku_id: str, batch_no: str | None = None, as_of: str | None = None,
                       discounts: tuple = (0, 10, 20, 30, 40)) -> pd.DataFrame:
    """What happens to a batch at risk of expiring if we discount it?

    ASSUMPTION (not measured from your data): demand rises by `markdown_elasticity` % per 1% discount.
    Calibrate it from real promotions before trusting the rupee figures.
    """
    as_of = as_of or _as_of(conn)
    c = context(conn)
    ex = expiry_exposure(conn, as_of, horizon_days=3650)
    ex = ex[ex["master_sku_id"] == master_sku_id]
    if batch_no:
        ex = ex[ex["batch_no"] == batch_no]
    if ex.empty:
        return pd.DataFrame()
    b = ex.sort_values("days_left").iloc[0]
    rows = []
    for d in discounts:
        rate = b["run_rate"] * (1 + c["markdown_elasticity"] * d / 100)
        sold = min(b["qty"], rate * b["days_left"])
        revenue = sold * b["mrp"] * (1 - d / 100)
        cogs_sold = sold * b["unit_cost"]
        unsold = b["qty"] - sold
        rows.append(dict(discount_pct=d, units_sold_before_expiry=round(sold, 1), unsold_units=round(unsold, 1),
                         revenue_ex_gst=round(revenue, 2), gross_margin_on_sold=round(revenue - cogs_sold, 2),
                         write_off_at_cost=round(unsold * b["unit_cost"], 2),
                         loss_vs_batch_cost=round(revenue - b["qty"] * b["unit_cost"], 2)))
    out = pd.DataFrame(rows)
    out.attrs["batch"] = dict(master_sku_id=master_sku_id, batch_no=b["batch_no"], days_left=int(b["days_left"]), qty=int(b["qty"]))
    return out
