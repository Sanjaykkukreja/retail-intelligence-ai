"""Component 5 - Interface. The owner's screen.   Run:  streamlit run app/streamlit_app.py

NOTE: written in a workspace that could not run Streamlit. Every number comes from tested functions; only the
screen layout is untested. If a page shows an error, send the message back and it will be fixed.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
import streamlit as st

from retail_intel import db
from retail_intel.actions import check_outcomes, decide, list_recommendations, mark_done
from retail_intel.agent.models import make_model
from retail_intel.agent.orchestrator import Agent
from retail_intel.briefing import build_briefing
from retail_intel.config import DB_PATH
from retail_intel.findings import answer_question
from retail_intel.metrics import funnel_fulfilment as ff
from retail_intel.metrics import inventory, sales
from retail_intel.metrics.common import as_of, context, shift
from retail_intel.agent.tools import Toolbox
from retail_intel.platform.catalog import (active_agents, available_streams, load_client, load_streams, load_templates,
                                           readiness, validate)
from retail_intel.platform.dashboards import agent_dashboard, control_tower, fmt_value
from retail_intel.platform.registry import CONNECTORS, load_plugins

st.set_page_config(page_title="Retail Intelligence AI", layout="wide")

# Cloud hosting: keys pasted into Streamlit's "Secrets" box become environment variables for the agent.
try:
    for _k in ("ANTHROPIC_API_KEY", "ANTHROPIC_MODEL", "ANTHROPIC_ROUTER_MODEL", "AGENT_PROVIDER", "OPENAI_API_KEY", "OPENAI_MODEL",
               "CLIENT_CONFIG_TOML"):
        if _k in st.secrets and not os.environ.get(_k):
            os.environ[_k] = str(st.secrets[_k])
except Exception:  # no secrets configured (e.g. on a laptop) - fine
    pass

# First start in the cloud there is no terminal, so the app builds its own demo data (about a minute).
if not Path(DB_PATH).exists():
    from retail_intel.config import SOURCE_DIR, TRUTH_DIR
    from retail_intel.pipeline import run_pipeline
    from retail_intel.synth.generate import generate
    with st.spinner("First start: building the GlowBasket demo data. This takes about a minute..."):
        generate(SOURCE_DIR, TRUTH_DIR)
        _r = run_pipeline()
    if _r["status"] != "ok":
        st.error("The data check stopped the build:\n\n" + _r["blockers"].to_string(index=False))
        st.stop()

conn = db.connect(DB_PATH)
today = as_of(conn)


@st.cache_data(show_spinner="Building today's briefing...")
def cached_briefing(version: str) -> dict:
    c = db.connect(DB_PATH)
    try:
        return build_briefing(c)
    finally:
        c.close()


version = conn.execute("select max(run_at) from pipeline_runs").fetchone()[0] if db.table_exists(conn, "pipeline_runs") else "0"
load_plugins()
client = load_client()
templates = load_templates()
agents = active_agents(client, templates)
by_id = {a.id: a for a in agents}

PAGES = ["Control tower", "Ask an agent", "Role dashboards", "Agent catalog", "Recommendations", "Daily briefing",
         "Findings and questions", "Trends", "Stock and expiry", "Data health", "Agent activity"]
if "page" not in st.session_state:
    st.session_state.page = PAGES[0]
if st.session_state.get("dash_agent") not in by_id:
    st.session_state.dash_agent = agents[0].id
if st.session_state.get("ask_agent") not in by_id:
    st.session_state.ask_agent = client.default_agent if client.default_agent in by_id else agents[0].id


def go(page: str, agent_id: str | None = None, key: str = "dash_agent"):
    st.session_state.page = page
    if agent_id:
        st.session_state[key] = agent_id


st.sidebar.title(client.name)
st.sidebar.caption(f"Data through {today} | {len(agents)} agents active")
page = st.sidebar.radio("Go to", PAGES, key="page")


def show_kpi(col, k, help_text=None):
    col.metric(k["label"], k["text"], help=help_text or (k["error"] or None))


def rec_list(recs: pd.DataFrame, key_prefix: str):
    """Approve / reject / done buttons for a set of recommendations."""
    if recs.empty:
        st.write("Nothing here.")
    for _, r in recs.iterrows():
        impact = f" - about {fmt_value(r['est_impact_inr'], 'inr')}" if pd.notna(r["est_impact_inr"]) else ""
        owner = f" ({by_id[r['agent']].name})" if "agent" in r and r.get("agent") in by_id else ""
        with st.expander(f"#{r['id']} [{r['severity'].upper()}] {r['title']}{impact}{owner}"):
            st.write(f"**Suggested action:** {r['action']}")
            if r.get("decision_note"):
                st.write(f"**Your note:** {r['decision_note']}")
            if r.get("outcome_note"):
                st.info(f"Outcome: {r['outcome_note']}")
            note = st.text_input("Note (optional)", key=f"{key_prefix}n{r['id']}")
            c1, c2, c3 = st.columns(3)
            if r["status"] == "proposed" and c1.button("Approve", key=f"{key_prefix}a{r['id']}"):
                decide(conn, int(r["id"]), True, note); st.rerun()
            if r["status"] == "proposed" and c2.button("Reject", key=f"{key_prefix}r{r['id']}"):
                decide(conn, int(r["id"]), False, note); st.rerun()
            if r["status"] == "approved" and c3.button("Mark done", key=f"{key_prefix}d{r['id']}"):
                mark_done(conn, int(r["id"]), note); st.rerun()


if page == "Control tower":
    st.header("Control tower")
    st.caption("The whole business on one page: headline numbers, then each agent's north-star number and the decisions it is waiting on.")
    ct = control_tower(conn, agents)
    cols = st.columns(len(ct["kpis"]) + 1)
    for c, k in zip(cols, ct["kpis"]):
        show_kpi(c, k)
    cols[-1].metric("AI spend so far", f"${ct['ai_cost_usd']:.2f}")
    st.subheader("Agents")
    for i in range(0, len(agents), 2):
        row = st.columns(2)
        for col, (_, a) in zip(row, ct["agents"].iloc[i:i + 2].iterrows()):
            with col.container(border=True):
                st.markdown(f"**{a['agent']}**  \n{a['role']} | {a['autonomy']}")
                c1, c2 = st.columns(2)
                c1.metric(a["north_star"], a["north_star_value"])
                c2.metric("Decisions waiting", int(a["decisions_waiting"]),
                          help=f"Worth about {fmt_value(a['rupees_waiting'], 'inr')}")
                acc = "no decisions yet" if pd.isna(a["acceptance_pct"]) else f"{a['acceptance_pct']:.0f}% of its advice accepted"
                st.caption(f"{acc} | {int(a['questions'])} questions | AI ${a['ai_cost_usd']:.2f}")
                b1, b2 = st.columns(2)
                b1.button("Open dashboard", key=f"ct_d_{a['agent_id']}", on_click=go, args=("Role dashboards", a["agent_id"]))
                b2.button("Ask this agent", key=f"ct_a_{a['agent_id']}", on_click=go,
                          args=("Ask an agent", a["agent_id"], "ask_agent"))
    st.subheader("Decisions waiting, biggest first")
    w = ct["waiting"].assign(agent=lambda d: d["agent"].map(lambda x: by_id[x].name if x in by_id else "-"))
    st.dataframe(w[["id", "agent", "severity", "title", "est_impact_inr"]].rename(columns={"est_impact_inr": "impact_rs"}),
                 use_container_width=True, hide_index=True)

elif page == "Role dashboards":
    t = by_id[st.selectbox("Agent", list(by_id), key="dash_agent", format_func=lambda x: by_id[x].name)]
    d = agent_dashboard(conn, t, agents, Toolbox(DB_PATH))
    st.header(t.name)
    st.caption(f"{t.role} | {t.autonomy_label} | {t.cadence}")
    st.write(t.goal)
    top = st.columns(1 + len(d["guardrails"]))
    if d["north_star"]:
        show_kpi(top[0], d["north_star"], "North star: the number this agent is judged on")
    for c, k in zip(top[1:], d["guardrails"]):
        show_kpi(c, k, "Guardrail: must not get worse while the north star improves")
    if d["kpis"]:
        for c, k in zip(st.columns(len(d["kpis"])), d["kpis"]):
            show_kpi(c, k)
    with st.expander("What this agent decides and how it is judged"):
        st.markdown("**Decisions it supports**\n" + "\n".join(f"- {x}" for x in t.decisions))
        st.markdown(f"**How we prove it worked:** {t.outcome}")
    sc = d["scorecard"]
    st.subheader("Its recommendations")
    c = st.columns(5)
    c[0].metric("Waiting", sc["proposed"], help=f"Worth about {fmt_value(sc['rupees_waiting'], 'inr')}")
    c[1].metric("Approved", sc["approved"]); c[2].metric("Done", sc["done"]); c[3].metric("Rejected", sc["rejected"])
    c[4].metric("Advice accepted", "-" if sc["acceptance_pct"] is None else f"{sc['acceptance_pct']:.0f}%")
    rec_list(d["recommendations"][d["recommendations"]["status"].isin(["proposed", "approved"])], f"rd_{t.id}_")
    for tb in d["tables"]:
        st.subheader(tb["title"])
        if tb["error"]:
            st.error(tb["error"])
        else:
            st.dataframe(tb["rows"], use_container_width=True, hide_index=True)
    act = d["activity"]
    st.caption(f"{act['questions']} questions asked | AI ${act['cost_usd']:.2f}"
               + ("" if act["grounded_pct"] is None else f" | {act['grounded_pct']:.0f}% of answers fully traced to data"))
    st.button(f"Ask {t.name}", on_click=go, args=("Ask an agent", t.id, "ask_agent"))

elif page == "Agent catalog":
    st.header("Agent catalog")
    st.caption("Every role agent the platform offers. An agent runs only when it is built, switched on for this "
               "client, and every data stream it needs is connected.")
    have = available_streams(client)
    rows = []
    for t in templates.values():
        rd = readiness(t, client)
        rows.append(dict(agent=t.name, role=t.role, built=t.status, switched_on=rd["enabled"], runs=rd["ready"] and rd["enabled"],
                         why=rd["reason"], autonomy=t.autonomy_label, judged_on=t.north_star or "-",
                         problems="; ".join(validate(t)) or "-"))
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    st.subheader("Data contract: which data each agent needs")
    streams = load_streams()
    matrix = pd.DataFrame([{"stream": s.label, "connected": "yes" if k in have else "no",
                            **{t.name: ("needs" if k in t.streams else "") for t in templates.values()}}
                           for k, s in streams.items()])
    st.dataframe(matrix, use_container_width=True, hide_index=True)
    st.subheader("Connectors")
    st.dataframe(pd.DataFrame([dict(connector=c.label, status=c.status, in_use=name in client.connectors,
                                    streams=", ".join(c.streams), settings=c.settings_help)
                               for name, c in CONNECTORS.items()]), use_container_width=True, hide_index=True)

elif page == "Ask an agent":
    t = by_id[st.selectbox("Talk to", list(by_id), key="ask_agent", format_func=lambda x: f"{by_id[x].name} ({by_id[x].role})")]
    st.header(t.name)
    st.caption(t.goal)
    model = make_model("answer")
    if model is None:
        st.info("No ANTHROPIC_API_KEY found, so answers show the evidence the agent gathered instead of a written reply. "
                "See the build guide, section 4, to add your Claude API key.")
    if st.session_state.get("chat_agent") != t.id:                     # each agent keeps its own conversation
        st.session_state.chat, st.session_state.session_id, st.session_state.chat_agent = [], None, t.id
    for role, text in st.session_state.chat:
        with st.chat_message(role):
            st.markdown(text)
    q = st.chat_input(f"Ask the {t.name}...")
    if not st.session_state.chat and t.starter_questions:
        for c, sq in zip(st.columns(len(t.starter_questions)), t.starter_questions):
            if c.button(sq, key=f"sq_{t.id}_{sq}"):
                q = sq
    if q:
        st.session_state.chat.append(("user", q))
        with st.chat_message("user"):
            st.markdown(q)
        with st.chat_message("assistant"):
            with st.spinner("Checking the data..."):
                r = Agent.from_env(DB_PATH, template=t).ask(q, st.session_state.session_id)
            st.session_state.session_id = r.session_id
            st.markdown(r.answer)
            cost = f" | about ${r.cost_usd:.4f}" if r.cost_usd is not None else ""
            st.caption(f"{r.agent} | {r.intent} | {r.pattern} | tools: {', '.join(r.tools_used) or 'none'} | "
                       f"{r.usage.total_input_tokens + r.usage.output_tokens} tokens ({r.usage.cache_read_tokens} from cache){cost} | {r.latency_s}s")
            if r.grounded is False:
                st.warning("Some figures could not be traced to the data. Check them before acting.")
        st.session_state.chat.append(("assistant", r.answer))
    if st.session_state.chat and st.button("New conversation"):
        st.session_state.chat, st.session_state.session_id = [], None
        st.rerun()

elif page == "Daily briefing":
    st.markdown(cached_briefing(version)["markdown"])

elif page == "Recommendations":
    st.header("Recommendations")
    if st.button("Check outcomes of approved actions"):
        check_outcomes(conn)
    status = st.selectbox("Show", ["proposed", "approved", "done", "rejected", "expired", "all"])
    from retail_intel.platform.dashboards import recommendations_by_agent
    allr = recommendations_by_agent(conn, agents)
    full = list_recommendations(conn, None if status == "all" else status).merge(allr[["id", "agent"]], on="id", how="left")
    rec_list(full, "rp_")

elif page == "Findings and questions":
    st.header("What the system found")
    only_current = st.checkbox("Only current issues", value=True)
    f = db.read(conn, "select * from findings " + ("where is_current = 1 " if only_current else "") +
                "order by case severity when 'high' then 0 when 'medium' then 1 else 2 end, est_impact_inr desc")
    q = db.read(conn, "select * from questions").set_index("finding_id")
    for _, r in f.iterrows():
        with st.expander(f"[{r['severity'].upper()}] {r['title']}"):
            if pd.notna(r["est_impact_inr"]):
                st.write(f"**Estimated impact:** Rs {r['est_impact_inr']:,.0f}")
            st.write(f"**Suggested action:** {r['action']}")
            st.caption(f"How it was estimated: {r['calc']}")
            if r["id"] in q.index:
                row = q.loc[r["id"]]
                st.write(f"**Question for you:** {row['question']}")
                if row["status"] == "answered":
                    st.success(f"Your answer: {row['answer']}")
                else:
                    ans = st.text_input("Your answer", key=f"a_{r['id']}")
                    if st.button("Save answer", key=f"b_{r['id']}") and ans.strip():
                        answer_question(conn, r["id"], ans.strip()); st.rerun()

elif page == "Trends":
    st.header("Trends")
    days = st.selectbox("Period", [30, 90, 180, 365], index=1)
    start = shift(today, -days + 1)
    st.subheader("Net sales and gross margin per day (Rs, ex-GST)")
    st.line_chart(sales.daily(conn, start, today).set_index("date")[["net_revenue", "gross_margin"]])
    st.subheader("Website: checkout to purchase rate")
    st.line_chart(ff.daily_funnel(conn, start, today).set_index("date")[["checkout_to_purchase"]])
    st.subheader("Average hours from order to dispatch")
    st.line_chart(ff.daily_fulfilment(conn, start, today).set_index("date")[["avg_dispatch_hours"]])
    st.subheader("Category performance")
    st.dataframe(sales.by_category(conn, start, today).round(1), use_container_width=True)

elif page == "Stock and expiry":
    st.header("Stock and expiry")
    st.subheader("Out of stock now or recently")
    st.dataframe(inventory.stockout_events(conn, today, min_days=2, min_lost_units=5).head(15).round(1), use_container_width=True)
    st.subheader("Less than a week of cover")
    st.dataframe(inventory.low_stock(conn, today).round(1), use_container_width=True)
    st.subheader("Batches that will not sell before expiry")
    ex = inventory.expiry_exposure(conn, today)
    st.dataframe(ex.round(1), use_container_width=True)
    if len(ex):
        pick = st.selectbox("Test markdowns on", [f"{r['name']} / {r['batch_no']}" for _, r in ex.iterrows()])
        name, batch = pick.split(" / ")
        sku = ex[(ex["name"] == name) & (ex["batch_no"] == batch)]["master_sku_id"].iloc[0]
        st.dataframe(inventory.markdown_scenarios(conn, sku, batch, today), use_container_width=True)
        st.caption(f"Assumes demand rises {context(conn)['markdown_elasticity']}% per 1% discount. This is a guess, not measured.")
    st.subheader("Slow movers")
    st.dataframe(inventory.slow_movers(conn, today).round(1), use_container_width=True)

elif page == "Data health":
    st.header("Data health")
    st.dataframe(db.read(conn, "select check_name, severity, issue_count, detail from dq_issue order by severity"), use_container_width=True)
    st.subheader("SKU mappings waiting for a human decision")
    rq = db.read(conn, "select * from sku_map where status <> 'accepted'")
    if rq.empty:
        st.success("All product codes across website, billing and warehouse are matched.")
    else:
        st.dataframe(rq, use_container_width=True)
        st.caption("Approve by adding a line to data/sku_map_overrides.csv (system,source_code,master_sku_id), then re-run the pipeline.")
    st.subheader("How SKUs were matched")
    st.dataframe(db.read(conn, "select system, method, status, count(*) as n from sku_map group by 1,2,3"), use_container_width=True)

else:
    st.header("Agent activity")
    st.caption("Every question, which agent answered it, what it cost.")
    if not db.table_exists(conn, "agent_runs"):
        st.write("No questions asked yet.")
    else:
        _cols = [r[1] for r in conn.execute("pragma table_info(agent_runs)")]
        runs = db.read(conn, "select ts, " + ("agent, " if "agent" in _cols else "") + "question, intent, pattern, model, input_tokens, output_tokens, model_calls, cost_usd, "
                             "grounded, stopped, latency_s from agent_runs order by ts desc limit 200")
        c1, c2, c3 = st.columns(3)
        c1.metric("Questions", len(runs))
        c2.metric("Tokens", f"{int(runs['input_tokens'].sum() + runs['output_tokens'].sum()):,}")
        c3.metric("Answered without a model call", f"{100 * (runs['model_calls'] == 0).mean():.0f}%")
        st.dataframe(runs, use_container_width=True)
    if db.table_exists(conn, "tool_calls_log"):
        st.subheader("Tool calls (cache hits are free)")
        st.dataframe(db.read(conn, "select tool, count(*) calls, sum(cached) cache_hits, sum(1 - ok) errors, "
                                   "round(avg(ms)) avg_ms from tool_calls_log group by tool order by calls desc"),
                     use_container_width=True)
