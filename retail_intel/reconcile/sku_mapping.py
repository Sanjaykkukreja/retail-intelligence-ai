"""Layer 2a - One master SKU for every product, across three systems that name things differently.

Rules, strongest evidence first. Only the first three are accepted automatically:
  1. barcode        billing and warehouse both carry the barcode -> exact link (master id = barcode)
  2. title_exact    website title == billing description after normalising (case, spaces, "30 ml" vs "30ml")
  3. order_evidence orders that contain ONE website item and ONE billing line vote for the pairing;
                    accepted only with >= 10 votes and >= 90% agreement
  4. fuzzy_title    similar-looking titles are only PROPOSED -> goes to the human review queue
  5. human_override rows in data/sku_map_overrides.csv always win

A wrong mapping silently corrupts every insight, so anything unsure is held back, never guessed.
"""
from __future__ import annotations

import difflib
import re
from pathlib import Path

import pandas as pd

from .. import db

MIN_VOTES = 10
MIN_SHARE = 0.90
FUZZY_MIN = 0.80


def normalize_title(s: str) -> str:
    s = s.lower().replace("glowbasket", " ")
    s = re.sub(r"(\d+)\s*(ml|g)\b", r"\1\2", s)
    return " ".join(sorted(re.findall(r"[a-z0-9]+", s)))


def norm_ref(series: pd.Series) -> pd.Series:
    return series.str.strip().str.upper()


def _row(system, code, master, method, conf, status, note=""):
    return dict(system=system, source_code=code, master_sku_id=master, method=method,
                confidence=round(float(conf), 3), status=status, note=note)


def build_sku_map(conn, overrides_path: str | Path | None = None) -> pd.DataFrame:
    bill = db.read(conn, "select distinct sku_code, barcode, item_description from raw_billing_invoices")
    wms = db.read(conn, "select wms_sku, barcode, description, category from raw_wms_products")
    web = db.read(conn, "select item_id, item_name from raw_web_items")
    rows = []

    # 1. barcode links (billing + warehouse)
    for code, g in bill.groupby("sku_code"):
        codes = g["barcode"].unique()
        if len(codes) == 1 and codes[0]:
            rows.append(_row("billing", code, codes[0], "barcode", 1.0, "accepted"))
        else:
            rows.append(_row("billing", code, None, "barcode", 0, "review", "billing code has 0 or several barcodes"))
    for _, r in wms.iterrows():
        if r["barcode"]:
            rows.append(_row("wms", r["wms_sku"], r["barcode"], "barcode", 1.0, "accepted"))
        else:
            rows.append(_row("wms", r["wms_sku"], None, "barcode", 0, "review", "warehouse SKU has no barcode"))

    # 2. website title == billing description
    bill_norm = {}
    for _, r in bill.drop_duplicates("barcode").iterrows():
        bill_norm.setdefault(normalize_title(r["item_description"]), []).append(r["barcode"])

    # 3. order evidence from orders with exactly one web item and exactly one billing line
    wp = db.read(conn, "select transaction_id, item_id from raw_web_purchases where transaction_id <> ''")
    wp["ref"] = norm_ref(wp["transaction_id"])
    single_web = wp.groupby("ref")["item_id"].nunique()
    wp = wp[wp["ref"].isin(single_web[single_web == 1].index)].drop_duplicates("ref")
    bl = db.read(conn, "select invoice_no, line_no, order_ref, barcode from raw_billing_invoices")
    bl = bl.drop_duplicates(["invoice_no", "line_no"])
    bl["ref"] = norm_ref(bl["order_ref"])
    lines_per_ref = bl.groupby("ref")["line_no"].count()
    bl = bl[bl["ref"].isin(lines_per_ref[lines_per_ref == 1].index)]
    votes = wp.merge(bl[["ref", "barcode"]], on="ref").groupby(["item_id", "barcode"]).size().reset_index(name="n")

    fuzzy_pool = list(bill_norm.keys())
    for _, r in web.iterrows():
        item, name = r["item_id"], r["item_name"]
        key = normalize_title(name)
        if len(bill_norm.get(key, [])) == 1:
            rows.append(_row("website", item, bill_norm[key][0], "title_exact", .98, "accepted"))
            continue
        v = votes[votes["item_id"] == item].sort_values("n", ascending=False)
        if len(v):
            top, total = v.iloc[0], v["n"].sum()
            share = top["n"] / total
            if total >= MIN_VOTES and share >= MIN_SHARE:
                rows.append(_row("website", item, top["barcode"], "order_evidence", share, "accepted",
                                 f"{int(top['n'])} of {int(total)} single-item orders agree"))
                continue
        near = difflib.get_close_matches(key, fuzzy_pool, n=1, cutoff=FUZZY_MIN)
        if near and len(bill_norm[near[0]]) == 1:
            ratio = difflib.SequenceMatcher(None, key, near[0]).ratio()
            rows.append(_row("website", item, bill_norm[near[0]][0], "fuzzy_title", ratio, "review",
                             "similar title only - needs human approval"))
        else:
            rows.append(_row("website", item, None, "none", 0, "unmapped", "no evidence found"))

    sku_map = pd.DataFrame(rows)

    # 5. human overrides always win
    if overrides_path and Path(overrides_path).exists():
        ov = pd.read_csv(overrides_path, dtype=str)
        for _, o in ov.iterrows():
            m = (sku_map["system"] == o["system"]) & (sku_map["source_code"] == o["source_code"])
            sku_map = sku_map[~m]
            sku_map = pd.concat([sku_map, pd.DataFrame([_row(o["system"], o["source_code"], o["master_sku_id"],
                                                             "human_override", 1.0, "accepted")])], ignore_index=True)
    db.write(conn, sku_map, "sku_map")

    # master product table: one row per barcode
    cat = wms.set_index("barcode")["category"].to_dict()
    name_by_barcode = {}
    for _, r in bill.iterrows():
        name_by_barcode.setdefault(r["barcode"], re.sub(r"(\d+) (Ml|G)\b", lambda m: m.group(1) + m.group(2).lower(),
                                                       r["item_description"].title()))
    masters = sorted(set(wms["barcode"]) | set(bill["barcode"]))
    dim = pd.DataFrame(dict(master_sku_id=masters,
                            name=[name_by_barcode.get(b, "") for b in masters],
                            category=[cat.get(b, "Unknown") for b in masters]))
    db.write(conn, dim, "dim_product_base")
    conn.commit()
    return sku_map


def review_queue(conn) -> pd.DataFrame:
    return db.read(conn, "select * from sku_map where status <> 'accepted' order by system, source_code")
