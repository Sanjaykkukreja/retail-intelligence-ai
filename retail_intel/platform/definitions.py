"""The definitions register: every rule the numbers depend on, in plain words, with its current value and who
signed it off. This is the client-facing answer to "who decided what counts as a stuck order?".

Two kinds:
  rule        a number the owner can change (business_context): margin target, dispatch promise, stuck-after days...
  definition  a fixed meaning built into the tested code (net revenue, gross margin, stockout ...). Changing one is a
              code change with a test, made by us and signed off by the client.
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd

from .. import db
from ..config import DEFAULT_CONTEXT
from ..metrics.common import context

RULES = {
    "gross_margin_target_pct": ("Gross margin target", "%", "Margin the owner wants to protect. Discounts that earn less are leakage."),
    "dispatch_sla_hours": ("Dispatch promise", "hours", "Order-to-dispatch promise. Orders slower than this are delayed."),
    "stuck_after_days": ("Stuck order", "days", "A paid order not dispatched after this many days counts as stuck."),
    "low_stock_days_of_supply": ("Low stock", "days of cover", "Below this, a selling SKU needs a reorder now."),
    "slow_mover_days_of_supply": ("Slow mover", "days of cover", "Above this, a SKU is slow stock."),
    "expiry_horizon_days": ("Expiry watch window", "days", "Batches expiring within this window are checked for unsold units."),
    "lookback_days": ("Sales-rate window", "days", "Days used to compute the daily sales rate and days of cover."),
    "markdown_elasticity": ("Markdown response (assumed)", "x", "ASSUMPTION, not measured: +1% discount gives this % more units."),
}

DEFINITIONS = [
    ("Net revenue", "Taxable value after discount, excluding GST, minus refunds, on recognised sales only.", "core/build_core.py"),
    ("Recognised sale", "An invoice line marked paid whose order was not returned to origin (cancelled lines are never paid).", "core/build_core.py"),
    ("Cost of goods (COGS)", "Units x landed cost of the batch they were shipped from.", "core/build_core.py"),
    ("Gross margin", "Net revenue minus COGS. Not contribution margin: shipping, payment fees and marketing are not included.", "core/build_core.py"),
    ("Stockout", "A SKU with zero units in the warehouse's end-of-day count for 2+ days in a row.", "metrics/inventory.py"),
    ("Lost sales (estimate)", "Units per day before the stockout x days out x average price before the stockout. Assumes demand stayed steady.", "metrics/inventory.py"),
    ("Discount leakage", "SKUs sold at 25%+ discount (3+ lines) whose gross margin before refunds is below the margin target; the shortfall in rupees.", "metrics/sales.py"),
    ("Delayed order", "Dispatched later than the dispatch promise, or still not dispatched past it.", "metrics/orders.py"),
    ("Order out of stock at order", "A product in the order showed zero in the end-of-day count on the order day (overstates true backorders).", "metrics/orders.py"),
    ("Sell-through", "Units sold / (units sold + units on hand at period end).", "metrics/orders.py"),
    ("GMROI", "Gross margin / average stock at cost, scaled to a year: margin earned per rupee of stock.", "metrics/orders.py"),
    ("Paid but not dispatched", "Paid in billing; missing in the warehouse or PENDING there; older than the stuck-order rule.", "reconcile/order_matching.py"),
    ("Dispatched but not billed", "In the warehouse, with no invoice in billing.", "reconcile/order_matching.py"),
]


def ensure(conn) -> None:
    conn.execute("create table if not exists definition_signoffs (name text, value text, signed_by text, signed_at text, note text)")
    conn.execute("create table if not exists rule_changes (key text, old_value text, new_value text, changed_by text, changed_at text)")
    conn.commit()


def register(conn) -> pd.DataFrame:
    """One row per rule or definition, with the latest sign-off."""
    ensure(conn)
    ctx = context(conn)
    sign = db.read(conn, "select name, value, signed_by, signed_at from definition_signoffs order by signed_at")
    last = {r["name"]: r for _, r in sign.iterrows()}
    rows = []
    for key, (label, unit, meaning) in RULES.items():
        v = ctx.get(key, DEFAULT_CONTEXT.get(key))
        s = last.get(label)
        rows.append(dict(kind="rule", name=label, key=key, value=f"{v:g} {unit}", meaning=meaning,
                         default=f"{DEFAULT_CONTEXT[key]:g} {unit}",
                         signed_off=(f"{s['signed_by']} on {s['signed_at'][:10]}" + ("" if s["value"] == f"{v:g} {unit}"
                                     else " (value changed since)")) if s is not None else "not signed off"))
    for label, meaning, where in DEFINITIONS:
        s = last.get(label)
        rows.append(dict(kind="definition", name=label, key="", value="", meaning=meaning, default=where,
                         signed_off=f"{s['signed_by']} on {s['signed_at'][:10]}" if s is not None else "not signed off"))
    return pd.DataFrame(rows)


def sign_off(conn, name: str, signed_by: str, note: str = "") -> None:
    if not signed_by.strip():
        raise ValueError("say who is signing off")
    reg = register(conn)
    row = reg[reg["name"] == name]
    if row.empty:
        raise ValueError(f"unknown definition '{name}'")
    conn.execute("insert into definition_signoffs values (?,?,?,?,?)",
                 (name, row["value"].iloc[0], signed_by.strip(), datetime.now().isoformat(timespec="seconds"), note))
    conn.commit()


def change_rule(conn, key: str, value: float, changed_by: str) -> dict:
    """Change an owner rule and log who changed it. Numbers built in the daily refresh change from the next refresh."""
    if key not in RULES:
        raise ValueError(f"unknown rule '{key}'")
    ensure(conn)
    old = context(conn)[key]
    conn.execute("create table if not exists business_context (key text, value text)")
    conn.execute("delete from business_context where key=?", (key,))
    conn.execute("insert into business_context (key, value) values (?, ?)", (key, str(float(value))))
    conn.execute("insert into rule_changes values (?,?,?,?,?)", (key, f"{old:g}", f"{float(value):g}", changed_by or "unknown",
                                                                 datetime.now().isoformat(timespec="seconds")))
    conn.commit()
    return dict(key=key, old=old, new=float(value))
