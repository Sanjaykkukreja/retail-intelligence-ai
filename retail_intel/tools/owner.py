"""What the owner has told us, and the tools that record the owner's answers, decisions and rules."""
from __future__ import annotations

from .. import db
from ..actions import decide, list_recommendations as _list_recs
from ..config import DEFAULT_CONTEXT
from ..findings import answer_question
from ..platform.registry import tool


@tool("open_questions", "Questions waiting for the owner's answer.", cacheable=False)
def open_questions(conn):
    return db.read(conn, "select finding_id, question from questions where status='open'")


@tool("owner_answers", "Answers the owner has already given (use before asking again).", cacheable=False)
def owner_answers(conn):
    return db.read(conn, "select finding_id, question, answer, answered_at from questions where status='answered'")


@tool("list_recommendations", "Tracked recommendations and their status (proposed, approved, rejected, done, expired).",
      {"status": {"type": "string"}}, cacheable=False)
def list_recommendations(conn, status=None):
    return _list_recs(conn, status)


@tool("record_answer", "Save the owner's answer to an open question. Only when the owner has stated the answer.",
      {"finding_id": {"type": "string"}, "answer": {"type": "string"}}, ["finding_id", "answer"], side_effect=True)
def record_answer(conn, finding_id, answer):
    answer_question(conn, finding_id, answer)
    return {"saved": True}


@tool("decide_recommendation", "Approve or reject a recommendation. ONLY when the owner explicitly says so in this message.",
      {"rec_id": {"type": "integer"}, "approve": {"type": "boolean"}, "note": {"type": "string"}}, ["rec_id", "approve"],
      side_effect=True)
def decide_recommendation(conn, rec_id, approve, note=""):
    return decide(conn, int(rec_id), bool(approve), note)


@tool("update_business_rule", "Change a business rule the owner states (e.g. gross_margin_target_pct). Allowed keys: "
      + ", ".join(sorted(DEFAULT_CONTEXT)) + ".", {"key": {"type": "string"}, "value": {"type": "number"}},
      ["key", "value"], side_effect=True)
def update_business_rule(conn, key, value):
    if key not in DEFAULT_CONTEXT:
        raise ValueError(f"unknown rule '{key}'. Allowed: {sorted(DEFAULT_CONTEXT)}")
    conn.execute("delete from business_context where key=?", (key,))
    conn.execute("insert into business_context (key, value) values (?, ?)", (key, str(float(value))))
    conn.commit()
    return {"saved": True, "key": key, "value": float(value)}
