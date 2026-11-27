"""Does the pipeline recover everything the generator secretly planted, and does revenue tie out?

The generator keeps its own independent ledger (ground truth). These tests compare the pipeline's answers
with that ledger. A pass means the numbers are right on this data, not that real client data will be clean.
"""
import json

import numpy as np
import pandas as pd

from retail_intel import db
from retail_intel.agent.tools import Toolbox
from retail_intel.briefing import build_briefing
from retail_intel.findings import answer_question, run_all_findings, save_findings
from retail_intel.metrics import inventory, sales
from retail_intel.metrics.common import context
from retail_intel.pipeline import run_pipeline


# ------------------------------------------------------------------ master SKU mapping
def test_sku_mapping_has_no_wrong_accepts(world):
    m = db.read(world["conn"], "select * from sku_map")
    acc = m[m["status"] == "accepted"]
    truth = world["truth"]["sku_map"]
    wrong = acc[acc.apply(lambda r: truth[r["source_code"]] != r["master_sku_id"], axis=1)]
    assert len(wrong) == 0, wrong
    assert len(acc) >= 590          # 600 codes in three systems; renamed website items resolved by order evidence


def test_renamed_website_items_resolved_by_order_evidence(world):
    m = db.read(world["conn"], "select * from sku_map where system='website'").set_index("source_code")
    for web_id in world["truth"]["planted"]["renamed_web_ids"]:
        row = m.loc[web_id]
        assert row["method"] in ("order_evidence", "fuzzy_title", "none")      # never the easy title match
        if row["status"] == "accepted":
            assert row["master_sku_id"] == world["truth"]["sku_map"][web_id]


def test_fuzzy_matches_are_never_auto_accepted(world):
    m = db.read(world["conn"], "select method, status from sku_map where method = 'fuzzy_title'")
    assert (m["status"] == "review").all()


# ------------------------------------------------------------------ reconciliation counts
def test_quality_checks_find_every_planted_issue(world):
    dq = db.read(world["conn"], "select check_name, issue_count from dq_issue").set_index("check_name")["issue_count"]
    c = world["truth"]["counts"]
    assert dq["billing_duplicate_rows"] == c["duplicate_billing_rows"]
    assert dq["billing_messy_order_refs"] == c["messy_billing_refs"]
    assert dq["wms_messy_order_refs"] == c["messy_wms_refs"]
    assert dq["wms_negative_stock_rows"] == c["negative_stock_rows"]
    assert dq["orders_paid_not_dispatched"] == c["stuck_orders_old"]
    assert dq["orders_dispatched_not_billed"] == c["manual_orders"]
    assert dq["web_purchase_not_billed"] == 0


def test_website_tracking_gap_is_reported(world):
    dq = db.read(world["conn"], "select check_name, issue_count from dq_issue").set_index("check_name")["issue_count"]
    assert 5 <= dq["billing_orders_missing_in_web_pct"] <= 15        # ~7% untracked + 5% missing transaction id


# ------------------------------------------------------------------ revenue ties out
def test_daily_revenue_cogs_orders_match_ledger(world):
    truth = pd.read_csv(world["truth_dir"] / "ground_truth_daily.csv")
    core = sales.daily(world["conn"], truth["date"].min(), truth["date"].max())
    orders = db.read(world["conn"], """select order_date as date, count(distinct case when recognized then order_ref end) as orders
                                       from fact_order_line group by order_date""")
    m = truth.merge(core, on="date", suffixes=("_t", "_c")).merge(orders, on="date", suffixes=("", "_o"))
    assert len(m) == len(truth)
    assert np.allclose(m["net_revenue_t"], m["net_revenue_c"].fillna(0), atol=1.0)
    assert np.allclose(m["cogs_t"], m["cogs_c"].fillna(0), atol=1.0)
    assert (m["orders_t"] == m["orders"]).all()


def test_core_reconciles_to_raw(world):
    assert bool(world["result"]["reconciliation"]["ok"].iloc[0])


# ------------------------------------------------------------------ planted business problems
def test_stockouts_match_warehouse_ledger(world):
    conn, truth = world["conn"], world["truth"]
    det = inventory.stockout_events(conn, min_days=2, min_lost_units=1)
    key = lambda df: {(r["master_sku_id"] if "master_sku_id" in r else r["barcode"], r["start"], r["end"]) for _, r in df.iterrows()}
    led = pd.DataFrame(truth["stockout_events"])
    big = led[(led["days"] >= 3) & (led["lost_units_realized"] >= 10)]
    assert len(big) >= 2 and key(big) <= key(det)                     # every material ledger event is detected
    det_big = det[det["est_lost_units"] >= 10]
    assert key(det_big) <= key(led)                                   # nothing invented


def test_hero_stockout_and_current_stockout(world):
    conn, truth = world["conn"], world["truth"]
    det = inventory.stockout_events(conn, min_days=2, min_lost_units=1)
    h = truth["planted"]["hero"]
    hero = det[(det["master_sku_id"] == h["barcode"]) & (det["start"] == h["start"])].iloc[0]
    assert hero["days"] == 9 and hero["end"] == h["end"] and not hero["ongoing"]
    realized = next(e["lost_units_realized"] for e in truth["stockout_events"]
                    if e["barcode"] == h["barcode"] and e["start"] == h["start"])
    assert abs(hero["est_lost_units"] - realized) / realized <= 0.35          # estimate within 35% of what truly was lost
    assert hero["web_views_per_day_during"] >= 0.8 * hero["web_views_per_day_before"]   # demand continued
    s = truth["planted"]["second"]
    second = det[(det["master_sku_id"] == s["barcode"]) & (det["start"] == s["start"])].iloc[0]
    assert bool(second["ongoing"]) and second["days"] == 3


def _finding(findings, type_):
    return [f for f in findings if f["type"] == type_]


def test_dispatch_delay_window_found(world):
    f = _finding(run_all_findings(world["conn"]), "dispatch_delay")
    p = world["truth"]["planted"]["dispatch_delay"]
    assert len(f) == 1 and f[0]["start"] == p["start"] and f[0]["end"] == p["end"]
    assert f[0]["evidence"]["window_avg_hours"] > 60 and f[0]["est_impact_inr"] > 0


def test_checkout_failure_window_found(world):
    f = _finding(run_all_findings(world["conn"]), "checkout_drop")
    p = world["truth"]["planted"]["checkout_failure"]
    assert len(f) == 1 and f[0]["start"] == p["start"] and f[0]["end"] == p["end"]


def test_discount_leakage_flags_exactly_the_planted_skus(world):
    conn, p = world["conn"], world["truth"]["planted"]
    lk = sales.discount_leakage(conn, p["diwali_sale"]["start"], p["diwali_sale"]["end"], context(conn)["gross_margin_target_pct"])
    flagged, planted = set(lk["master_sku_id"]), set(p["leaky_barcodes"])
    assert flagged <= planted                                           # no innocent SKU accused
    assert len(flagged) >= 0.9 * len(planted)                           # nearly all found (some sell too few units)


def test_expiry_exposure_matches_warehouse_ledger(world):
    ex = inventory.expiry_exposure(world["conn"], horizon_days=90)
    got = {(r["master_sku_id"], r["batch_no"], int(r["qty"])) for _, r in ex.iterrows()}
    want = {(b["barcode"], b["batch_no"], b["qty"]) for b in world["truth"]["near_expiry_final"]}
    assert got == want and len(want) >= 3


def test_slow_movers_match_independent_calculation(world):
    """Recompute days of supply straight from the raw files, using the ground-truth SKU map."""
    truth = world["truth"]["sku_map"]
    src = world["src"]
    snap = pd.read_csv(src / "wms" / "wms_stock_snapshots.csv", dtype={"wms_sku": str})
    end = world["truth"]["as_of"]
    on_hand = snap[(snap["snapshot_date"] == end) & (snap["qty_on_hand"] >= 0)].groupby("wms_sku")["qty_on_hand"].sum()
    on_hand.index = on_hand.index.map(truth)
    b = pd.read_csv(src / "billing" / "billing_invoices.csv").drop_duplicates(["invoice_no", "line_no"])
    b = b[(b["status"] == "paid") & (b["invoice_ts"].str[:10] > (pd.Timestamp(end) - pd.Timedelta(days=28)).date().isoformat())]
    b["m"] = b["sku_code"].map(truth)
    rate = b.groupby("m")["qty"].sum() / 28
    df = pd.DataFrame({"on_hand": on_hand}).join(rate.rename("rate")).fillna({"rate": 0})
    df = df[df["on_hand"] > 0]
    want = set(df[(df["rate"] == 0) | (df["on_hand"] / df["rate"].replace(0, np.nan) > 120)].index)
    got = set(inventory.slow_movers(world["conn"])["master_sku_id"])
    assert got == want and len(want) >= 3


# ------------------------------------------------------------------ the gate stops bad data
def test_gate_blocks_stale_warehouse_feed(world, fresh_copy, tmp_path):
    f = fresh_copy / "wms" / "wms_stock_snapshots.csv"
    s = pd.read_csv(f, dtype=str)
    s[s["snapshot_date"] <= "2026-11-17"].to_csv(f, index=False)         # warehouse stopped sending 6 days ago
    r = run_pipeline(fresh_copy, tmp_path / "x.db", overrides_path=None, with_findings=False)
    assert r["status"] == "blocked"
    assert "source_freshness_lag_days" in set(r["blockers"]["check_name"])


def test_gate_blocks_when_big_sku_has_no_master(world, fresh_copy, tmp_path):
    f = fresh_copy / "billing" / "billing_invoices.csv"
    b = pd.read_csv(f, dtype=str, keep_default_na=False)
    hero_code = next(c for c, m in world["truth"]["sku_map"].items() if m == world["truth"]["planted"]["hero"]["barcode"]
                     and c.startswith("SKN"))
    b.loc[b["sku_code"] == hero_code, "barcode"] = ""                    # barcode lost for the best seller
    b.to_csv(f, index=False)
    r = run_pipeline(fresh_copy, tmp_path / "y.db", overrides_path=None, with_findings=False)
    assert r["status"] == "blocked"
    assert "billing_sku_unmapped_revenue_pct" in set(r["blockers"]["check_name"])


# ------------------------------------------------------------------ agent tools, briefing, questions
def test_every_agent_tool_runs_and_sql_is_locked_down(world):
    box = Toolbox(world["db_path"])
    day, start = world["truth"]["as_of"], "2026-10-01"
    calls = {"get_briefing": {}, "get_sales_summary": dict(start=start, end=day), "compare_day": dict(day=day),
             "top_skus": dict(start=start, end=day), "category_performance": dict(start=start, end=day),
             "discount_leakage": dict(start=start, end=day), "stock_position": dict(name_contains="serum"),
             "stockout_events": {}, "expiry_exposure": {}, "slow_movers": {}, "website_funnel": dict(start=start, end=day),
             "fulfilment_speed": dict(start=start, end=day), "return_reasons": dict(start=start, end=day),
             "list_findings": {}, "open_questions": {}, "data_quality": {}}
    for name, args in calls.items():
        out = box.call(name, args)
        assert not (isinstance(out, dict) and "error" in out), (name, out)
    ok = box.call("run_readonly_sql", dict(query="select count(*) c from dim_product"))
    assert ok["rows"][0][0] == world["truth"]["counts"]["n_skus"]
    for bad in ("select * from raw_billing_invoices", "drop table dim_product", "update dim_product set name='x'",
                "select * from fact_order_line; drop table dim_product"):
        assert "error" in box.call("run_readonly_sql", dict(query=bad)), bad
    assert db.read(world["conn"], "select count(*) c from dim_product")["c"][0] == world["truth"]["counts"]["n_skus"]


def test_briefing_shows_the_planted_problems(world):
    md = build_briefing(world["conn"])["markdown"]
    assert "Niacinamide Face Wash" in md and "out of stock" in md
    assert "paid orders have not been dispatched" in md
    assert "no invoice" in md


def test_owner_answers_survive_a_rerun(world):
    conn = world["conn"]
    findings = run_all_findings(conn)
    save_findings(conn, findings)
    fid = next(f["id"] for f in findings if f["type"] == "stockout" and f["ongoing"])
    answer_question(conn, fid, "PO delayed by supplier, arrives 3 Dec")
    save_findings(conn, run_all_findings(conn))
    row = db.read(conn, "select status, answer from questions where finding_id = ?", (fid,)).iloc[0]
    assert row["status"] == "answered" and "3 Dec" in row["answer"]
