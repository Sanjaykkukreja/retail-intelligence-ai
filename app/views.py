"""The seven pages. Shared objects (database connection, client, agents, history store) are set by the main app in S.

Overview   Control tower | Ask an agent
Work       Role dashboards | Decisions | Trends and stock
Admin      Agents and data | Usage and history
"""
from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import streamlit as st

from retail_intel import db
from retail_intel.actions import check_outcomes, decide, list_recommendations, mark_done
from retail_intel.agent.models import make_model
from retail_intel.agent.orchestrator import Agent
from retail_intel.agent.tools import Toolbox
from retail_intel.findings import answer_question
from retail_intel.metrics import funnel_fulfilment as ff
from retail_intel.metrics import inventory, sales
from retail_intel.metrics.common import context, shift
from retail_intel.platform.catalog import available_streams, load_streams, readiness, validate
from retail_intel.platform.dashboards import agent_dashboard, control_tower, fmt_value, recommendations_by_agent
from retail_intel.platform.definitions import RULES, change_rule, register, sign_off
from retail_intel.platform.questions import family_summary, load_families, promotions, question_log, request_promotion
from retail_intel.platform.registry import CONNECTORS

from style import CHART, card_title, page_header, pills, severity_label

S = SimpleNamespace()        # conn, today, client, templates, agents, by_id, persist, briefing, db_path, pages


# ------------------------------------------------------------------------------------------------ shared pieces
def go(page: str, **state):
    """Jump to another page, optionally choosing an agent there."""
    for k, v in state.items():
        st.session_state[k] = v
    st.switch_page(S.pages[page])


def kpi(col, k, help_text=None):
    col.metric(k["label"], k["text"], help=help_text or k.get("error") or None)


def agent_name(agent_id) -> str:
    return S.by_id[agent_id].name if agent_id in S.by_id else "-"


def rec_list(recs: pd.DataFrame, key_prefix: str, empty: str = "Nothing waiting. You are up to date."):
    """Recommendations with approve / reject / done."""
    if recs.empty:
        st.caption(empty)
    for _, r in recs.iterrows():
        impact = f" · about {fmt_value(r['est_impact_inr'], 'inr')}" if pd.notna(r["est_impact_inr"]) else ""
        owner = f" · {agent_name(r['agent'])}" if "agent" in r and r.get("agent") in S.by_id else ""
        with st.expander(f"{severity_label(r['severity'])}  {r['title']}{impact}{owner}"):
            st.markdown(f"**Suggested action:** {r['action']}")
            if r.get("decision_note"):
                st.markdown(f"**Your note:** {r['decision_note']}")
            if r.get("outcome_note"):
                st.info(f"Outcome: {r['outcome_note']}")
            note = st.text_input("Note (optional)", key=f"{key_prefix}n{r['id']}", placeholder="e.g. PO raised with supplier")
            c1, c2, c3, _ = st.columns([1, 1, 1, 3])
            if r["status"] == "proposed" and c1.button("Approve", key=f"{key_prefix}a{r['id']}", type="primary"):
                decide(S.conn, int(r["id"]), True, note); S.persist(); st.rerun()
            if r["status"] == "proposed" and c2.button("Reject", key=f"{key_prefix}r{r['id']}"):
                decide(S.conn, int(r["id"]), False, note); S.persist(); st.rerun()
            if r["status"] == "approved" and c3.button("Mark done", key=f"{key_prefix}d{r['id']}"):
                mark_done(S.conn, int(r["id"]), note); S.persist(); st.rerun()
            st.caption(f"#{r['id']} · {r['status']}")


def table(df: pd.DataFrame, **kw):
    st.dataframe(df, hide_index=True, **kw)          # tables stretch to the page width by default


# ------------------------------------------------------------------------------------------------ Overview
def page_control_tower():
    page_header("Control tower", f"Data through {S.today}. The whole business on one page: the headline numbers, "
                "each agent's north-star number, and the decisions waiting for you.", eyebrow=S.client.name)
    ct = control_tower(S.conn, S.agents)
    tab_overview, tab_brief = st.tabs(["Overview", "Today's briefing"])
    with tab_overview:
        cols = st.columns(len(ct["kpis"]))
        for c, k in zip(cols, ct["kpis"]):
            kpi(c, k)
        st.subheader("Agents")
        for i in range(0, len(S.agents), 2):
            row = st.columns(2, gap="medium")
            for col, (_, a) in zip(row, ct["agents"].iloc[i:i + 2].iterrows()):
                with col.container(border=True):
                    card_title(a["agent"], a["role"])
                    acc = "no decisions yet" if pd.isna(a["acceptance_pct"]) else f"{a['acceptance_pct']:.0f}% advice accepted"
                    waiting = int(a["decisions_waiting"])
                    pills((a["autonomy"], ""), (f"{waiting} waiting", "warn" if waiting else "ok"), (acc, ""))
                    c1, c2 = st.columns(2)
                    c1.metric(a["north_star"], a["north_star_value"])
                    c2.metric("Rupees waiting on you", fmt_value(a["rupees_waiting"], "inr"))
                    b1, b2, _ = st.columns([1, 1, 1])
                    if b1.button("Dashboard", key=f"ct_d_{a['agent_id']}"):
                        go("Role dashboards", dash_agent=a["agent_id"])
                    if b2.button("Ask", key=f"ct_a_{a['agent_id']}", type="primary"):
                        go("Ask an agent", ask_agent=a["agent_id"])
        st.subheader("Decisions waiting, biggest first")
        w = ct["waiting"].assign(agent=lambda d: d["agent"].map(agent_name))
        if w.empty:
            st.caption("Nothing waiting.")
        else:
            table(w[["id", "agent", "severity", "title", "est_impact_inr"]].rename(
                columns={"est_impact_inr": "impact (Rs)", "id": "#"}))
            if st.button("Review and decide"):
                go("Decisions")
        st.caption(f"AI spend so far: ${ct['ai_cost_usd']:.2f}. Dashboards and the daily refresh cost nothing; only questions to an agent call the AI.")
    with tab_brief:
        st.markdown(S.briefing()["markdown"])


def page_ask():
    by_id = S.by_id
    t = by_id[st.session_state.ask_agent]
    if st.session_state.get("chat_agent") != t.id:                     # each agent keeps its own conversation
        st.session_state.chat, st.session_state.session_id, st.session_state.chat_agent = [], None, t.id
    left, mid, right = st.columns([1, 4, 1])
    with mid:
        st.selectbox("Talk to", list(by_id), key="ask_agent", label_visibility="collapsed",
                     format_func=lambda x: f"{by_id[x].name} · {by_id[x].role}")
        if make_model("answer") is None:
            st.info("No Claude API key found, so answers show the evidence the agent gathered instead of a written reply.")
        q = None
        if not st.session_state.chat:
            st.markdown(f'<div class="ri-chat-empty"><div class="ri-eyebrow">{t.role}</div>'
                        f'<div class="ri-hero">What would you like to know?</div>'
                        f'<div class="ri-sub" style="margin:0 auto">{t.goal}</div></div>', unsafe_allow_html=True)
            for sq in t.starter_questions:
                if st.button(sq, key=f"sq_{t.id}_{sq}", width="stretch"):
                    q = sq
        for role, text in st.session_state.chat:
            with st.chat_message(role, avatar=":material/person:" if role == "user" else ":material/insights:"):
                st.markdown(text)
        typed = st.chat_input(f"Ask the {t.name}...")
        q = typed or q
        if q:
            st.session_state.chat.append(("user", q))
            with st.chat_message("user", avatar=":material/person:"):
                st.markdown(q)
            with st.chat_message("assistant", avatar=":material/insights:"):
                with st.spinner("Checking the data..."):
                    r = Agent.from_env(S.db_path, template=t).ask(q, st.session_state.session_id)
                st.session_state.session_id = r.session_id
                S.persist()
                st.markdown(r.answer)
                cost = f" · ${r.cost_usd:.3f}" if r.cost_usd is not None else ""
                tag = "Exploratory" if r.exploratory else "Certified tools"
                st.caption(f"{tag} · {len(r.tools_used)} checks{cost} · {r.latency_s}s")
                with st.popover("How this was answered"):
                    st.markdown(f"**Agent:** {t.name}  \n**Question type:** {r.intent} ({r.pattern})  \n"
                                f"**Checks run:** {', '.join(r.tools_used) or 'none'}  \n"
                                f"**Tokens:** {r.usage.total_input_tokens + r.usage.output_tokens:,} "
                                f"({r.usage.cache_read_tokens:,} from cache)")
                if r.grounded is False:
                    st.warning("Some figures could not be traced to the data. Check them before acting.")
            st.session_state.chat.append(("assistant", r.answer))
        if st.session_state.chat and st.button("New conversation", icon=":material/add:"):
            st.session_state.chat, st.session_state.session_id = [], None
            st.rerun()


# ------------------------------------------------------------------------------------------------ Work
def page_roles():
    by_id = S.by_id
    t = by_id[st.selectbox("Agent", list(by_id), key="dash_agent", format_func=lambda x: by_id[x].name)]
    d = agent_dashboard(S.conn, t, S.agents, Toolbox(S.db_path))
    page_header(t.name, t.goal, eyebrow=t.role)
    pills((t.autonomy_label, ""), (t.cadence, ""))
    top = st.columns(1 + len(d["guardrails"]))
    if d["north_star"]:
        kpi(top[0], d["north_star"], "North star: the number this agent is judged on")
    for c, k in zip(top[1:], d["guardrails"]):
        kpi(c, k, "Guardrail: must not get worse while the north star improves")
    if d["kpis"]:
        for c, k in zip(st.columns(len(d["kpis"])), d["kpis"]):
            kpi(c, k)
    tab_dec, tab_data, tab_about = st.tabs(["Decisions", "Details", "How it is judged"])
    with tab_dec:
        sc = d["scorecard"]
        c = st.columns(4)
        c[0].metric("Waiting", sc["proposed"], help=f"Worth about {fmt_value(sc['rupees_waiting'], 'inr')}")
        c[1].metric("Approved", sc["approved"])
        c[2].metric("Done", sc["done"])
        c[3].metric("Advice accepted", "-" if sc["acceptance_pct"] is None else f"{sc['acceptance_pct']:.0f}%")
        rec_list(d["recommendations"][d["recommendations"]["status"].isin(["proposed", "approved"])], f"rd_{t.id}_")
    with tab_data:
        for tb in d["tables"]:
            st.markdown(f"**{tb['title']}**")
            if tb["error"]:
                st.error(tb["error"])
            else:
                table(tb["rows"])
    with tab_about:
        st.markdown("**Decisions it supports**\n" + "\n".join(f"- {x}" for x in t.decisions))
        st.markdown(f"**How we prove it worked:** {t.outcome}")
        act = d["activity"]
        st.caption(f"{act['questions']} questions asked · AI ${act['cost_usd']:.2f}"
                   + ("" if act["grounded_pct"] is None else f" · {act['grounded_pct']:.0f}% of answers fully traced to data"))
    if st.button(f"Ask the {t.name}", type="primary", icon=":material/forum:"):
        go("Ask an agent", ask_agent=t.id)


def page_decisions():
    page_header("Decisions", "What the agents recommend, what you decided, and what came of it. Findings explain "
                "each issue in rupees and ask you what only you can know.")
    allr = recommendations_by_agent(S.conn, S.agents)
    tab_wait, tab_all, tab_find = st.tabs(["Waiting for you", "All decisions", "Findings and questions"])
    with tab_wait:
        rec_list(list_recommendations(S.conn, "proposed").merge(allr[["id", "agent"]], on="id", how="left"), "dw_")
    with tab_all:
        c1, c2 = st.columns([2, 1])
        status = c1.selectbox("Show", ["approved", "done", "rejected", "expired", "all"])
        if c2.button("Check outcomes now", icon=":material/refresh:"):
            check_outcomes(S.conn); S.persist()
        rec_list(list_recommendations(S.conn, None if status == "all" else status)
                 .merge(allr[["id", "agent"]], on="id", how="left"), "da_", empty="Nothing here.")
    with tab_find:
        only_current = st.toggle("Only current issues", value=True)
        f = db.read(S.conn, "select * from findings " + ("where is_current = 1 " if only_current else "") +
                    "order by case severity when 'high' then 0 when 'medium' then 1 else 2 end, est_impact_inr desc")
        q = db.read(S.conn, "select * from questions").set_index("finding_id")
        for _, r in f.iterrows():
            impact = f" · {fmt_value(r['est_impact_inr'], 'inr')}" if pd.notna(r["est_impact_inr"]) else ""
            with st.expander(f"{severity_label(r['severity'])}  {r['title']}{impact}"):
                st.markdown(f"**Suggested action:** {r['action']}")
                st.caption(f"How it was estimated: {r['calc']}")
                if r["id"] in q.index:
                    row = q.loc[r["id"]]
                    st.markdown(f"**Question for you:** {row['question']}")
                    if row["status"] == "answered":
                        st.success(f"Your answer: {row['answer']}")
                    else:
                        ans = st.text_input("Your answer", key=f"a_{r['id']}")
                        if st.button("Save answer", key=f"b_{r['id']}") and ans.strip():
                            answer_question(S.conn, r["id"], ans.strip()); S.persist(); st.rerun()


def page_trends():
    page_header("Trends and stock", "How sales, the website and fulfilment are moving, and where stock needs attention.")
    tab_sales, tab_stock = st.tabs(["Sales and website", "Stock and expiry"])
    with tab_sales:
        days = st.segmented_control("Period", [30, 90, 180, 365], default=90, format_func=lambda d: f"{d} days") or 90
        start = shift(S.today, -days + 1)
        with st.container(border=True):
            card_title("Net sales and gross margin per day", "Rupees, ex-GST")
            st.line_chart(sales.daily(S.conn, start, S.today).set_index("date")[["net_revenue", "gross_margin"]],
                          color=CHART[:2], height=260)
        c1, c2 = st.columns(2, gap="medium")
        with c1.container(border=True):
            card_title("Checkout to purchase", "Share of checkouts that became orders")
            st.line_chart(ff.daily_funnel(S.conn, start, S.today).set_index("date")[["checkout_to_purchase"]],
                          color=CHART[0], height=200)
        with c2.container(border=True):
            card_title("Hours from order to dispatch", "Daily average")
            st.line_chart(ff.daily_fulfilment(S.conn, start, S.today).set_index("date")[["avg_dispatch_hours"]],
                          color=CHART[2], height=200)
        st.markdown("**Category performance**")
        table(sales.by_category(S.conn, start, S.today).round(1))
    with tab_stock:
        st.markdown("**Out of stock now or recently**")
        table(inventory.stockout_events(S.conn, S.today, min_days=2, min_lost_units=5).head(15).round(1))
        st.markdown("**Less than a week of cover**")
        table(inventory.low_stock(S.conn, S.today).round(1))
        st.markdown("**Batches that will not sell before expiry**")
        ex = inventory.expiry_exposure(S.conn, S.today)
        table(ex.round(1))
        if len(ex):
            pick = st.selectbox("Test markdowns on", [f"{r['name']} / {r['batch_no']}" for _, r in ex.iterrows()])
            name, batch = pick.split(" / ")
            sku = ex[(ex["name"] == name) & (ex["batch_no"] == batch)]["master_sku_id"].iloc[0]
            table(inventory.markdown_scenarios(S.conn, sku, batch, S.today))
            st.caption(f"Assumes demand rises {context(S.conn)['markdown_elasticity']}% per 1% discount. An assumption, not measured.")
        st.markdown("**Slow movers**")
        table(inventory.slow_movers(S.conn, S.today).round(1))


# ------------------------------------------------------------------------------------------------ Admin
def page_setup():
    page_header("Agents and data", "Which agents run and what data they need, the rules behind every number, "
                "and whether the data can be trusted.")
    tab_agents, tab_defs, tab_health = st.tabs(["Agent catalog", "Definitions", "Data health"])
    with tab_agents:
        have = available_streams(S.client)
        rows = []
        for t in S.templates.values():
            rd = readiness(t, S.client)
            rows.append(dict(agent=t.name, role=t.role, status="running" if rd["ready"] and rd["enabled"] else
                             ("switched off" if rd["ready"] else rd["reason"]), autonomy=t.autonomy_label,
                             judged_on=t.north_star or "-", problems="; ".join(validate(t)) or "-"))
        table(pd.DataFrame(rows))
        st.markdown("**Data contract: which data each agent needs**")
        streams = load_streams()
        table(pd.DataFrame([{"data": s.label, "connected": "yes" if k in have else "no",
                             **{t.name: ("needs" if k in t.streams else "") for t in S.templates.values()}}
                            for k, s in streams.items()]))
        st.markdown("**Connectors**")
        table(pd.DataFrame([dict(connector=c.label, status=c.status, in_use=name in S.client.connectors,
                                 provides=", ".join(c.streams)) for name, c in CONNECTORS.items()]))
    with tab_defs:
        st.caption("Rules are yours to set; definitions are built into the tested code and change only with a test. "
                   "Sign each one off so everyone knows who agreed it.")
        reg = register(S.conn)
        table(reg[["kind", "name", "value", "meaning", "signed_off"]])
        c1, c2 = st.columns(2, gap="medium")
        with c1.container(border=True):
            card_title("Sign off")
            who = st.text_input("Your name", key="so_who")
            pick = st.selectbox("Rule or definition", list(reg["name"]), key="so_pick")
            if st.button("Sign off", type="primary"):
                try:
                    sign_off(S.conn, pick, who); S.persist(); st.success(f"Signed off: {pick}"); st.rerun()
                except ValueError as e:
                    st.error(str(e))
        with c2.container(border=True):
            card_title("Change a rule", "Logged with your name")
            key = st.selectbox("Rule", list(RULES), format_func=lambda k: RULES[k][0], key="cr_key")
            val = st.number_input(f"New value ({RULES[key][1]})", value=float(context(S.conn)[key]), key="cr_val")
            if st.button("Save rule"):
                if not st.session_state.get("so_who", "").strip():
                    st.error("Enter your name in the sign-off box first.")
                else:
                    change_rule(S.conn, key, val, st.session_state["so_who"]); S.persist()
                    st.success("Saved. Dashboards use it now; findings and order checks from the next daily refresh.")
        if db.table_exists(S.conn, "rule_changes"):
            ch = db.read(S.conn, "select * from rule_changes order by changed_at desc")
            if len(ch):
                st.markdown("**Rule change history**")
                table(ch)
    with tab_health:
        table(db.read(S.conn, "select check_name, severity, issue_count, detail from dq_issue order by severity"))
        rq = db.read(S.conn, "select * from sku_map where status <> 'accepted'")
        if rq.empty:
            st.success("All product codes across website, billing and warehouse are matched.")
        else:
            st.markdown("**Product matches waiting for a human decision**")
            table(rq)
        st.markdown("**How products were matched**")
        table(db.read(S.conn, "select system, method, status, count(*) as n from sku_map group by 1,2,3"))


def page_usage():
    page_header("Usage and history", "Every conversation, what people ask most, and what the AI costs.")
    tab_conv, tab_q, tab_spend = st.tabs(["Conversations", "Questions asked", "Spend"])
    with tab_conv:
        _conversations()
    with tab_q:
        _questions()
    with tab_spend:
        _spend()


def _conversations():
    conn = S.conn
    if not db.table_exists(conn, "agent_messages"):
        st.caption("No conversations yet.")
        return
    cols = [r[1] for r in conn.execute("pragma table_info(agent_runs)")] if db.table_exists(conn, "agent_runs") else []
    sessions = db.read(conn, """select m.session_id, min(m.ts) as started, max(m.ts) as last, count(*) / 2 as questions,
                                 (select content from agent_messages f where f.session_id = m.session_id and f.role = 'user'
                                  order by f.rowid limit 1) as first_question
                                from agent_messages m group by m.session_id order by last desc limit 100""")
    if "agent" in cols:
        ag = db.read(conn, "select session_id, max(agent) as agent, sum(cost_usd) as cost_usd from agent_runs group by session_id")
        sessions = sessions.merge(ag, on="session_id", how="left")
    else:
        sessions["agent"], sessions["cost_usd"] = "analyst", None
    for _, sr in sessions.iterrows():
        who = agent_name(sr["agent"]) if sr["agent"] in S.by_id else "Analyst"
        cost = "" if pd.isna(sr["cost_usd"]) else f" · ${sr['cost_usd']:.3f}"
        with st.expander(f"{sr['first_question'][:80]}  ·  {who} · {sr['last'][:10]}{cost}"):
            for role, text in conn.execute("select role, content from agent_messages where session_id=? order by rowid",
                                           (sr["session_id"],)).fetchall():
                with st.chat_message(role, avatar=":material/person:" if role == "user" else ":material/insights:"):
                    st.markdown(text)
            if st.button("Continue this conversation", key=f"res_{sr['session_id']}", icon=":material/forum:"):
                msgs = conn.execute("select role, content from agent_messages where session_id=? order by rowid",
                                    (sr["session_id"],)).fetchall()
                st.session_state.chat = [(m[0], m[1]) for m in msgs]
                st.session_state.session_id = sr["session_id"]
                agent = sr["agent"] if sr["agent"] in S.by_id else st.session_state.ask_agent
                st.session_state.chat_agent = agent
                go("Ask an agent", ask_agent=agent)


def _questions():
    fams = load_families()
    log = question_log(S.conn, fams)
    summ = family_summary(log, fams)
    c = st.columns(4)
    c[0].metric("Questions asked", len(log))
    c[1].metric("Answered by certified tools", f"{100 * (1 - log['exploratory'].mean()):.0f}%" if len(log) else "-")
    c[2].metric("Question families", len(fams))
    c[3].metric("Waiting for client data", int(sum(f["status"] == "needs_data" for f in fams.values())))
    st.markdown("**By family**")
    table(summ[["label", "role", "status", "asked", "last_asked", "sample"]])
    st.markdown("**Long tail: exploratory or no family**")
    tail = log[log["exploratory"]] if len(log) else log
    if tail.empty:
        st.caption("Every question so far was answered by certified tools.")
    for i, (_, tr) in enumerate(tail.head(30).iterrows()):
        with st.expander(f"{tr['question'][:90]}  ·  {tr['ts'][:10]}"):
            fam = st.selectbox("Closest family", ["(new family)"] + list(fams), key=f"pf_{i}")
            note = st.text_input("What should the certified version do?", key=f"pn_{i}")
            if st.button("Ask for this to be certified", key=f"pb_{i}"):
                request_promotion(S.conn, tr["question"], "" if fam == "(new family)" else fam, note, "owner")
                S.persist(); st.success("Added to the list for the next build.")
    st.markdown("**Waiting for data: what to ask the client for**")
    table(pd.DataFrame([dict(family=f["label"], role=f["role"], needs=", ".join(f.get("needs", [])),
                             example=f["examples"][0]) for f in fams.values() if f["status"] == "needs_data"]))
    pr = promotions(S.conn)
    if len(pr):
        st.markdown("**Requested for certification**")
        table(pr)


def _spend():
    conn = S.conn
    if not db.table_exists(conn, "agent_runs"):
        st.caption("No questions asked yet.")
        return
    cols = [r[1] for r in conn.execute("pragma table_info(agent_runs)")]
    runs = db.read(conn, "select ts, " + ("agent, " if "agent" in cols else "'analyst' as agent, ")
                   + "question, intent, model, input_tokens, output_tokens, model_calls, cost_usd, latency_s "
                   "from agent_runs order by ts desc")
    runs["cost_usd"] = runs["cost_usd"].fillna(0)
    asked = runs[runs["model_calls"] > 0]
    c = st.columns(4)
    c[0].metric("Total AI spend", f"${runs['cost_usd'].sum():.2f}")
    c[1].metric("Questions", len(runs))
    c[2].metric("Average per AI answer", f"${asked['cost_usd'].mean():.3f}" if len(asked) else "-")
    c[3].metric("Answered without AI", f"{100 * (runs['model_calls'] == 0).mean():.0f}%")
    with st.container(border=True):
        card_title("Spend per day", "US dollars")
        st.bar_chart(runs.assign(day=runs["ts"].str[:10]).groupby("day")["cost_usd"].sum(), color=CHART[0], height=220)
    st.markdown("**Per agent**")
    table(runs.assign(agent=runs["agent"].map(lambda a: agent_name(a) if a in S.by_id else a))
          .groupby("agent").agg(questions=("question", "size"), cost_usd=("cost_usd", "sum"),
                                avg_seconds=("latency_s", "mean")).round(3).reset_index())
    with st.expander("Every question"):
        table(runs)
