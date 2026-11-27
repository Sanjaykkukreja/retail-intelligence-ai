"""Generate GlowBasket: three deliberately messy "client systems" plus a ground-truth file.

Systems (each uses its own SKU codes, like a real client would):
  website  - GA4-style export: item views, add-to-cart, checkouts, purchases
  billing  - Zoho-style invoices and refunds
  wms      - warehouse: daily stock by batch/expiry, receipts with cost, dispatches

Problems planted on purpose (all recorded in truth/ground_truth.json so tests can check the pipeline finds them):
  * different SKU codes in every system, some website items renamed
  * duplicate billing rows, messy order references, ~7% of web purchases untracked
  * orders paid but never dispatched, orders dispatched but never billed (manual orders)
  * negative stock rows in the WMS
  * a hero SKU out of stock for 9 days while web demand continues, and a second SKU out of stock right now
  * a one-week dispatch delay, a one-week payment/checkout failure
  * a Diwali sale where low-margin SKUs are discounted 35%
  * slow movers and batches close to expiry
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import SynthConfig

# type: (category, mrp_lo, mrp_hi, shelf_days, cost_lo, cost_hi, sizes)
TYPES = {
    "Serum": ("Skincare", 449, 899, 540, .28, .40, ["30ml"]),
    "Face Wash": ("Skincare", 199, 399, 730, .30, .42, ["100ml", "150ml"]),
    "Moisturizer": ("Skincare", 299, 649, 730, .30, .42, ["50g", "100g"]),
    "Sunscreen SPF50": ("Skincare", 349, 599, 730, .30, .40, ["50g"]),
    "Toner": ("Skincare", 249, 499, 540, .28, .40, ["100ml", "200ml"]),
    "Night Cream": ("Skincare", 399, 799, 540, .28, .40, ["50g"]),
    "Eye Cream": ("Skincare", 449, 849, 540, .28, .40, ["15g"]),
    "Face Mask": ("Skincare", 199, 449, 540, .30, .42, ["100g"]),
    "Shampoo": ("Haircare", 249, 549, 900, .32, .44, ["250ml", "340ml"]),
    "Conditioner": ("Haircare", 249, 549, 900, .32, .44, ["250ml"]),
    "Hair Oil": ("Haircare", 199, 499, 900, .30, .42, ["100ml", "200ml"]),
    "Hair Serum": ("Haircare", 299, 599, 730, .30, .42, ["50ml"]),
    "Body Lotion": ("Bath & Body", 249, 549, 730, .32, .44, ["200ml", "400ml"]),
    "Body Wash": ("Bath & Body", 199, 449, 730, .32, .44, ["250ml"]),
    "Lip Balm": ("Makeup", 149, 299, 730, .30, .42, ["4g"]),
    "Lipstick": ("Makeup", 299, 699, 900, .30, .42, ["4g"]),
    "Kajal": ("Makeup", 149, 349, 900, .30, .42, ["1g"]),
    "Primer": ("Makeup", 399, 799, 730, .30, .42, ["30ml"]),
    "Foundation": ("Makeup", 499, 999, 730, .30, .42, ["30ml"]),
    "Perfume": ("Fragrance", 499, 1299, 1095, .30, .42, ["50ml"]),
}
SHADE_TYPES = {"Lipstick", "Kajal", "Foundation", "Perfume"}
INGREDIENTS = ["Vitamin C", "Niacinamide", "Hyaluronic Acid", "Retinol", "Salicylic Acid", "Ceramide",
               "Aloe Vera", "Rose", "Tea Tree", "Argan", "Shea Butter", "Kojic Acid", "Peptide", "Bakuchiol",
               "Squalane", "Centella", "Green Tea", "Charcoal", "Turmeric", "Onion", "Rice Water", "Coconut",
               "Almond", "Lavender", "Cocoa", "Papaya", "Honey", "Oat", "Cucumber", "Sandalwood"]
SHADES = ["Ruby Red", "Nude Rose", "Coral Blush", "Berry Wine", "Mocha Brown", "Sand Beige", "Ivory Glow",
          "Jasmine", "Oud Noir", "White Musk", "Amber", "Peach Fizz"]
ADJECTIVES = ["Radiance", "Glow", "Pure", "Dew", "Silk", "Bloom", "Velvet", "Luminous", "Aqua", "Cloud", "Petal", "Prism"]
CAT3 = {"Skincare": "SKN", "Haircare": "HAR", "Bath & Body": "BTH", "Makeup": "MKP", "Fragrance": "FRG"}
RETURN_P = {"Skincare": .035, "Haircare": .025, "Bath & Body": .02, "Makeup": .06, "Fragrance": .03}
RETURN_REASONS = {
    "Skincare": (["skin_reaction", "damaged", "wrong_item", "changed_mind"], [.45, .2, .1, .25]),
    "Haircare": (["damaged", "wrong_item", "changed_mind", "skin_reaction"], [.3, .15, .4, .15]),
    "Bath & Body": (["damaged", "wrong_item", "changed_mind"], [.4, .2, .4]),
    "Makeup": (["shade_mismatch", "damaged", "changed_mind", "wrong_item"], [.6, .1, .2, .1]),
    "Fragrance": (["damaged", "changed_mind", "wrong_item"], [.3, .5, .2]),
}
DOW_FACTOR = [1.0, .95, .95, 1.0, 1.05, 1.15, 1.2]  # Mon..Sun
HOUR_W = np.array([.5, .3, .2, .2, .3, .5, 1, 2, 3, 4, 5, 5, 5, 5, 5, 5, 5, 6, 7, 8, 8, 7, 4, 2], float)
HOUR_W /= HOUR_W.sum()

HERO_BLOCK = (date(2026, 10, 12), date(2026, 10, 20))        # 9 days
SECOND_BLOCK_START = date(2026, 11, 21)                      # still out of stock at end of data
DISPATCH_DELAY = (date(2026, 9, 14), date(2026, 9, 20))
CHECKOUT_FAIL = (date(2026, 8, 3), date(2026, 8, 9))
DIWALI_SALE = (date(2026, 11, 1), date(2026, 11, 10))
EOSS = (date(2025, 11, 24), date(2025, 11, 30))


def _in(d: date, win: tuple[date, date]) -> bool:
    return win[0] <= d <= win[1]


def _build_products(cfg: SynthConfig, rng: np.random.Generator) -> list[dict]:
    combos = []
    for t, (cat, lo, hi, shelf, clo, chi, sizes) in TYPES.items():
        pool = SHADES if t in SHADE_TYPES else INGREDIENTS
        for ing in pool:
            for sz in sizes:
                combos.append((ing, t, sz))
    hero, second = ("Vitamin C", "Serum", "30ml"), ("Niacinamide", "Face Wash", "100ml")
    rest = [c for c in combos if c not in (hero, second)]
    order = rng.permutation(len(rest))
    chosen = [hero, second] + [rest[i] for i in order[: cfg.n_skus - 2]]
    prods = []
    for idx, (ing, t, sz) in enumerate(chosen):
        cat, lo, hi, shelf, clo, chi, _ = TYPES[t]
        mrp = float(round(rng.uniform(lo, hi) / 10) * 10 - 1)
        name = f"{ing} {t} {sz}"
        prods.append(dict(
            idx=idx, rank=idx + 1, name=name, type=t, category=cat, size=sz, mrp=mrp,
            cost_ratio=float(rng.uniform(clo, chi)), shelf_days=shelf,
            barcode=f"890{1_000_000_000 + idx * 104729:010d}",
            web_id=f"GB-{CAT3[cat]}-{idx:03d}",
            bill_code=f"{CAT3[cat]}{idx:03d}-{sz.upper()}",
            wms_sku=f"W{100000 + idx}",
            web_name=f"GlowBasket {name}",
            bill_desc=re.sub(r"(\d+)(ml|g)\b", r"\1 \2", name).upper(),
            wms_desc=name.replace("Vitamin", "Vit").replace("Hyaluronic Acid", "HA") + " GB",
            flags=set(),
        ))
    # shuffle ranks (popularity) but keep hero=1, second=2
    perm = rng.permutation(np.arange(3, cfg.n_skus + 1))
    for p, r in zip(prods[2:], perm):
        p["rank"] = int(r)
    prods[0]["rank"], prods[1]["rank"] = 1, 2
    by_rank = sorted(prods, key=lambda p: p["rank"])

    def pick(lo, hi, k, exclude_flags=()):
        pool = [p for p in by_rank if lo <= p["rank"] <= hi and not p["flags"] & set(exclude_flags)
                and p["idx"] > 1]
        sel = rng.choice(len(pool), size=k, replace=False)
        return [pool[i] for i in sel]

    for p in pick(170, cfg.n_skus, 8):
        p["flags"].add("slow")
    for p in pick(5, 60, 12, ("slow",)):
        p["flags"].add("leaky")
        p["cost_ratio"] = float(rng.uniform(.60, .64))
    for p in pick(40, 90, 3, ("slow", "leaky")):
        p["flags"].add("near_expiry")
    for p in pick(20, 150, 12, ("slow", "leaky", "near_expiry")):
        p["flags"].add("renamed")
        p["web_name"] = f"GlowBasket {ADJECTIVES[p['idx'] % len(ADJECTIVES)]} {p['type']} {p['size']}"
    return prods


def generate(out_dir: str | Path, truth_dir: str | Path, cfg: SynthConfig = SynthConfig()) -> dict:
    out_dir, truth_dir = Path(out_dir), Path(truth_dir)
    for sub in ("website", "billing", "wms"):
        (out_dir / sub).mkdir(parents=True, exist_ok=True)
    truth_dir.mkdir(parents=True, exist_ok=True)

    rng = np.random.default_rng(cfg.seed)
    prods = _build_products(cfg, rng)
    n = len(prods)
    days = [cfg.start + timedelta(d) for d in range((cfg.end - cfg.start).days + 1)]
    nd = len(days)
    START, END = cfg.start, cfg.end

    # ---- popularity and demand -------------------------------------------------
    w_by_idx = np.zeros(n)
    for p in prods:
        w_by_idx[p["idx"]] = p["rank"] ** -0.8
    w_by_idx /= w_by_idx.sum()

    def season(d: date) -> float:
        m = (d.year - START.year) * 12 + d.month - START.month
        f = DOW_FACTOR[d.weekday()] * (1 + 0.012 * m)
        if _in(d, DIWALI_SALE): f *= 1.9
        if _in(d, EOSS): f *= 1.4
        if date(2026, 2, 10) <= d <= date(2026, 2, 14): f *= 1.25
        return f

    raw = np.array([season(d) for d in days])
    target_attempts = cfg.target_orders / 0.90
    lam = raw / raw.sum() * target_attempts      # attempts per day, unaffected by failures
    avg_lines = 1.4
    for p in prods:
        p["rate_lines"] = float(w_by_idx[p["idx"]] * target_attempts * avg_lines / nd)
        p["rate_units"] = p["rate_lines"] * 1.18
        p["lead"] = int(rng.integers(10, 21))

    # ---- inventory ledger ------------------------------------------------------
    batches: list[list[dict]] = [[] for _ in range(n)]
    on_hand = np.zeros(n, dtype=int)
    open_po_qty = np.zeros(n, dtype=int)
    arrivals: dict[date, list[dict]] = {}
    receipts: list[dict] = []
    batch_counter = [0] * n
    blocks = {0: HERO_BLOCK, 1: (SECOND_BLOCK_START, END + timedelta(days=3))}
    writeoffs = {}

    def new_batch(p, rec_date, qty, expiry=None, age=None):
        batch_counter[p["idx"]] += 1
        if expiry is None:
            age = int(rng.integers(45, 121)) if age is None else age
            expiry = rec_date - timedelta(days=age) + timedelta(days=p["shelf_days"])
        cost = round(p["mrp"] * p["cost_ratio"] * (1 + rng.uniform(-.03, .03)), 2)
        return dict(batch_no=f"B{rec_date:%y%m}{p['idx']:03d}{batch_counter[p['idx']]}", expiry=expiry,
                    qty=int(qty), unit_cost=cost, received=rec_date)

    def add_batch(p, b):
        i = p["idx"]
        batches[i].append(b)
        batches[i].sort(key=lambda x: x["expiry"])
        on_hand[i] += b["qty"]
        receipts.append(dict(receipt_date=b["received"].isoformat(), wms_sku=p["wms_sku"], batch_no=b["batch_no"],
                             expiry_date=b["expiry"].isoformat(), qty=b["qty"], unit_cost=b["unit_cost"]))

    for p in prods:
        rec = START - timedelta(days=int(rng.integers(10, 61)))
        if "slow" in p["flags"]:
            qty = max(12, int(p["rate_units"] * 250))
            b = new_batch(p, rec, qty)
        elif "near_expiry" in p["flags"]:
            b = new_batch(p, rec, 300, expiry=END + timedelta(days=int(rng.integers(35, 61))))
        else:
            qty = max(12, int(p["rate_units"] * rng.uniform(35, 70)))
            b = new_batch(p, rec, qty)
        add_batch(p, b)

    def blocked(i: int, d: date) -> bool:
        return i in blocks and blocks[i][0] <= d <= blocks[i][1]

    # ---- accumulators ----------------------------------------------------------
    cust_pool: list[str] = []
    cust_n = 0
    order_no = 0
    billing_rows, refund_rows, wms_rows, web_purchase_rows, snap_rows = [], [], [], [], []
    checkout_units = np.zeros((n, nd), dtype=int)
    lost_units = np.zeros((n, nd), dtype=int)
    purchase_units_tracked = np.zeros((n, nd), dtype=int)
    truth_daily = {d: dict(orders=0, net_revenue=0.0, cogs=0.0) for d in days}
    counts = dict(messy_billing_refs=0, messy_wms_refs=0, manual_orders=0, stuck_orders=0, stuck_orders_old=0,
                  cancelled_orders=0, rto_orders=0, payment_failed_attempts=0, web_untracked=0, web_missing_txn=0)
    stuck_cutoff = END - timedelta(days=4)

    def p_fail(d):  return .50 if _in(d, CHECKOUT_FAIL) else .08
    def p_cancel(d): return .06 if _in(d, DISPATCH_DELAY) else .015
    def p_rto(d):   return .09 if _in(d, DISPATCH_DELAY) else .04

    for di, d in enumerate(days):
        # arrivals
        for po in arrivals.pop(d, []):
            i = po["idx"]
            open_po_qty[i] -= po["qty"]
            if blocked(i, d):
                arrivals.setdefault(blocks[i][1] + timedelta(days=1), []).append(po)
                open_po_qty[i] += po["qty"]
                continue
            add_batch(prods[i], new_batch(prods[i], d, po["qty"]))
        # planted stock-outs start (QC hold / lost stock) and the PO is delayed past the window
        for i, (bs, be) in blocks.items():
            if d == bs:
                writeoffs[i] = int(on_hand[i])
                batches[i] = []
                on_hand[i] = 0
                if open_po_qty[i] == 0:
                    q = max(24, int(prods[i]["rate_units"] * 50))
                    arrivals.setdefault(be + timedelta(days=1), []).append(dict(idx=i, qty=q))
                    open_po_qty[i] += q

        n_attempts = int(rng.poisson(lam[di]))
        for _ in range(n_attempts):
            k = int(rng.choice([1, 2, 3], p=[.7, .2, .1]))
            skus = rng.choice(n, size=k, replace=False, p=w_by_idx)
            lines = []
            for s in skus:
                q = int(rng.choice([1, 2, 3], p=[.85, .12, .03]))
                if on_hand[s] <= 0:
                    lost_units[s, di] += q
                    continue
                lines.append((int(s), min(q, int(on_hand[s]))))
            if not lines:
                continue
            for s, q in lines:
                checkout_units[s, di] += q
            if rng.random() < p_fail(d):
                counts["payment_failed_attempts"] += 1
                continue
            order_no += 1
            ref = f"GB{100000 + order_no}"
            hh = int(rng.choice(24, p=HOUR_W))
            ts = datetime(d.year, d.month, d.day, hh, int(rng.integers(0, 60)), int(rng.integers(0, 60)))
            cancelled = rng.random() < p_cancel(d)
            manual = (not cancelled) and rng.random() < 0.002
            if manual:
                ref = f"MN{100000 + order_no}"
                counts["manual_orders"] += 1
            if cancelled:
                counts["cancelled_orders"] += 1
            # customer
            if cust_pool and rng.random() < .30:
                cust = cust_pool[int(rng.integers(0, len(cust_pool)))]
            else:
                cust_n += 1
                cust = f"C{cust_n:06d}"
                cust_pool.append(cust)
            # discounts
            line_disc = []
            for s, q in lines:
                p = prods[s]
                if _in(d, DIWALI_SALE):
                    dp = .35 if "leaky" in p["flags"] else float(rng.choice([.15, .20, .25]))
                elif _in(d, EOSS):
                    dp = float(rng.choice([.15, .20]))
                else:
                    dp = float(rng.choice([0, .10, .15], p=[.6, .3, .1]))
                line_disc.append(dp)
            # fulfilment
            status_wms = None
            recv_ts = ts + timedelta(minutes=5)
            disp_ts = None
            if not cancelled:
                hours = float(rng.gamma(4, 20 if _in(d, DISPATCH_DELAY) else 5))
                disp_ts = recv_ts + timedelta(hours=hours)
                stuck = (d <= stuck_cutoff) and rng.random() < .003
                if stuck:
                    counts["stuck_orders"] += 1
                    disp_ts, status_wms = None, "PENDING"
                elif disp_ts > datetime(END.year, END.month, END.day, 23, 59, 59):
                    disp_ts, status_wms = None, "PENDING"
                elif rng.random() < p_rto(d):
                    status_wms = "RTO"
                    counts["rto_orders"] += 1
                else:
                    status_wms = "DISPATCHED"
            bref = ref
            if not manual and rng.random() < .015:
                bref = ref.lower() if rng.random() < .5 else ref + " "
                counts["messy_billing_refs"] += 1
            wref = ref
            if not cancelled and rng.random() < .01:
                wref = ref.lower()
                counts["messy_wms_refs"] += 1
            tracked = rng.random() < .93
            missing_txn = tracked and rng.random() < .05
            if not manual and not tracked:
                counts["web_untracked"] += 1
            if not manual and missing_txn:
                counts["web_missing_txn"] += 1
            order_rev = order_cogs = 0.0
            recognized = (not cancelled) and (not manual) and status_wms != "RTO"
            for ln, ((s, q), dp) in enumerate(zip(lines, line_disc), start=1):
                p = prods[s]
                gross = round(q * p["mrp"], 2)
                disc = round(gross * dp, 2)
                taxable = round(gross - disc, 2)
                tax = round(taxable * .18, 2)
                total = round(taxable + tax, 2)
                if not cancelled:
                    takes, remaining = [], q
                    for b in batches[s]:
                        if remaining <= 0: break
                        t = min(remaining, b["qty"])
                        if t > 0:
                            b["qty"] -= t; remaining -= t; takes.append((b, t))
                    batches[s] = [b for b in batches[s] if b["qty"] > 0]
                    on_hand[s] -= q
                    for b, t in takes:
                        wms_rows.append(dict(order_ref=wref, wms_sku=p["wms_sku"], batch_no=b["batch_no"], qty=t,
                                             order_received_at=recv_ts.isoformat(sep=" "),
                                             dispatched_at=disp_ts.isoformat(sep=" ") if disp_ts else "",
                                             status=status_wms))
                        order_cogs += t * b["unit_cost"]
                if not manual:
                    billing_rows.append(dict(
                        invoice_no=f"INV-{d:%Y}-{order_no:06d}", order_ref=bref, line_no=ln,
                        invoice_ts=ts.isoformat(sep=" "), customer_id=cust, sku_code=p["bill_code"],
                        barcode=p["barcode"], item_description=p["bill_desc"], qty=q, mrp=p["mrp"],
                        discount_amt=disc, taxable_value=taxable, tax_rate=18, tax_amt=tax, total_amt=total,
                        status="cancelled" if cancelled else "paid",
                        payment_mode=str(rng.choice(["UPI", "Card", "COD"], p=[.55, .25, .20]))))
                    # web purchase event (tracked or not as a whole order, like a real GA4 transaction)
                    if tracked:
                        web_purchase_rows.append(dict(transaction_id="" if missing_txn else ref, date=d.isoformat(),
                                                      item_id=p["web_id"], quantity=q))
                        purchase_units_tracked[s, di] += q
                if recognized:
                    order_rev += taxable
                    # refunds
                    if rng.random() < RETURN_P[p["category"]]:
                        reasons, pr = RETURN_REASONS[p["category"]]
                        rd = (disp_ts or recv_ts).date() + timedelta(days=int(rng.integers(6, 16)))
                        if disp_ts is not None and rd <= END:
                            refund_rows.append(dict(
                                refund_id=f"RF-{len(refund_rows) + 1:06d}", order_ref=ref, sku_code=p["bill_code"],
                                qty=q, refund_amt=total, reason=str(rng.choice(reasons, p=pr)),
                                refund_date=rd.isoformat()))
                            order_rev -= taxable
            if recognized:
                t = truth_daily[d]
                t["orders"] += 1; t["net_revenue"] += order_rev; t["cogs"] += order_cogs
            # truth for stuck old orders
            if status_wms == "PENDING" and d <= stuck_cutoff:
                counts["stuck_orders_old"] += 1

        # purchases for cancelled orders also fire web events (handled above); end-of-day snapshot
        for p in prods:
            i = p["idx"]
            if batches[i]:
                for b in batches[i]:
                    snap_rows.append((d.isoformat(), p["wms_sku"], b["batch_no"], b["expiry"].isoformat(), b["qty"]))
            else:
                snap_rows.append((d.isoformat(), p["wms_sku"], "NA", "", 0))
        # reorders
        for p in prods:
            i = p["idx"]
            if blocked(i, d):
                continue
            rop = p["rate_units"] * (p["lead"] + 7)
            if on_hand[i] + open_po_qty[i] < rop:
                q = max(12, int(p["rate_units"] * 50))
                lead = max(5, p["lead"] + int(rng.integers(-2, 4)))
                arrivals.setdefault(d + timedelta(days=lead), []).append(dict(idx=i, qty=q))
                open_po_qty[i] += q

    # ---- website daily files ---------------------------------------------------
    rate_day = np.outer(w_by_idx, lam / lam.sum() * target_attempts * avg_lines)   # expected lines/day
    views = rng.poisson(35 * rate_day)
    ckout = rng.binomial(checkout_units, .93)
    atc = rng.binomial(np.round(checkout_units * 1.7).astype(int) + rng.poisson(.02 * views), .93)
    web_daily = []
    for i, p in enumerate(prods):
        for di, d in enumerate(days):
            web_daily.append((d.isoformat(), p["web_id"], int(views[i, di]), int(atc[i, di]), int(ckout[i, di]),
                              int(purchase_units_tracked[i, di])))
    sessions = rng.poisson(lam / .022)
    pd.DataFrame(web_daily, columns=["date", "item_id", "item_views", "add_to_cart", "begin_checkout", "purchases"]
                 ).to_csv(out_dir / "website" / "web_daily_items.csv", index=False)
    pd.DataFrame(dict(date=[d.isoformat() for d in days], sessions=sessions)
                 ).to_csv(out_dir / "website" / "web_sessions.csv", index=False)
    pd.DataFrame(web_purchase_rows).to_csv(out_dir / "website" / "web_purchases.csv", index=False)
    pd.DataFrame([dict(item_id=p["web_id"], item_name=p["web_name"], item_category=p["category"]) for p in prods]
                 ).to_csv(out_dir / "website" / "web_items.csv", index=False)

    # ---- billing ---------------------------------------------------------------
    bdf = pd.DataFrame(billing_rows)
    dup_idx = rng.choice(len(bdf), size=int(len(bdf) * .005), replace=False)
    bdf = pd.concat([bdf, bdf.iloc[dup_idx]], ignore_index=True)
    bdf = bdf.sample(frac=1, random_state=cfg.seed).reset_index(drop=True)
    bdf.to_csv(out_dir / "billing" / "billing_invoices.csv", index=False)
    pd.DataFrame(refund_rows).to_csv(out_dir / "billing" / "billing_refunds.csv", index=False)

    # ---- warehouse -------------------------------------------------------------
    sdf = pd.DataFrame(snap_rows, columns=["snapshot_date", "wms_sku", "batch_no", "expiry_date", "qty_on_hand"])
    neg_rows = []
    for _ in range(3):
        j = int(rng.integers(len(sdf) // 3, len(sdf) - 5000))
        neg_rows.append(dict(snapshot_date=sdf.iloc[j]["snapshot_date"], wms_sku=sdf.iloc[j]["wms_sku"],
                             batch_no="ADJ", expiry_date="", qty_on_hand=-2))
    sdf = pd.concat([sdf, pd.DataFrame(neg_rows)], ignore_index=True)
    sdf.to_csv(out_dir / "wms" / "wms_stock_snapshots.csv", index=False)
    pd.DataFrame(receipts).to_csv(out_dir / "wms" / "wms_receipts.csv", index=False)
    pd.DataFrame(wms_rows).to_csv(out_dir / "wms" / "wms_dispatch.csv", index=False)
    pd.DataFrame([dict(wms_sku=p["wms_sku"], barcode=p["barcode"], description=p["wms_desc"], category=p["category"])
                  for p in prods]).to_csv(out_dir / "wms" / "wms_products.csv", index=False)

    # ---- ground truth ----------------------------------------------------------
    bar = {p["idx"]: p["barcode"] for p in prods}
    events = []
    snap_df = pd.DataFrame(snap_rows, columns=["d", "sku", "b", "e", "q"])
    tot = snap_df.groupby(["sku", "d"])["q"].sum().reset_index()
    sku_to_idx = {p["wms_sku"]: p["idx"] for p in prods}
    day_pos = {d.isoformat(): k for k, d in enumerate(days)}
    for sku, g in tot.groupby("sku"):
        g = g.sort_values("d")
        i = sku_to_idx[sku]
        run = []
        for dstr, q in zip(g["d"], g["q"]):
            if q == 0:
                run.append(dstr)
            else:
                if run:
                    events.append((i, run)); run = []
        if run:
            events.append((i, run))
    stockout_events = []
    for i, run in events:
        lost = int(sum(lost_units[i, day_pos[x]] for x in run))
        stockout_events.append(dict(barcode=bar[i], start=run[0], end=run[-1], days=len(run),
                                    ongoing=run[-1] == END.isoformat(), lost_units_realized=lost))
    near = []
    for p in prods:
        for b in batches[p["idx"]]:
            if b["expiry"] <= END + timedelta(days=90):
                near.append(dict(barcode=p["barcode"], batch_no=b["batch_no"], expiry=b["expiry"].isoformat(),
                                 qty=int(b["qty"])))
    # daily truth
    td = pd.DataFrame([dict(date=d.isoformat(), orders=v["orders"], net_revenue=round(v["net_revenue"], 2),
                            cogs=round(v["cogs"], 2)) for d, v in truth_daily.items()])
    td.to_csv(truth_dir / "ground_truth_daily.csv", index=False)

    truth = dict(
        as_of=END.isoformat(),
        counts=dict(counts, duplicate_billing_rows=int(len(dup_idx)), negative_stock_rows=3,
                    billing_orders=int(bdf["invoice_no"].nunique()), n_skus=n),
        planted=dict(
            hero=dict(barcode=bar[0], start=HERO_BLOCK[0].isoformat(), end=HERO_BLOCK[1].isoformat(), days=9),
            second=dict(barcode=bar[1], start=SECOND_BLOCK_START.isoformat(), end=END.isoformat(), days=3, ongoing=True),
            dispatch_delay=dict(start=DISPATCH_DELAY[0].isoformat(), end=DISPATCH_DELAY[1].isoformat()),
            checkout_failure=dict(start=CHECKOUT_FAIL[0].isoformat(), end=CHECKOUT_FAIL[1].isoformat()),
            diwali_sale=dict(start=DIWALI_SALE[0].isoformat(), end=DIWALI_SALE[1].isoformat()),
            leaky_barcodes=[p["barcode"] for p in prods if "leaky" in p["flags"]],
            slow_barcodes=[p["barcode"] for p in prods if "slow" in p["flags"]],
            near_expiry_plant_barcodes=[p["barcode"] for p in prods if "near_expiry" in p["flags"]],
            renamed_web_ids=[p["web_id"] for p in prods if "renamed" in p["flags"]],
        ),
        stockout_events=stockout_events,
        near_expiry_final=near,
        sku_map={**{p["web_id"]: p["barcode"] for p in prods}, **{p["bill_code"]: p["barcode"] for p in prods},
                 **{p["wms_sku"]: p["barcode"] for p in prods}},
    )
    (truth_dir / "ground_truth.json").write_text(json.dumps(truth, indent=1))
    return truth


if __name__ == "__main__":
    from ..config import SOURCE_DIR, TRUTH_DIR
    t = generate(SOURCE_DIR, TRUTH_DIR)
    print(json.dumps(t["counts"], indent=1))
