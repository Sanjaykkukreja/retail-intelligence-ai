"""Question-family tools: one tool, many questions, by changing settings."""
from __future__ import annotations

from ..metrics import orders
from ..platform.registry import tool

D = {"type": "string", "description": "ISO date, YYYY-MM-DD"}


@tool("order_list", "Order questions of every kind in one tool. Filter by status (all, delayed, dispatched_late, "
      "not_dispatched, cancelled, rto, out_of_stock_at_order), age in days, payment mode (COD, UPI, Card), order-date "
      "period and delay threshold in hours (default: the dispatch promise). Lists orders, or counts them grouped by "
      "payment_mode, month, day, status or category. Returns the definition used, so state it in the answer.",
      {"status": {"type": "string", "enum": orders.STATUSES}, "min_age_days": {"type": "integer"},
       "max_age_days": {"type": "integer"}, "payment_mode": {"type": "string"}, "start": D, "end": D,
       "delay_over_hours": {"type": "number"},
       "group_by": {"type": "string", "enum": [g for g in orders.GROUPS if g]}, "limit": {"type": "integer"}})
def order_list(conn, status="all", min_age_days=None, max_age_days=None, payment_mode=None, start=None, end=None,
               delay_over_hours=None, group_by=None, limit=20):
    return orders.order_list(conn, status, min_age_days, max_age_days, payment_mode, start, end, delay_over_hours,
                             group_by, limit)


@tool("sell_through", "Sell-through %, annual stock turn, GMROI (gross margin per rupee of average stock) and cash tied "
      "up in stock at period end, by category or SKU, for a period.",
      {"start": D, "end": D, "group_by": {"type": "string", "enum": ["category", "sku"]}, "limit": {"type": "integer"}},
      ["start", "end"])
def sell_through(conn, start, end, group_by="category", limit=20):
    return orders.sell_through(conn, start, end, group_by, limit)
