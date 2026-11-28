"""Component 4 - Task manager. Every recommendation becomes a tracked task:

    proposed -> approved | rejected -> done -> outcome checked
                (proposed -> expired when the problem disappears before anyone decided)

The system proposes; only the owner approves. After approval it checks whether the problem actually went away,
so over time you can see which advice paid off.
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd

from . import db
from .metrics.common import as_of as _as_of

SCHEMA = """create table if not exists recommendations (
    id integer primary key autoincrement, finding_id text unique, type text, severity text, title text,
    action text, est_impact_inr real, status text, created_at text, decided_at text, decision_note text,
    done_at text, outcome_note text, outcome_checked_at text)"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def ensure(conn) -> None:
    conn.execute(SCHEMA)
    conn.commit()


def sync_recommendations(conn, findings: list[dict]) -> dict:
    """Create a proposed task for each current high/medium finding. Never overwrite a decision already made."""
    ensure(conn)
    current = {f["id"]: f for f in findings if f["is_current"] and f["severity"] in ("high", "medium") and f["action"]}
    existing = db.read(conn, "select id, finding_id, status from recommendations")
    known = dict(zip(existing["finding_id"], existing["status"]))
    created = updated = expired = 0
    for fid, f in current.items():
        if fid not in known:
            conn.execute("""insert into recommendations (finding_id, type, severity, title, action, est_impact_inr, status, created_at)
                            values (?,?,?,?,?,?, 'proposed', ?)""",
                         (fid, f["type"], f["severity"], f["title"], f["action"], f["est_impact_inr"], _now()))
            created += 1
        elif known[fid] == "proposed":
            conn.execute("update recommendations set title=?, action=?, est_impact_inr=?, severity=? where finding_id=?",
                         (f["title"], f["action"], f["est_impact_inr"], f["severity"], fid))
            updated += 1
    for fid, status in known.items():
        if status == "proposed" and fid not in current:
            conn.execute("update recommendations set status='expired', outcome_note='problem no longer current' where finding_id=?", (fid,))
            expired += 1
    conn.commit()
    return dict(created=created, updated=updated, expired=expired)


def list_recommendations(conn, status: str | None = None) -> pd.DataFrame:
    ensure(conn)
    sql = "select id, status, type, severity, title, action, est_impact_inr, decision_note, outcome_note from recommendations"
    df = db.read(conn, sql + (" where status = ?" if status else "") +
                 " order by case severity when 'high' then 0 when 'medium' then 1 else 2 end, est_impact_inr desc",
                 (status,) if status else ())
    return df


def decide(conn, rec_id: int, approve: bool, note: str = "") -> dict:
    ensure(conn)
    row = conn.execute("select status from recommendations where id=?", (rec_id,)).fetchone()
    if row is None:
        raise ValueError(f"no recommendation #{rec_id}")
    if row[0] != "proposed":
        raise ValueError(f"recommendation #{rec_id} is already {row[0]}")
    new = "approved" if approve else "rejected"
    conn.execute("update recommendations set status=?, decided_at=?, decision_note=? where id=?", (new, _now(), note, rec_id))
    conn.commit()
    return dict(id=rec_id, status=new)


def mark_done(conn, rec_id: int, note: str = "") -> dict:
    ensure(conn)
    row = conn.execute("select status from recommendations where id=?", (rec_id,)).fetchone()
    if row is None or row[0] != "approved":
        raise ValueError(f"recommendation #{rec_id} must be approved before it can be done")
    conn.execute("update recommendations set status='done', done_at=?, decision_note=coalesce(decision_note,'') || ? where id=?",
                 (_now(), f" | done: {note}" if note else "", rec_id))
    conn.commit()
    return dict(id=rec_id, status="done")


def check_outcomes(conn, as_of: str | None = None) -> pd.DataFrame:
    """For approved/done tasks: is the problem still there? Written back as outcome_note."""
    ensure(conn)
    as_of = as_of or _as_of(conn)
    recs = db.read(conn, "select id, finding_id, type from recommendations where status in ('approved','done')")
    for _, r in recs.iterrows():
        parts = r["finding_id"].split(":")
        note = "check manually"
        if r["type"] == "stockout":
            q = conn.execute("select coalesce(sum(qty),0) from fact_inventory_daily where date=? and master_sku_id=?",
                             (as_of, parts[1])).fetchone()[0]
            note = f"back in stock ({q} units)" if q > 0 else "still out of stock"
        elif r["type"] == "expiry_risk":
            q = conn.execute("select coalesce(sum(qty),0) from fact_inventory_daily where date=? and master_sku_id=? and batch_no=?",
                             (as_of, parts[1], parts[2])).fetchone()[0]
            note = f"{q} units of the batch still on hand"
        elif r["type"] == "paid_not_dispatched":
            n = conn.execute("select count(*) from order_xref where paid_not_dispatched=1").fetchone()[0]
            note = f"{n} paid orders still not dispatched"
        elif r["type"] == "dispatched_not_billed":
            n = conn.execute("select count(*) from order_xref where dispatched_not_billed=1").fetchone()[0]
            note = f"{n} shipments still without invoice"
        conn.execute("update recommendations set outcome_note=?, outcome_checked_at=? where id=?", (f"{as_of}: {note}", _now(), int(r["id"])))
    conn.commit()
    return db.read(conn, "select id, status, title, outcome_note from recommendations where status in ('approved','done')")
