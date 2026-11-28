"""Company-wide KPIs for the control tower."""
from __future__ import annotations

from .. import db
from ..actions import ensure as _ensure_recs
from ..platform.registry import kpi


@kpi("open_decisions", "Decisions waiting for you", "count", "down")
def open_decisions(conn):
    _ensure_recs(conn)
    return conn.execute("select count(*) from recommendations where status='proposed'").fetchone()[0]


@kpi("rupees_at_stake", "Rupee impact of decisions waiting", "inr", "down")
def rupees_at_stake(conn):
    """Sum of estimated impact on recommendations still waiting for a decision."""
    _ensure_recs(conn)
    return conn.execute("select coalesce(sum(est_impact_inr),0) from recommendations where status='proposed'").fetchone()[0]


@kpi("high_findings", "High-severity issues open", "count", "down")
def high_findings(conn):
    if not db.table_exists(conn, "findings"):
        return 0
    return conn.execute("select count(*) from findings where is_current=1 and severity='high'").fetchone()[0]
