"""Sales, margin, discount and returns tools."""
from __future__ import annotations

from ..briefing import build_briefing
from ..metrics import sales
from ..metrics.common import context
from ..platform.registry import tool

D = {"type": "string", "description": "ISO date, YYYY-MM-DD"}
RANGE = {"start": D, "end": D}


@tool("get_briefing", "The daily briefing: latest day's numbers, top actions, open questions, data health.", {"day": D})
def get_briefing(conn, day=None):
    return {k: v for k, v in build_briefing(conn, day).items() if k != "markdown"}


@tool("get_sales_summary", "Net revenue, orders, units, gross margin, discounts, cancellations for a date range "
      "(ex-GST, net of refunds).", RANGE, ["start", "end"])
def get_sales_summary(conn, start, end):
    return sales.summary(conn, start, end)


@tool("compare_day", "One day versus previous day, trailing 7-day average and same weekday.", {"day": D}, ["day"])
def compare_day(conn, day):
    return sales.day_vs_baseline(conn, day)


@tool("top_skus", "Best (or worst) SKUs by net revenue in a range, with gross margin and average discount.",
      {**RANGE, "limit": {"type": "integer"}, "worst": {"type": "boolean"}}, ["start", "end"])
def top_skus(conn, start, end, limit=10, worst=False):
    df = sales.by_sku(conn, start, end)
    return df.tail(limit) if worst else df.head(limit)


@tool("category_performance", "Net revenue and gross margin by category for a date range.", RANGE, ["start", "end"])
def category_performance(conn, start, end):
    return sales.by_category(conn, start, end)


@tool("discount_leakage", "SKUs whose heavily discounted (25%+) sales earned less than the target gross margin.",
      RANGE, ["start", "end"])
def discount_leakage(conn, start, end):
    return sales.discount_leakage(conn, start, end, context(conn)["gross_margin_target_pct"]).head(20)


@tool("return_reasons", "Returns by category and reason (shade mismatch, skin reaction, damaged ...).", RANGE, ["start", "end"])
def return_reasons(conn, start, end):
    return sales.returns_by_category(conn, start, end)
