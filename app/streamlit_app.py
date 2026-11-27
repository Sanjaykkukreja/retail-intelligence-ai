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

st.set_page_config(page_title="Retail Intelligence AI", layout="wide")

# Cloud hosting: keys pasted into Streamlit's "Secrets" box become environment variables for the agent.
try:
    for _k in ("ANTHROPIC_API_KEY", "ANTHROPIC_MODEL", "ANTHROPIC_ROUTER_MODEL", "AGENT_PROVIDER", "OPENAI_API_KEY", "OPENAI_MODEL"):
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
st.sidebar.title("GlowBasket")
st.sidebar.caption(f"Data through {today}")
page = st.sidebar.radio("Go to", ["Ask the analyst", "Daily briefing", "Recommendations", "Findings and questions",
                                  "Trends", "Stock and expiry", "Data health", "Agent activity"])

if page == "Ask the analyst":
    st.header("Ask the analyst")
    model = make_model("answer")
    if model is None:
        st.info("No ANTHROPIC_API_KEY found, so answers show the evidence the analyst gathered instead of a written reply. "
                "See the build guide, section 4, to add your Claude API key.")
    if "chat" not in st.session_state:
        st.session_state.chat, st.session_state.session_id = [], None
    for role, text in st.session_state.chat:
        with st.chat_message(role):
            st.markdown(text)
    q = st.chat_input("e.g. Why did cancellations jump in mid-September?")
    if q:
        st.session_state.chat.append(("user", q))
        with st.chat_message("user"):
            st.markdown(q)
        with st.chat_message("assistant"):
            with st.spinner("Checking the data..."):
                r = Agent.from_env(DB_PATH).ask(q, st.session_state.session_id)
            st.session_state.session_id = r.session_id
            st.markdown(r.answer)
            cost = f" | about ${r.cost_usd:.4f}" if r.cost_usd is not None else ""
            st.caption(f"{r.intent} | {r.pattern} | tools: {', '.join(r.tools_used) or 'none'} | "
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
    recs = list_recommendations(conn, None if status == "all" else status)
    if recs.empty:
        st.write("Nothing here.")
    for _, r in recs.iterrows():
        impact = f" - about Rs {r['est_impact_inr']:,.0f}" if pd.notna(r["est_impact_inr"]) else ""
        with st.expander(f"#{r['id']} [{r['severity'].upper()}] {r['title']}{impact}"):
            st.write(f"**Suggested action:** {r['action']}")
            if r["decision_note"]:
                st.write(f"**Your note:** {r['decision_note']}")
            if r["outcome_note"]:
                st.info(f"Outcome: {r['outcome_note']}")
            note = st.text_input("Note (optional)", key=f"n{r['id']}")
            c1, c2, c3 = st.columns(3)
            if r["status"] == "proposed" and c1.button("Approve", key=f"a{r['id']}"):
                decide(conn, int(r["id"]), True, note); st.rerun()
            if r["status"] == "proposed" and c2.button("Reject", key=f"r{r['id']}"):
                decide(conn, int(r["id"]), False, note); st.rerun()
            if r["status"] == "approved" and c3.button("Mark done", key=f"d{r['id']}"):
                mark_done(conn, int(r["id"]), note); st.rerun()

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
    if not db.table_exists(conn, "agent_runs"):
        st.write("No questions asked yet.")
    else:
        runs = db.read(conn, "select ts, question, intent, pattern, model, input_tokens, output_tokens, model_calls, cost_usd, "
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
