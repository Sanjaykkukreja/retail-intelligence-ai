"""Data for the role dashboards and the company control tower. No screen code here, so any front end
(Streamlit today, a web app later) can draw the same thing and tests can check it.

Every agent is judged the same way:
  north star   the one KPI it is responsible for moving
  guardrails   KPIs it must not damage on the way
  scorecard    what happened to its recommendations: proposed -> decided -> done -> outcome checked
"""
from __future__ import annotations

import pandas as pd

from .. import db
from ..actions import ensure as _ensure_recs
from ..metrics.common import as_of as _as_of
from .catalog import AgentTemplate, date_values, fill_args, owner_agent
from .registry import KPIS, load_plugins


def fmt_value(v, unit: str) -> str:
    if v is None:
        return "not known"
    if unit == "inr":
        a = abs(v)
        s = f"Rs {a / 1e7:.2f} Cr" if a >= 1e7 else f"Rs {a / 1e5:.2f} L" if a >= 1e5 else f"Rs {a:,.0f}"
        return ("-" if v < 0 else "") + s
    if unit == "pct":
        return f"{v:.1f}%"
    if unit == "hours":
        return f"{v:.1f} h"
    if unit == "usd":
        return f"${v:.2f}"
    return f"{v:,.0f}"


def kpi_values(conn, names: list[str]) -> list[dict]:
    load_plugins()
    out = []
    for n in names:
        k = KPIS.get(n)
        try:
            v, err = k.value(conn), None
        except Exception as e:                      # one broken KPI must not take the dashboard down
            v, err = None, f"{type(e).__name__}: {e}"
        out.append(dict(name=n, label=k.label, unit=k.unit, better=k.better, value=v, text=fmt_value(v, k.unit), error=err))
    return out


def recommendations_by_agent(conn, agents: list[AgentTemplate]) -> pd.DataFrame:
    _ensure_recs(conn)
    recs = db.read(conn, "select id, status, type, severity, title, action, est_impact_inr, decided_at, outcome_note "
                         "from recommendations")
    recs["agent"] = recs["type"].map(lambda t: owner_agent(t, agents))
    return recs


def scorecard(recs: pd.DataFrame) -> dict:
    """The outcome loop for one agent's recommendations."""
    n = lambda s: int((recs["status"] == s).sum())
    decided = n("approved") + n("done") + n("rejected")
    return dict(proposed=n("proposed"), approved=n("approved"), done=n("done"), rejected=n("rejected"), expired=n("expired"),
                acceptance_pct=None if not decided else round(100 * (n("approved") + n("done")) / decided, 1),
                rupees_waiting=float(recs.loc[recs["status"] == "proposed", "est_impact_inr"].fillna(0).sum()),
                outcomes_checked=int(recs["outcome_note"].notna().sum()))


def agent_activity(conn, agent_id: str) -> dict:
    if not db.table_exists(conn, "agent_runs"):
        return dict(questions=0, cost_usd=0.0, grounded_pct=None)
    cols = [r[1] for r in conn.execute("pragma table_info(agent_runs)")]
    if "agent" not in cols:
        return dict(questions=0, cost_usd=0.0, grounded_pct=None)
    r = conn.execute("""select count(*), coalesce(sum(cost_usd),0), avg(grounded) from agent_runs where agent = ?""",
                     (agent_id,)).fetchone()
    return dict(questions=r[0], cost_usd=round(r[1], 4), grounded_pct=None if r[2] is None else round(100 * r[2], 1))


def agent_dashboard(conn, t: AgentTemplate, agents: list[AgentTemplate], box=None) -> dict:
    """Everything one role's page shows: north star, guardrails, other KPIs, tables, its decisions and its scorecard."""
    names = list(dict.fromkeys([t.north_star] + t.guardrails + t.kpis)) if t.north_star else t.kpis
    vals = {k["name"]: k for k in kpi_values(conn, [n for n in names if n])}
    tables = []
    if box is not None:
        values = date_values(_as_of(conn))
        for tb in t.dashboard.get("tables", []):
            args = fill_args(tb.get("args", {}), values) or {}
            res = box.call_ex(tb["tool"], args)
            rows = res.result.get("rows") if res.ok and isinstance(res.result, dict) else None
            if rows is None and res.ok and isinstance(res.result, dict) and "gaps" in res.result:
                rows = res.result["gaps"]
            tables.append(dict(title=tb["title"], rows=pd.DataFrame(rows or []), error=None if res.ok else res.result))
    recs = recommendations_by_agent(conn, agents)
    mine = recs[recs["agent"] == t.id]
    return dict(agent=t, north_star=vals.get(t.north_star), guardrails=[vals[g] for g in t.guardrails if g in vals],
                kpis=[vals[k] for k in t.kpis if k in vals and k != t.north_star and k not in t.guardrails],
                tables=tables, recommendations=mine.sort_values("est_impact_inr", ascending=False),
                scorecard=scorecard(mine), activity=agent_activity(conn, t.id))


def control_tower(conn, agents: list[AgentTemplate], company_kpis: list[str] | None = None) -> dict:
    """Company-wide view: headline KPIs, then one row per active agent with its north star and outcome loop."""
    company_kpis = company_kpis or ["net_revenue_28d", "gross_margin_pct_28d", "open_decisions", "rupees_at_stake"]
    recs = recommendations_by_agent(conn, agents)
    rows = []
    for t in agents:
        ns = kpi_values(conn, [t.north_star])[0] if t.north_star else None
        sc = scorecard(recs[recs["agent"] == t.id])
        act = agent_activity(conn, t.id)
        rows.append(dict(agent_id=t.id, agent=t.name, role=t.role, autonomy=t.autonomy_label,
                         north_star=ns["label"] if ns else "", north_star_value=ns["text"] if ns else "",
                         decisions_waiting=sc["proposed"], rupees_waiting=sc["rupees_waiting"],
                         acceptance_pct=sc["acceptance_pct"], done=sc["done"], questions=act["questions"],
                         ai_cost_usd=act["cost_usd"]))
    ai_total = 0.0
    if db.table_exists(conn, "agent_runs"):
        ai_total = conn.execute("select coalesce(sum(cost_usd),0) from agent_runs").fetchone()[0]
    return dict(kpis=kpi_values(conn, company_kpis), agents=pd.DataFrame(rows), ai_cost_usd=round(ai_total, 4),
                waiting=recs[recs["status"] == "proposed"].sort_values("est_impact_inr", ascending=False))
