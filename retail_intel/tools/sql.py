"""The only free-form door: one read-only SELECT on an allow-list of tables, with row and time limits."""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from ..platform.registry import tool

SQL_TABLES = {"fact_order_line", "fact_inventory_daily", "fact_web_daily", "fact_web_sessions", "dim_product",
              "findings", "questions", "business_context", "recommendations", "order_xref"}
SQL_ROW_LIMIT = 200
SQL_SECONDS = 5


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


@tool("run_readonly_sql", "LAST RESORT when no other tool answers: one read-only SELECT on the clean tables "
      "(fact_order_line, fact_inventory_daily, fact_web_daily, dim_product, order_xref). Max 200 rows.",
      {"query": {"type": "string"}}, ["query"], tier=1)
def readonly_sql(conn, query):
    db_file = conn.execute("pragma database_list").fetchone()[2]     # the same database the toolbox opened
    return run_readonly_sql(db_file, query)
