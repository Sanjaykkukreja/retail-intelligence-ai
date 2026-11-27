"""Component 3 - Memory.

Short-term:  the last few question/answer turns of this chat session (stored, so a chat survives a restart).
Long-term:   what the owner has told the system - business rules, answers to questions, decisions on
             recommendations - injected into every prompt so the agent never asks the same thing twice.
The business data itself (sales, stock) is not "memory": the agent reads it fresh through tools.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from .. import db
from ..metrics.common import as_of as _as_of, context


def ensure(conn) -> None:
    conn.execute("create table if not exists agent_messages (session_id text, ts text, role text, content text)")
    conn.commit()


def new_session() -> str:
    return uuid.uuid4().hex[:12]


def history(conn, session_id: str, max_turns: int = 6) -> list[dict]:
    ensure(conn)
    rows = conn.execute("select role, content from agent_messages where session_id=? order by rowid desc limit ?",
                        (session_id, 2 * max_turns)).fetchall()
    return [dict(role=r[0], content=r[1]) for r in reversed(rows)]


def append(conn, session_id: str, role: str, content: str) -> None:
    ensure(conn)
    conn.execute("insert into agent_messages values (?,?,?,?)", (session_id, datetime.now().isoformat(timespec="seconds"), role, content))
    conn.commit()


def long_term_context(conn, max_items: int = 15) -> str:
    lines = [f"Data is complete through {_as_of(conn)} (treat that as 'today' / 'yesterday')."]
    lines.append("Business rules: " + ", ".join(f"{k}={v:g}" for k, v in context(conn).items()))
    if db.table_exists(conn, "questions"):
        ans = conn.execute("select question, answer from questions where status='answered' order by answered_at desc limit ?",
                           (max_items,)).fetchall()
        if ans:
            lines.append("What the owner has already told us:")
            lines += [f"- Q: {q}  A: {a}" for q, a in ans]
    if db.table_exists(conn, "recommendations"):
        dec = conn.execute("""select id, status, title, coalesce(decision_note,'') from recommendations
                              where status in ('approved','rejected','done') order by decided_at desc limit ?""", (max_items,)).fetchall()
        if dec:
            lines.append("Owner decisions on recommendations:")
            lines += [f"- #{i} {s}: {t}" + (f" (note: {n})" if n else "") for i, s, t, n in dec]
    return "\n".join(lines)
