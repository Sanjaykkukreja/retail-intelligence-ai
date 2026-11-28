"""Component 5 - Interface. The owner's screen.   Run:  streamlit run app/streamlit_app.py

This file only sets things up: secrets, demo data on first start, the history store, the look, and the menu.
The pages are in views.py; the look is in style.py.

Every page is run in the automated checks against a stand-in for Streamlit, so code errors are caught; how the
pages look is only checked in a real browser. If a page shows an error, send the message back.
"""
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import streamlit as st

from retail_intel import db
from retail_intel.briefing import build_briefing
from retail_intel.config import DB_PATH
from retail_intel.metrics.common import as_of
from retail_intel.platform.catalog import active_agents, load_client, load_templates
from retail_intel.platform.registry import load_plugins
from retail_intel.platform.state import StateStore

import views
from style import apply_style, sidebar_brand

st.set_page_config(page_title="Retail Intelligence", page_icon=":material/insights:", layout="wide")
apply_style()

# Cloud hosting: keys pasted into Streamlit's "Secrets" box become environment variables.
try:
    for _k in ("ANTHROPIC_API_KEY", "ANTHROPIC_MODEL", "ANTHROPIC_ROUTER_MODEL", "AGENT_PROVIDER", "OPENAI_API_KEY",
               "OPENAI_MODEL", "CLIENT_CONFIG_TOML", "SUPABASE_DB_URL"):
        if _k in st.secrets and not os.environ.get(_k):
            os.environ[_k] = str(st.secrets[_k])
except Exception:  # no secrets configured (e.g. on a laptop) - fine
    pass

# First start in the cloud there is no terminal, so the app builds its own demo data (about a minute).
if not Path(DB_PATH).exists():
    from retail_intel.config import SOURCE_DIR, TRUTH_DIR
    from retail_intel.pipeline import run_pipeline
    from retail_intel.synth.generate import generate
    with st.spinner("First start: building the demo data. This takes about a minute..."):
        generate(SOURCE_DIR, TRUTH_DIR)
        _r = run_pipeline()
    if _r["status"] != "ok":
        st.error("The data check stopped the build:\n\n" + _r["blockers"].to_string(index=False))
        st.stop()

conn = db.connect(DB_PATH)
load_plugins()
client = load_client()


@st.cache_resource(show_spinner="Loading saved history...")
def state_store(client_id: str):
    """Once per app start: connect to Supabase (if configured) and bring back everything people did."""
    s = StateStore.from_env(client_id)
    if s is None:
        return None, "History is not saved permanently yet."
    try:
        s.ensure()
        c = db.connect(DB_PATH)
        n = s.restore(c)
        c.close()
        return s, f"History saved permanently · {sum(n.values())} records restored"
    except Exception as e:                                   # the app must still open if Supabase is down
        return None, f"History store unreachable ({type(e).__name__}); this session is not saved."


store, store_note = state_store(client.id)


def persist():
    """Send what changed to the history store. Called after every question and every decision."""
    if store is None:
        return
    try:
        store.save(conn)
    except Exception as e:
        st.warning(f"Not saved to the history store this time ({type(e).__name__}). It will be sent with the next change.")


@st.cache_data(show_spinner="Building today's briefing...")
def _briefing(version: str) -> dict:
    c = db.connect(DB_PATH)
    try:
        return build_briefing(c)
    finally:
        c.close()


version = conn.execute("select max(run_at) from pipeline_runs").fetchone()[0] if db.table_exists(conn, "pipeline_runs") else "0"
templates = load_templates()
agents = active_agents(client, templates)
by_id = {a.id: a for a in agents}
if st.session_state.get("dash_agent") not in by_id:
    st.session_state.dash_agent = agents[0].id
if st.session_state.get("ask_agent") not in by_id:
    st.session_state.ask_agent = client.default_agent if client.default_agent in by_id else agents[0].id

PAGES = {
    "Control tower": st.Page(views.page_control_tower, title="Control tower", icon=":material/space_dashboard:",
                             url_path="control-tower", default=True),
    "Ask an agent": st.Page(views.page_ask, title="Ask an agent", icon=":material/forum:", url_path="ask"),
    "Role dashboards": st.Page(views.page_roles, title="Role dashboards", icon=":material/groups:", url_path="roles"),
    "Decisions": st.Page(views.page_decisions, title="Decisions", icon=":material/task_alt:", url_path="decisions"),
    "Trends and stock": st.Page(views.page_trends, title="Trends and stock", icon=":material/monitoring:", url_path="trends"),
    "Agents and data": st.Page(views.page_setup, title="Agents and data", icon=":material/tune:", url_path="setup"),
    "Usage and history": st.Page(views.page_usage, title="Usage and history", icon=":material/history:", url_path="usage"),
}
views.S.__dict__.update(conn=conn, today=as_of(conn), client=client, templates=templates, agents=agents, by_id=by_id,
                        persist=persist, briefing=lambda: _briefing(version), db_path=str(DB_PATH), pages=PAGES)

sidebar_brand(client.name, f"Data through {views.S.today} · {len(agents)} agents  \n{store_note}",
              str(HERE / "logo.svg"), str(HERE / "logo_icon.svg"))
nav = st.navigation({
    "Overview": [PAGES["Control tower"], PAGES["Ask an agent"]],
    "Work": [PAGES["Role dashboards"], PAGES["Decisions"], PAGES["Trends and stock"]],
    "Admin": [PAGES["Agents and data"], PAGES["Usage and history"]],
})
nav.run()
