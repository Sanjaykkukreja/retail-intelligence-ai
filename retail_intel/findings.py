"""Findings: what happened, how big it is (in rupees), a question for the owner, and a suggested action.

Every number comes from the metric functions. The text here is a plain template; later the LLM agent can
rephrase and prioritise, but it never invents numbers. Impact figures are ESTIMATES and say how they were made.
"""
from __future__ import annotations

import json
from datetime import datetime

import numpy as np
import pandas as pd

from . import db
from .metrics import funnel_fulfilment as ff
from .metrics import inventory, sales
from .metrics.common import as_of as _as_of, context, shift

SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2, "info": 3}


def _py(o):
    if isinstance(o, (np.integer,)): return int(o)
    if isinstance(o, (np.floating,)): return None if np.isnan(o) else float(o)
    if isinstance(o, (np.bool_,)): return bool(o)
    if isinstance(o, (pd.Timestamp,)): return o.isoformat()
    return str(o)


def _clean(d):
    return json.loads(json.dumps(d, default=_py))


def _runs(days: list[str], min_len: int, max_gap: int = 1) -> list[tuple[str, str]]:
    """Group flagged dates into windows; a single unflagged day inside a window does not split it."""
    if not days:
        return []
    ds = sorted(pd.to_datetime(days))
    out, start, prev = [], ds[0], ds[0]
    for d in ds[1:]:
        if (d - prev).days > max_gap + 1:
            out.append((start, prev)); start = d
        prev = d
    out.append((start, prev))
    return [(a.date().isoformat(), b.date().isoformat()) for a, b in out if (b - a).days + 1 >= min_len]


def _severity(impact: float, monthly_revenue: float) -> str:
    if monthly_revenue <= 0:
        return "medium"
    r = impact / monthly_revenue
    return "high" if r >= 0.03 else "medium" if r >= 0.005 else "low"


def _f(type_, key, start, severity, title, impact, evidence, question, action, calc, end=None, ongoing=False, current=True):
    return dict(id=f"{type_}:{key}:{start or 'na'}", type=type_, severity=severity, title=title, start=start, end=end,
                ongoing=bool(ongoing), est_impact_inr=round(float(impact), 2) if impact is not None else None,
                evidence=_clean(evidence), question=question, action=action, calc=calc, is_current=bool(current))


# ---------------------------------------------------------------------------- detectors
def stockout_findings(conn, as_of, first, monthly_rev, min_days=2, min_lost_units=5.0, current_days=14):
    out = []
    se = inventory.stockout_events(conn, as_of, min_days, min_lost_units)
    for _, r in se.iterrows():
        kept = (r["web_views_per_day_during"] or 0) >= 0.6 * (r["web_views_per_day_before"] or 1)
        title = f"{r['name']} out of stock {r['days']} days ({r['start']} to {r['end']})" + (" - still out" if r["ongoing"] else "")
        demand = (f"Customers kept looking: product views {r['web_views_per_day_before']:.0f} -> "
                  f"{r['web_views_per_day_during']:.0f} a day, so demand was real." if kept else "")
        out.append(_f(
            "stockout", r["master_sku_id"], r["start"], _severity(r["est_lost_revenue"], monthly_rev), title,
            r["est_lost_revenue"], dict(r.to_dict(), demand_note=demand),
            f"Why was {r['name']} out of stock from {r['start']}? A delayed purchase order, a quality hold, or planned? "
            f"If a PO is delayed, what is the new arrival date?",
            ("Expedite the open purchase order, switch the product page to 'notify me' and pause ads on this SKU until stock lands."
             if r["ongoing"] else "Raise this SKU's reorder point or order earlier; the PO should be placed (lead time + 7 days) before cover runs out."),
            f"Estimated lost units = in-stock average of {r['pre_event_units_per_day']}/day in the 28 days before x {r['days']} days; "
            f"revenue = units x average net price (ex-GST).",
            end=r["end"], ongoing=r["ongoing"], current=r["ongoing"] or r["end"] >= shift(as_of, -current_days)))
    return out


def dispatch_delay_findings(conn, as_of, first, monthly_rev, current_days=14):
    d = ff.daily_fulfilment(conn, first, as_of)
    d = d[d["orders"] >= 5]
    if d.empty:
        return []
    base = d["avg_dispatch_hours"].median()
    flagged = d.loc[d["avg_dispatch_hours"] > 1.8 * base, "date"].tolist()
    aov = sales.summary(conn, first, as_of)["aov"]
    out = []
    for s, e in _runs(flagged, 3):
        w = d[(d["date"] >= s) & (d["date"] <= e)]
        b = d[~d["date"].between(s, e)]
        extra_cancel = max(0.0, w["cancel_rate_pct"].mean() - b["cancel_rate_pct"].median()) / 100 * w["orders"].sum()
        extra_rto = max(0.0, w["rto_rate_pct"].mean() - b["rto_rate_pct"].median()) / 100 * w["orders"].sum()
        impact = (extra_cancel + extra_rto) * aov
        out.append(_f(
            "dispatch_delay", "all", s, _severity(impact, monthly_rev),
            f"Dispatch took {w['avg_dispatch_hours'].mean():.0f}h on average ({s} to {e}) against a normal {base:.0f}h",
            impact, dict(window_avg_hours=round(w["avg_dispatch_hours"].mean(), 1), normal_hours=round(base, 1),
                         pct_over_sla=round(w["pct_over_sla"].mean(), 1), orders_in_window=int(w["orders"].sum()),
                         extra_cancellations=round(extra_cancel, 1), extra_rto=round(extra_rto, 1)),
            f"What happened in the warehouse between {s} and {e}: staff shortage, carrier pickup delay, a system outage?",
            "Add a daily alert when average dispatch time passes the SLA; agree a backup carrier pickup and a surge-staffing rule.",
            "Impact = extra cancellations and returned-to-origin orders above the normal rate x average order value (ex-GST).",
            end=e, current=e >= shift(as_of, -current_days)))
    return out


def conversion_drop_findings(conn, as_of, first, monthly_rev, current_days=14):
    f = ff.daily_funnel(conn, first, as_of)
    f = f[f["checkout_units"] >= 20]
    if f.empty:
        return []
    base = f["checkout_to_purchase"].median()
    flagged = f.loc[f["checkout_to_purchase"] < 0.75 * base, "date"].tolist()
    s_all = sales.summary(conn, first, as_of)
    price = s_all["net_revenue"] / s_all["units"] if s_all["units"] else 0
    out = []
    for s, e in _runs(flagged, 3):
        w = f[(f["date"] >= s) & (f["date"] <= e)]
        missed = ((base - w["checkout_to_purchase"]).clip(lower=0) * w["checkout_units"]).sum()
        impact = missed * price
        out.append(_f(
            "checkout_drop", "all", s, _severity(impact, monthly_rev),
            f"Checkout-to-purchase fell to {100 * w['checkout_to_purchase'].mean():.0f}% ({s} to {e}) against a normal {100 * base:.0f}%",
            impact, dict(window_rate_pct=round(100 * w["checkout_to_purchase"].mean(), 1), normal_rate_pct=round(100 * base, 1),
                         sessions_per_day=round(w["sessions"].mean()), views_per_day=round(w["item_views"].mean()),
                         missed_units=round(missed, 1)),
            f"Did anything change on the payment gateway, checkout page or site between {s} and {e}? Traffic stayed normal, so shoppers were reaching checkout and failing to pay.",
            "Check payment-gateway success rate and error logs for those dates; alert when checkout-to-purchase drops below 75% of normal for a day.",
            "Impact = missed purchase units (normal rate minus actual, x checkouts) x average net price per unit (ex-GST). Website numbers undercount ~7%.",
            end=e, current=e >= shift(as_of, -current_days)))
    return out


def leakage_findings(conn, as_of, start, end, target):
    lk = sales.discount_leakage(conn, start, end, target)
    if lk.empty:
        return []
    dim = db.read(conn, "select master_sku_id, avg_unit_cost, mrp from dim_product")
    lk = lk.merge(dim, on="master_sku_id", how="left")
    # highest discount that still earns the target gross margin: 1 - cost / (mrp x (1 - target))
    lk["max_discount_for_target_pct"] = (100 * (1 - lk["avg_unit_cost"] / (lk["mrp"] * (1 - target / 100)))).clip(lower=0).round(1)
    top = lk.head(10)[["name", "lines", "avg_discount_pct", "gross_margin_pct", "shortfall_vs_target_inr",
                       "max_discount_for_target_pct"]]
    impact = lk["shortfall_vs_target_inr"].sum()
    first_d, last_d = lk["first_date"].min(), lk["last_date"].max()
    return [_f(
        "discount_leakage", "all", first_d, "high" if impact >= 20000 else "medium",
        f"{len(lk)} SKUs sold below the {target:.0f}% margin target at 25%+ discount ({first_d} to {last_d})", impact,
        dict(skus=len(lk), total_shortfall_inr=round(impact, 2), worst=top.round(1).to_dict("records")),
        "Were these deep discounts intentional (clearance) or a promotion set-up error? Who approved a 35% discount on thin-margin SKUs?",
        "Set a discount floor per SKU = the largest discount that still earns the target margin (column max_discount_for_target_pct); "
        "exclude thin-margin SKUs from sitewide sales.",
        "Shortfall = target margin x revenue minus actual gross margin on lines discounted 25%+ (before refunds).",
        end=last_d, current=last_d >= shift(as_of, -30))]


def expiry_findings(conn, as_of):
    ex = inventory.expiry_exposure(conn, as_of)
    out = []
    if ex.empty:
        return out
    el = context(conn)["markdown_elasticity"]
    for _, r in ex[(ex["units_at_risk"] >= 5) & (ex["value_at_risk_at_cost"] >= 1000)].iterrows():
        sc = inventory.markdown_scenarios(conn, r["master_sku_id"], r["batch_no"], as_of)
        best = sc.sort_values("revenue_ex_gst", ascending=False).iloc[0] if len(sc) else None
        if best is not None:
            share_left = best["unsold_units"] / r["qty"]
            action = (f"Best of the discounts tested is {best['discount_pct']:.0f}% off: about {best['units_sold_before_expiry']:.0f} units sell and "
                      f"{best['revenue_ex_gst']:,.0f} INR is recovered (ex-GST), leaving {best['unsold_units']:.0f} units to write off. "
                      + ("Markdown alone will not clear this batch, so push the supplier-return, bundle or liquidation route first. "
                         if share_left > 0.5 else "")
                      + f"Assumes demand rises {el}% per 1% discount, which is not measured from your data.")
        else:
            action = "Ask the supplier about returns, bundle into a set, or liquidate."
        out.append(_f(
            "expiry_risk", f"{r['master_sku_id']}:{r['batch_no']}", as_of,
            "high" if r["value_at_risk_at_cost"] >= 25000 else "medium",
            f"{r['name']}: {r['units_at_risk']:.0f} of {r['qty']} units will not sell before {r['expiry_date']} ({r['days_left']} days)",
            r["value_at_risk_at_cost"], r.to_dict(),
            f"Can batch {r['batch_no']} be returned to the supplier, bundled into a gift set or sold to a liquidator? Is a markdown acceptable?",
            action,
            "Units at risk = batch quantity minus (28-day run-rate x days to expiry), after older batches of the same SKU sell first. Value at landed cost.",
            end=r["expiry_date"]))
    return out


def stock_position_findings(conn, as_of):
    out, c = [], context(conn)
    slow = inventory.slow_movers(conn, as_of)
    if len(slow):
        slow = slow.copy()
        slow["excess_units"] = (slow["on_hand"] - c["slow_mover_days_of_supply"] * slow["run_rate"]).clip(lower=0)
        slow["excess_value_at_cost"] = slow["excess_units"] * slow["avg_unit_cost"]
        tie = slow["excess_value_at_cost"].sum()
        top = slow.sort_values("excess_value_at_cost", ascending=False).head(10)[
            ["name", "on_hand", "run_rate", "days_of_supply", "excess_value_at_cost"]].round(1)
        out.append(_f("slow_movers", "all", as_of, "medium" if tie >= 20000 else "low",
                      f"{len(slow)} SKUs hold more than {c['slow_mover_days_of_supply']:.0f} days of supply; about {tie:,.0f} INR of excess stock at cost",
                      tie, dict(skus=len(slow), worst=top.to_dict("records")),
                      "Which of these SKUs are being phased out and which are stocked on purpose (launch, festival)?",
                      "Stop reordering these SKUs, test a 10-15% markdown, and bundle them with fast sellers.",
                      "Excess = units on hand beyond the slow-mover threshold of cover at the 28-day run-rate, valued at landed cost.",
                      current=False))
    low = inventory.low_stock(conn, as_of)
    if len(low):
        top = low.head(10)[["name", "on_hand", "run_rate", "days_of_supply"]].round(1)
        out.append(_f("low_stock", "all", as_of, "medium",
                      f"{len(low)} selling SKUs have less than {c['low_stock_days_of_supply']:.0f} days of cover",
                      None, dict(skus=len(low), most_urgent=top.to_dict("records")),
                      "Are purchase orders already placed for these SKUs, and when do they arrive?",
                      "Place or expedite POs today for the SKUs with the least cover.",
                      "Days of cover = units on hand / average daily units over the last 28 days.", current=True))
    return out


def reconciliation_findings(conn, as_of, monthly_rev):
    out = []
    x = db.read(conn, "select * from order_xref")
    sku_price = sales.summary(conn, "0000-01-01", as_of)
    price = sku_price["net_revenue"] / sku_price["units"] if sku_price["units"] else 0
    stuck = x[x["paid_not_dispatched"] == 1]
    if len(stuck):
        val = db.read(conn, "select sum(taxable_value) v from fact_order_line where order_ref in (%s)" %
                      ",".join("?" * len(stuck)), tuple(stuck["order_ref_n"]))["v"][0] or 0
        out.append(_f("paid_not_dispatched", "all", as_of, _severity(val, monthly_rev),
                      f"{len(stuck)} paid orders have not been dispatched after {int(context(conn)['stuck_after_days'])}+ days "
                      f"(oldest {stuck['order_date'].min()})",
                      val, dict(orders=len(stuck), oldest=stuck["order_date"].min(),
                                sample=stuck.sort_values("order_date")["order_ref_n"].head(10).tolist()),
                      "Why are these orders stuck: out of stock, pick-and-pack, address problem, or a billing-to-warehouse sync failure?",
                      "Dispatch or refund each order this week and add an alert for any paid order older than 48 hours without dispatch.",
                      "Value = taxable value (ex-GST) of the stuck orders.", current=True))
    nb = x[x["dispatched_not_billed"] == 1]
    if len(nb):
        w = db.read(conn, "select order_ref, qty from raw_wms_dispatch")
        w["ref"] = w["order_ref"].str.strip().str.upper()
        units = w[w["ref"].isin(set(nb["order_ref_n"]))]["qty"].astype(int).sum()
        out.append(_f("dispatched_not_billed", "all", as_of, "medium",
                      f"{len(nb)} orders shipped from the warehouse have no invoice (about {units} units)",
                      units * price, dict(orders=len(nb), units=int(units), sample=nb["order_ref_n"].head(10).tolist()),
                      "Are these phone or WhatsApp orders? Who is responsible for invoicing them?",
                      "Invoice them. Shipments without invoices can also be a GST compliance problem.",
                      "Value = units x average net price per unit (ex-GST), an approximation.", current=True))
    paid = x[x["billing_status"] == "paid"]
    gap = 100 * (paid["in_web"] == 0).mean() if len(paid) else 0
    out.append(_f("web_tracking_gap", "all", as_of, "low",
                  f"The website analytics miss {gap:.0f}% of paid orders, so website conversion is understated",
                  None, dict(paid_orders=len(paid), missing_in_web_pct=round(gap, 1)),
                  "Is consent mode on, and does the purchase event always send the transaction_id?",
                  "Send transaction_id on every purchase event; treat billing as the source of truth for revenue and the website only for behaviour.",
                  "Paid invoice orders with no matching website purchase event.", current=False))
    return out


def returns_findings(conn, as_of, start):
    out = []
    r = sales.returns_by_category(conn, start, as_of)
    for _, row in r[r["return_reason"].isin(["shade_mismatch", "skin_reaction"])].iterrows():
        if row["returned_lines"] >= 15 and row["reason_share_pct"] >= 45:
            v = db.read(conn, """select sum(f.refund_ex_tax) v from fact_order_line f join dim_product p using (master_sku_id)
                                 where p.category=? and f.return_reason=? and f.order_date between ? and ?""",
                        (row["category"], row["return_reason"], start, as_of))["v"][0] or 0
            nice = row["return_reason"].replace("_", " ")
            out.append(_f("return_reason", f"{row['category']}:{row['return_reason']}", start, "low",
                          f"{nice.title()} is {row['reason_share_pct']:.0f}% of {row['category']} returns (return rate {row['return_rate_pct']:.1f}%)",
                          v, row.to_dict(),
                          f"Do {row['category']} product pages show shade swatches / skin-type guidance?",
                          "Add a shade guide or sample sizes and tag returns by SKU to find the worst offenders.",
                          "Refunded revenue (ex-GST) for this category and reason over the period.", current=False))
    return out


# ---------------------------------------------------------------------------- orchestration
def run_all_findings(conn, as_of: str | None = None, history_start: str | None = None, leakage_days: int | None = None) -> list[dict]:
    as_of = as_of or _as_of(conn)
    first = history_start or db.read(conn, "select min(date) d from fact_inventory_daily")["d"][0]
    c = context(conn)
    rev90 = sales.summary(conn, shift(as_of, -89), as_of)["net_revenue"]
    monthly = rev90 / 3
    findings = []
    findings += stockout_findings(conn, as_of, first, monthly)
    findings += dispatch_delay_findings(conn, as_of, first, monthly)
    findings += conversion_drop_findings(conn, as_of, first, monthly)
    findings += leakage_findings(conn, as_of, shift(as_of, -leakage_days) if leakage_days else first, as_of,
                                 c["gross_margin_target_pct"])
    findings += expiry_findings(conn, as_of)
    findings += stock_position_findings(conn, as_of)
    findings += reconciliation_findings(conn, as_of, monthly)
    findings += returns_findings(conn, as_of, shift(as_of, -180))
    findings.sort(key=lambda f: (SEVERITY_RANK[f["severity"]], -(f["est_impact_inr"] or 0)))
    return findings


def save_findings(conn, findings: list[dict]) -> None:
    """Store findings and the questions they raise. Answers the owner already gave are kept."""
    now = datetime.now().isoformat(timespec="seconds")
    rows = [dict(id=f["id"], type=f["type"], severity=f["severity"], title=f["title"], start=f["start"], end=f["end"],
                 ongoing=int(f["ongoing"]), is_current=int(f["is_current"]), est_impact_inr=f["est_impact_inr"],
                 action=f["action"], calc=f["calc"], evidence_json=json.dumps(f["evidence"]), saved_at=now)
            for f in findings]
    db.write(conn, pd.DataFrame(rows), "findings")
    old = db.read(conn, "select * from questions") if db.table_exists(conn, "questions") else pd.DataFrame(
        columns=["finding_id", "question", "status", "answer", "answered_at"])
    keep = {r["finding_id"]: r for _, r in old.iterrows()}
    q = []
    for f in findings:
        if f["question"]:
            prev = keep.get(f["id"])
            q.append(dict(finding_id=f["id"], question=f["question"], status=prev["status"] if prev is not None else "open",
                          answer=prev["answer"] if prev is not None else None,
                          answered_at=prev["answered_at"] if prev is not None else None))
    db.write(conn, pd.DataFrame(q, columns=["finding_id", "question", "status", "answer", "answered_at"]), "questions")
    conn.commit()


def answer_question(conn, finding_id: str, answer: str) -> None:
    conn.execute("update questions set answer=?, status='answered', answered_at=? where finding_id=?",
                 (answer, datetime.now().isoformat(timespec="seconds"), finding_id))
    conn.commit()
