"""Every page of the app runs, and the buttons that jump between pages land on the right page and agent.
Runs the real app file against a stand-in Streamlit in a separate process (so a real Streamlit install is untouched).
What this does NOT check: how the pages look. That needs a browser."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STUB = Path(__file__).resolve().parent / "ui_stub"

DRIVER = r'''
import json, runpy, sys
sys.path.insert(0, sys.argv[1])
import streamlit as st
out = []
for case in json.loads(sys.argv[2]):
    st.session_state.clear(); st.session_state.update(case["state"]); st.session_state["_press"] = set(case.get("press", []))
    st.LOG.clear()
    try:
        runpy.run_path("app/streamlit_app.py", run_name="__main__")
        res = "ran"
    except st._Switch:
        res = "jumped"
    except Exception as e:
        res = f"error {type(e).__name__}: {e}"
    ran = [x[1][0] for x in st.LOG if x[0] == "RUN"]
    out.append(dict(res=res, ran=ran, state={k: v for k, v in st.session_state.items() if k in ("ask_agent", "dash_agent")},
                    chat=len(st.session_state.get("chat", []) or []), metrics=[x[1][0] for x in st.LOG if x[0] == "METRIC"]))
print("RESULT" + json.dumps(out))
'''


def run_cases(db_path, cases):
    env = {**os.environ, "RI_DB_PATH": str(db_path), "ANTHROPIC_API_KEY": "", "SUPABASE_DB_URL": ""}
    r = subprocess.run([sys.executable, "-c", DRIVER, str(STUB), json.dumps(cases)], cwd=ROOT, env=env,
                       capture_output=True, text=True, timeout=600)
    line = next((l for l in r.stdout.splitlines() if l.startswith("RESULT")), None)
    assert line, r.stdout[-2000:] + r.stderr[-3000:]
    return json.loads(line[6:])


def test_every_page_runs_and_buttons_land_right(world, tmp_path):
    db_path = tmp_path / "app.db"
    shutil.copy(world["db_path"], db_path)
    pages = ["Control tower", "Ask an agent", "Role dashboards", "Decisions", "Trends and stock", "Agents and data",
             "Usage and history"]
    cases = [dict(state={"_test_page": p}) for p in pages]
    cases += [dict(state={"_test_page": "Role dashboards", "dash_agent": a})
              for a in ("replenishment", "margin_control", "revenue_assurance")]
    cases += [dict(state={"_test_page": "Control tower"}, press=["ct_a_replenishment"]),
              dict(state={"_test_page": "Control tower"}, press=["ct_d_margin_control"]),
              dict(state={"_test_page": "Ask an agent", "ask_agent": "revenue_assurance"},
                   press=["sq_revenue_assurance_Are any paid orders stuck?"])]
    out = run_cases(db_path, cases)
    for case, o in zip(cases, out):
        assert not o["res"].startswith("error"), (case, o["res"])
    n = len(pages) + 3
    assert all(o["ran"] == [c["state"]["_test_page"]] for c, o in zip(cases[:n], out[:n]))
    assert out[n]["res"] == "jumped" and out[n]["state"]["ask_agent"] == "replenishment"
    assert out[n + 1]["res"] == "jumped" and out[n + 1]["state"]["dash_agent"] == "margin_control"
    assert out[n + 2]["chat"] == 2                                        # starter question asked and answered
    assert "Net sales, last 28 days (ex-GST)" in out[0]["metrics"]
