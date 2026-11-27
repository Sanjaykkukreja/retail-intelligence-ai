"""The Daily Briefing: built from metrics and findings with no AI involved, so it is cheap, fast and reproducible.
The LLM agent can later rewrite or answer follow-ups, but the numbers always come from here."""
from __future__ import annotations

from datetime import date

import pandas as pd

from . import db
from .findings import SEVERITY_RANK, run_all_findings
from .metrics import sales
from .metrics.common import as_of as _as_of, context


def inr(x: float | None) -> str:
    if x is None:
        return "n/a"
    x = float(x)
    sign = "-" if x < 0 else ""
    x = abs(x)
    if x >= 1e7:
        return f"{sign}Rs {x / 1e7:.2f} Cr"
    if x >= 1e5:
        return f"{sign}Rs {x / 1e5:.2f} L"
    return f"{sign}Rs {x:,.0f}"


def _arrow(p: float | None) -> str:
    return "n/a" if p is None else f"{p:+.1f}%"


def build_briefing(conn, day: str | None = None, max_actions: int = 5, max_questions: int = 5) -> dict:
    day = day or _as_of(conn)
    c = context(conn)
    cmp_ = sales.day_vs_baseline(conn, day)
    t = cmp_["today"]
    findings = run_all_findings(conn, as_of=day, leakage_days=30)
    current = [f for f in findings if f["is_current"] and f["type"] not in ("web_tracking_gap",)]
    current.sort(key=lambda f: (SEVERITY_RANK[f["severity"]], -(f["est_impact_inr"] or 0)))
    actions = current[:max_actions]
    qs = db.read(conn, "select finding_id, question from questions where status = 'open'") \
        if db.table_exists(conn, "questions") else pd.DataFrame(columns=["finding_id", "question"])
    cur_ids = {f["id"] for f in current}
    qs = qs[qs["finding_id"].isin(cur_ids)].head(max_questions)
    dq = db.read(conn, "select check_name, severity, issue_count, detail from dq_issue where severity = 'warn' and issue_count > 0")
    top = sales.by_sku(conn, day, day).head(5)

    d = date.fromisoformat(day)
    lines = [f"# GlowBasket Daily Briefing - {d.strftime('%a %d %b %Y')}", ""]
    lines += [f"## Numbers for {day}", ""]
    lines += [
        f"- **Net sales:** {inr(t['net_revenue'])}  ({_arrow(cmp_['net_revenue_vs_7d_avg_pct'])} vs 7-day average, "
        f"{_arrow(cmp_['net_revenue_vs_same_weekday_pct'])} vs same weekday, {_arrow(cmp_['net_revenue_vs_prev_day_pct'])} vs previous day)",
        f"- **Orders:** {t['orders']:.0f}   **Average order value:** {inr(t['aov'])}",
        f"- **Gross margin:** {t['gross_margin_pct']:.1f}%  (target {c['gross_margin_target_pct']:.0f}%)   **Discount given:** {t['discount_pct']:.1f}% of list price",
        f"- **Cancelled orders:** {t['cancelled_orders']:.0f}   **Returned-to-origin:** {t['rto_orders']:.0f}",
        "",
    ]
    if len(top):
        lines += ["Top sellers: " + "; ".join(f"{r['name']} ({inr(r['net_revenue'])})" for _, r in top.iterrows()), ""]
    lines += [f"## What needs your attention ({len(actions)})", ""]
    if not actions:
        lines.append("Nothing urgent today.")
    for i, f in enumerate(actions, 1):
        imp = f" - about {inr(f['est_impact_inr'])} at stake (estimate)" if f["est_impact_inr"] else ""
        lines += [f"{i}. **[{f['severity'].upper()}] {f['title']}**{imp}", f"   - Do: {f['action']}", f"   - How we estimated it: {f['calc']}"]
    lines.append("")
    lines += ["## Questions for you", ""]
    if qs.empty:
        lines.append("No open questions.")
    for _, q in qs.iterrows():
        lines.append(f"- {q['question']}  _(ref {q['finding_id']})_")
    recs = db.read(conn, "select id, title from recommendations where status = 'proposed' "
                         "order by case severity when 'high' then 0 else 1 end, est_impact_inr desc") \
        if db.table_exists(conn, "recommendations") else pd.DataFrame(columns=["id", "title"])
    lines += ["", f"## Decisions waiting for you ({len(recs)})", ""]
    if recs.empty:
        lines.append("None.")
    for _, r in recs.head(8).iterrows():
        lines.append(f"- #{r['id']} {r['title']}")
    if len(recs):
        lines.append("\nApprove with `python -m retail_intel approve <#> \"note\"` or on the Recommendations screen.")
    lines += ["", "## Data health", ""]
    if dq.empty:
        lines.append("All checks passed.")
    for _, r in dq.iterrows():
        lines.append(f"- {r['check_name'].replace('_', ' ')}: {r['issue_count']} ({r['detail']})")
    lines += ["", f"_Data through {day}. Revenue is net of discounts, refunds and GST; cancelled and returned-to-origin orders excluded. "
                  "Estimates are marked; recommendations need your approval._"]
    return dict(day=day, markdown="\n".join(lines), actions=actions, questions=qs.to_dict("records"), summary=cmp_, data_health=dq.to_dict("records"))
