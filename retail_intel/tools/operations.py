"""Website, fulfilment, order-to-cash, findings and data-quality tools."""
from __future__ import annotations

from .. import db
from ..metrics import finance
from ..metrics import funnel_fulfilment as ff
from ..platform.registry import tool

D = {"type": "string", "description": "ISO date, YYYY-MM-DD"}
RANGE = {"start": D, "end": D}


@tool("website_funnel", "Daily sessions, item views, add-to-cart, checkouts, purchases and checkout-to-purchase rate.",
      RANGE, ["start", "end"])
def website_funnel(conn, start, end):
    return ff.daily_funnel(conn, start, end)


@tool("fulfilment_speed", "Daily average dispatch hours, share over SLA, cancellation and return-to-origin rates.",
      RANGE, ["start", "end"])
def fulfilment_speed(conn, start, end):
    return ff.daily_fulfilment(conn, start, end)


@tool("order_to_cash_gaps", "Order-to-cash reconciliation across website, billing and warehouse: paid but not dispatched, "
      "dispatched but not billed, web purchase not billed. Counts, billed value, sample order numbers, and a profile of "
      "each gap: where the orders are stuck, ages, spread over months, order-number prefix, payment mode, products, "
      "and whether a product was out of stock that day. Use the profile to explain WHY.")
def order_to_cash_gaps(conn):
    return finance.order_to_cash(conn)


@tool("list_findings", "Stored findings (what happened, rupee impact, suggested action), most severe first. "
      "Give start/end to get findings that overlap a period, including past ones. Optional type filter.",
      {"current_only": {"type": "boolean"}, "limit": {"type": "integer"}, "start": D, "end": D,
       "types": {"type": "array", "items": {"type": "string"}}})
def list_findings(conn, current_only=True, limit=10, start=None, end=None, types=None):
    sql = "select id, type, severity, title, start, \"end\", est_impact_inr, action, calc from findings where 1=1 "
    params: tuple = ()
    if current_only:
        sql += "and is_current = 1 "
    if start and end:
        sql += "and start <= ? and coalesce(\"end\", start) >= ? "
        params += (end, start)
    if types:
        sql += f"and type in ({','.join('?' * len(types))}) "
        params += tuple(types)
    sql += "order by case severity when 'high' then 0 when 'medium' then 1 else 2 end, est_impact_inr desc limit ?"
    return db.read(conn, sql, params + (limit,))


@tool("data_quality", "Latest data-quality checks across the three source systems.")
def data_quality(conn):
    return db.read(conn, "select check_name, severity, issue_count, detail from dq_issue")
