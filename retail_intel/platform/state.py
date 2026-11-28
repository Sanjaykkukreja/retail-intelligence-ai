"""Permanent storage for what PEOPLE did, so it survives restarts and redeploys (Supabase Postgres).

What is kept:     conversations, questions asked and their cost, approvals and decisions, owner answers,
                  business rules and who changed them, sign-offs, promotion requests.
What is not kept: the business data itself (sales, stock). That is rebuilt from the client's systems by the
                  daily refresh, so client data never needs to sit in our database longer than one run.

Everything lives in ONE Postgres table, keyed by client, so one Supabase project can hold several clients and
the schema never needs migrating when a feature adds a column:

    ria_state(client, tbl, row_key, seq, row jsonb, saved_at)

Two kinds of table:
  append   rows are only ever added (conversations, runs): new rows are sent after each change
  replace  small tables whose rows change (recommendation status, answers, rules): sent in full after each change

Switched on by the SUPABASE_DB_URL secret. Without it the app works exactly as before (local SQLite only).
"""
from __future__ import annotations

import hashlib
import json
import os

STATE_TABLES = {
    "agent_runs": "append",
    "agent_messages": "append",
    "definition_signoffs": "append",
    "rule_changes": "append",
    "recommendations": "replace",
    "questions": "replace",
    "business_context": "replace",
    "question_promotions": "replace",
}

SCHEMA = """create table if not exists ria_state (
    client text not null, tbl text not null, row_key text not null, seq bigint not null,
    row jsonb not null, saved_at timestamptz not null default now(),
    primary key (client, tbl, row_key))"""


def _psycopg_connect(url: str):
    import psycopg                                   # installed on the cloud from requirements.txt
    return psycopg.connect(url, connect_timeout=10, prepare_threshold=None)


def _rows(conn, table: str, after_rowid: int = 0) -> list[tuple[int, dict]]:
    cur = conn.execute(f'select rowid as _rid, * from "{table}" where rowid > ? order by rowid', (after_rowid,))
    cols = [c[0] for c in cur.description]
    return [(r[0], {c: v for c, v in zip(cols[1:], r[1:])}) for r in cur.fetchall()]


def _key(row: dict) -> str:
    return hashlib.sha1(json.dumps(row, sort_keys=True, default=str).encode()).hexdigest()


def _exists(conn, table: str) -> bool:
    return conn.execute("select 1 from sqlite_master where type='table' and name=?", (table,)).fetchone() is not None


class StateStore:
    def __init__(self, url: str, client_id: str, connect=None):
        self.url, self.client = url, client_id
        self._connect = connect or _psycopg_connect
        self._marks: dict[str, int] = {}             # last rowid already saved, per append table

    @classmethod
    def from_env(cls, client_id: str) -> "StateStore | None":
        url = os.environ.get("SUPABASE_DB_URL", "").strip()
        return cls(url, client_id) if url else None

    def _run(self, work):
        pg = self._connect(self.url)
        try:
            cur = pg.cursor()
            out = work(cur)
            pg.commit()
            return out
        finally:
            pg.close()

    def ensure(self) -> None:
        self._run(lambda cur: cur.execute(SCHEMA))

    def save(self, conn, tables: list[str] | None = None) -> dict:
        """Send what changed since the last save. Safe to call after every action."""
        plan = []
        for t, mode in STATE_TABLES.items():
            if (tables and t not in tables) or not _exists(conn, t):
                continue
            if mode == "append":
                rows = _rows(conn, t, self._marks.get(t, 0))
                plan.append((t, mode, rows))
            else:
                plan.append((t, mode, _rows(conn, t)))
        ins = ("insert into ria_state (client, tbl, row_key, seq, row) values (%s, %s, %s, %s, %s::jsonb) "
               "on conflict (client, tbl, row_key) do nothing")

        def work(cur):
            sent = {}
            for t, mode, rows in plan:
                if mode == "replace":
                    cur.execute("delete from ria_state where client = %s and tbl = %s", (self.client, t))
                    params = [(self.client, t, f"{i:09d}", i, json.dumps(r, default=str)) for i, (_, r) in enumerate(rows)]
                else:
                    params = [(self.client, t, _key(r), rid, json.dumps(r, default=str)) for rid, r in rows]
                if params:
                    cur.executemany(ins, params)
                sent[t] = len(params)
            return sent

        sent = self._run(work)
        for t, mode, rows in plan:                      # only after the commit succeeded
            if mode == "append" and rows:
                self._marks[t] = rows[-1][0]
        return sent

    def restore(self, conn) -> dict:
        """Load saved state into the local database (run once when the app starts)."""
        def work(cur):
            cur.execute("select tbl, row from ria_state where client = %s order by tbl, seq", (self.client,))
            return cur.fetchall()

        by_table: dict[str, list[dict]] = {}
        for tbl, row in self._run(work):
            by_table.setdefault(tbl, []).append(json.loads(row) if isinstance(row, str) else row)
        _ensure_local_tables(conn)
        restored = {}
        for t, rows in by_table.items():
            if t not in STATE_TABLES or not rows:
                continue
            cols = list(dict.fromkeys(c for r in rows for c in r))
            if _exists(conn, t):
                have = {r[1] for r in conn.execute(f'pragma table_info("{t}")')}
                for c in cols:
                    if c not in have:
                        conn.execute(f'alter table "{t}" add column "{c}"')
                conn.execute(f'delete from "{t}"')
            else:
                conn.execute(f'create table "{t}" (' + ", ".join(f'"{c}"' for c in cols) + ")")
            conn.executemany(f'insert into "{t}" (' + ", ".join(f'"{c}"' for c in cols) + ") values ("
                             + ", ".join("?" * len(cols)) + ")", [tuple(r.get(c) for c in cols) for r in rows])
            restored[t] = len(rows)
        conn.commit()
        for t, mode in STATE_TABLES.items():              # what is local now is already saved
            if mode == "append" and _exists(conn, t):
                self._marks[t] = conn.execute(f'select coalesce(max(rowid), 0) from "{t}"').fetchone()[0]
        return restored

    def spend(self) -> list[tuple]:
        """Total AI cost and questions per client straight from the permanent store (for a cross-client view)."""
        def work(cur):
            cur.execute("""select client, count(*), coalesce(sum((row->>'cost_usd')::float), 0)
                           from ria_state where tbl = 'agent_runs' group by client order by client""")
            return cur.fetchall()
        return self._run(work)


def _ensure_local_tables(conn) -> None:
    """Tables with special local schemas (auto-numbered IDs) are created by their owners before rows come back."""
    from ..actions import ensure as ensure_recs
    from ..agent.memory import ensure as ensure_messages
    ensure_recs(conn)
    ensure_messages(conn)
