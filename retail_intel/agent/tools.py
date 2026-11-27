"""Component 2 - Tools. Every number the agent shows comes from one of these tested functions.

Each tool carries:
  tier         cost hierarchy, cheapest first:  0 = local tested metric, 1 = free-form SQL fallback
  cacheable    read-only results are cached per data version (a new pipeline run clears the cache)
  side_effect  writes something (owner answers, approvals, business rules); never cached, only offered
               when the owner is clearly instructing

The only free-form door is run_readonly_sql: read-only connection, table allow-list, row and time limits.
Every call (cached or not) is logged in tool_calls_log.
"""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from .. import db
from ..actions import decide as _decide, list_recommendations as _list_recs
from ..briefing import build_briefing
from ..config import DEFAULT_CONTEXT
from ..findings import answer_question
from ..metrics import funnel_fulfilment as ff
from ..metrics import inventory, sales
from ..metrics.common import context

SQL_TABLES = {"fact_order_line", "fact_inventory_daily", "fact_web_daily", "fact_web_sessions", "dim_product",
              "findings", "questions", "business_context", "recommendations"}
SQL_ROW_LIMIT = 200
SQL_SECONDS = 5
MAX_RESULT_ROWS = 40
_CACHE: dict[tuple, object] = {}


def _json_safe(o):
    if isinstance(o, pd.DataFrame):      # tables come back with an explicit truncation flag, never silently cut
        return dict(rows=json.loads(o.head(MAX_RESULT_ROWS).round(2).to_json(orient="records")),
                    total_rows=len(o), truncated=len(o) > MAX_RESULT_ROWS)
    if isinstance(o, pd.Series):
        return json.loads(o.round(2).to_json())
    if isinstance(o, (np.integer,)): return int(o)
    if isinstance(o, (np.floating,)): return None if np.isnan(o) else round(float(o), 2)
    if isinstance(o, float): return round(o, 2)
    if isinstance(o, dict): return {k: _json_safe(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)): return [_json_safe(x) for x in o]
    return o


def is_empty(result) -> bool:
    return result in (None, [], {}) or (isinstance(result, dict) and result.get("rows") == [])


def run_readonly_sql(db_path: str | Path, query: str, limit: int = SQL_ROW_LIMIT) -> dict:
    conn = sqlite3.connect(f"file:{Path(db_path).resolve()}?mode=ro", uri=True, timeout=30)
    deadline = time.time() + SQL_SECONDS

    def authorizer(action, a1, a2, dbname, source):
        if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE):
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ and a1 in SQL_TABLES:
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY

    conn.set_authorizer(authorizer)
    conn.set_progress_handler(lambda: 1 if time.time() > deadline else 0, 20000)
    try:
        cur = conn.execute(query)
        cols = [c[0] for c in cur.description]
        rows = cur.fetchmany(min(limit, SQL_ROW_LIMIT))
        return dict(columns=cols, rows=[list(r) for r in rows], truncated=len(rows) == min(limit, SQL_ROW_LIMIT),
                    note=f"read-only; allowed tables: {sorted(SQL_TABLES)}")
    finally:
        conn.close()


@dataclass
class ToolResult:
    name: str
    args: dict
    ok: bool
    result: object
    cached: bool
    ms: int


class Toolbox:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        self.specs = self._specs()
        self.by_name = {s["name"]: s for s in self.specs}

    # ---- plumbing -----------------------------------------------------------------------------------
    def schemas(self, names: list[str] | None = None) -> list[dict]:
        """Tool definitions (name, description, JSON schema). Adapters convert to each provider's format."""
        wanted = self.specs if names is None else [self.by_name[n] for n in dict.fromkeys(names) if n in self.by_name]
        return [dict(name=s["name"], description=s["description"], input_schema=s["input_schema"]) for s in wanted]

    def data_version(self) -> str:
        conn = db.connect(self.db_path)
        try:
            if db.table_exists(conn, "pipeline_runs"):
                return conn.execute("select max(run_at) from pipeline_runs").fetchone()[0] or "0"
            return "0"
        finally:
            conn.close()

    def call(self, name: str, args: dict | None = None) -> object:
        return self.call_ex(name, args).result

    def call_ex(self, name: str, args: dict | None = None) -> ToolResult:
        args = args or {}
        t0 = time.time()
        spec = self.by_name.get(name)
        if spec is None:
            tr = ToolResult(name, args, False, {"error": f"unknown tool {name}"}, False, 0)
            self._log(tr)
            return tr
        key = (self.db_path, self.data_version(), name, json.dumps(args, sort_keys=True, default=str))
        if spec["cacheable"] and key in _CACHE:
            tr = ToolResult(name, args, True, _CACHE[key], True, int(1000 * (time.time() - t0)))
            self._log(tr)
            return tr
        conn = db.connect(self.db_path)
        try:
            result, ok = _json_safe(spec["fn"](conn, **args)), True
        except Exception as e:  # errors go back to the model so it can correct itself (conditional fallback)
            result, ok = {"error": f"{type(e).__name__}: {e}"}, False
        finally:
            conn.close()
        if ok and spec["cacheable"]:
            _CACHE[key] = result
        tr = ToolResult(name, args, ok, result, False, int(1000 * (time.time() - t0)))
        self._log(tr)
        return tr

    def _log(self, tr: ToolResult) -> None:
        conn = db.connect(self.db_path)
        try:
            conn.execute("create table if not exists tool_calls_log (ts text, tool text, args text, ok integer, cached integer, ms integer)")
            conn.execute("insert into tool_calls_log values (?,?,?,?,?,?)",
                         (datetime.now().isoformat(timespec="seconds"), tr.name, json.dumps(tr.args, default=str),
                          int(tr.ok), int(tr.cached), tr.ms))
            conn.commit()
        finally:
            conn.close()

    # ---- tool definitions ------------------------------------------------------------------------------
    def _specs(self) -> list[dict]:
        D = {"type": "string", "description": "ISO date, YYYY-MM-DD"}

        def spec(name, desc, props, required, fn, tier=0, cacheable=True, side_effect=False):
            return dict(name=name, description=desc, fn=fn, tier=tier, cacheable=cacheable and not side_effect,
                        side_effect=side_effect, input_schema=dict(type="object", properties=props, required=required))

        def _resolve(conn, sku: str) -> str:
            r = db.read(conn, "select master_sku_id from dim_product where master_sku_id = ? or lower(name) like ?",
                        (sku, f"%{sku.lower()}%"))
            if r.empty:
                raise ValueError(f"no product matches '{sku}'. Use stock_position to list product names.")
            return r["master_sku_id"].iloc[0]

        def _set_rule(conn, key: str, value: float):
            if key not in DEFAULT_CONTEXT:
                raise ValueError(f"unknown rule '{key}'. Allowed: {sorted(DEFAULT_CONTEXT)}")
            conn.execute("delete from business_context where key=?", (key,))
            conn.execute("insert into business_context (key, value) values (?, ?)", (key, str(float(value))))
            conn.commit()
            return {"saved": True, "key": key, "value": float(value)}

        return [
            spec("get_briefing", "The daily briefing: latest day's numbers, top actions, open questions, data health.",
                 {"day": D}, [], lambda conn, day=None: {k: v for k, v in build_briefing(conn, day).items() if k != "markdown"}),
            spec("get_sales_summary", "Net revenue, orders, units, gross margin, discounts, cancellations for a date range (ex-GST, net of refunds).",
                 {"start": D, "end": D}, ["start", "end"], lambda conn, start, end: sales.summary(conn, start, end)),
            spec("compare_day", "One day versus previous day, trailing 7-day average and same weekday.",
                 {"day": D}, ["day"], lambda conn, day: sales.day_vs_baseline(conn, day)),
            spec("top_skus", "Best (or worst) SKUs by net revenue in a range, with gross margin and average discount.",
                 {"start": D, "end": D, "limit": {"type": "integer"}, "worst": {"type": "boolean"}}, ["start", "end"],
                 lambda conn, start, end, limit=10, worst=False: (sales.by_sku(conn, start, end).tail(limit) if worst
                                                                  else sales.by_sku(conn, start, end).head(limit))),
            spec("category_performance", "Net revenue and gross margin by category for a date range.",
                 {"start": D, "end": D}, ["start", "end"], lambda conn, start, end: sales.by_category(conn, start, end)),
            spec("discount_leakage", "SKUs whose heavily discounted (25%+) sales earned less than the target gross margin.",
                 {"start": D, "end": D}, ["start", "end"],
                 lambda conn, start, end: sales.discount_leakage(conn, start, end, context(conn)["gross_margin_target_pct"]).head(20)),
            spec("stock_position", "Units on hand, 28-day run-rate and days of cover per SKU, lowest cover first. Optional product-name filter.",
                 {"name_contains": {"type": "string"}, "limit": {"type": "integer"}}, [],
                 lambda conn, name_contains="", limit=15: inventory.position(conn)
                 .pipe(lambda p: p[p["name"].str.lower().str.contains(name_contains.lower(), regex=False)])
                 .sort_values("days_of_supply").head(limit)),
            spec("stockout_events", "Periods when a SKU had zero stock (ongoing ones flagged), with estimated lost units and revenue.",
                 {"min_days": {"type": "integer"}, "limit": {"type": "integer"}}, [],
                 lambda conn, min_days=2, limit=15: inventory.stockout_events(conn, min_days=min_days, min_lost_units=5).head(limit)),
            spec("expiry_exposure", "Batches expiring within the horizon and units that will not sell before expiry.",
                 {"limit": {"type": "integer"}}, [], lambda conn, limit=15: inventory.expiry_exposure(conn).head(limit)),
            spec("markdown_scenarios", "Outcome of 0-40% discounts on an expiring batch. Uses an ASSUMED price elasticity.",
                 {"sku": {"type": "string", "description": "master SKU id or product name fragment"}, "batch_no": {"type": "string"}},
                 ["sku"], lambda conn, sku, batch_no=None: inventory.markdown_scenarios(conn, _resolve(conn, sku), batch_no)),
            spec("slow_movers", "SKUs holding more than the slow-mover threshold of days of supply.",
                 {"limit": {"type": "integer"}}, [], lambda conn, limit=15: inventory.slow_movers(conn).head(limit)),
            spec("website_funnel", "Daily sessions, item views, add-to-cart, checkouts, purchases and checkout-to-purchase rate.",
                 {"start": D, "end": D}, ["start", "end"], lambda conn, start, end: ff.daily_funnel(conn, start, end)),
            spec("fulfilment_speed", "Daily average dispatch hours, share over SLA, cancellation and return-to-origin rates.",
                 {"start": D, "end": D}, ["start", "end"], lambda conn, start, end: ff.daily_fulfilment(conn, start, end)),
            spec("return_reasons", "Returns by category and reason (shade mismatch, skin reaction, damaged ...).",
                 {"start": D, "end": D}, ["start", "end"], lambda conn, start, end: sales.returns_by_category(conn, start, end)),
            spec("list_findings", "Stored findings (what happened, rupee impact, suggested action), most severe first.",
                 {"current_only": {"type": "boolean"}, "limit": {"type": "integer"}}, [],
                 lambda conn, current_only=True, limit=10: db.read(
                     conn, "select id, type, severity, title, start, end, est_impact_inr, action, calc from findings "
                           + ("where is_current = 1 " if current_only else "")
                           + "order by case severity when 'high' then 0 when 'medium' then 1 else 2 end, est_impact_inr desc limit ?",
                     (limit,))),
            spec("data_quality", "Latest data-quality checks across the three source systems.", {}, [],
                 lambda conn: db.read(conn, "select check_name, severity, issue_count, detail from dq_issue")),
            spec("open_questions", "Questions waiting for the owner's answer.", {}, [],
                 lambda conn: db.read(conn, "select finding_id, question from questions where status='open'"), cacheable=False),
            spec("owner_answers", "Answers the owner has already given (use before asking again).", {}, [],
                 lambda conn: db.read(conn, "select finding_id, question, answer, answered_at from questions where status='answered'"),
                 cacheable=False),
            spec("list_recommendations", "Tracked recommendations and their status (proposed, approved, rejected, done, expired).",
                 {"status": {"type": "string"}}, [], lambda conn, status=None: _list_recs(conn, status), cacheable=False),
            spec("record_answer", "Save the owner's answer to an open question. Only when the owner has stated the answer.",
                 {"finding_id": {"type": "string"}, "answer": {"type": "string"}}, ["finding_id", "answer"],
                 lambda conn, finding_id, answer: (answer_question(conn, finding_id, answer), {"saved": True})[1], side_effect=True),
            spec("decide_recommendation", "Approve or reject a recommendation. ONLY when the owner explicitly says so in this message.",
                 {"rec_id": {"type": "integer"}, "approve": {"type": "boolean"}, "note": {"type": "string"}}, ["rec_id", "approve"],
                 lambda conn, rec_id, approve, note="": _decide(conn, int(rec_id), bool(approve), note), side_effect=True),
            spec("update_business_rule", "Change a business rule the owner states (e.g. gross_margin_target_pct). Allowed keys: "
                 + ", ".join(sorted(DEFAULT_CONTEXT)) + ".",
                 {"key": {"type": "string"}, "value": {"type": "number"}}, ["key", "value"], _set_rule, side_effect=True),
            spec("run_readonly_sql", "LAST RESORT when no other tool answers: one read-only SELECT on the clean tables "
                                     "(fact_order_line, fact_inventory_daily, fact_web_daily, dim_product). Max 200 rows.",
                 {"query": {"type": "string"}}, ["query"], lambda conn, query: run_readonly_sql(self.db_path, query), tier=1),
        ]
