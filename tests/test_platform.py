"""Platform tests: the plug points stay plug points, the catalog is valid, and role agents stay in their lane.

The modularity check reads the source code: core modules must never import a specific tool, KPI or connector.
If someone hard-wires one, this test fails and says which file did it.
"""
import ast
import shutil
from pathlib import Path

import pytest

from retail_intel.agent.models import ModelTurn, ScriptedModel, ToolCall, Usage
from retail_intel.agent.orchestrator import Agent
from retail_intel.agent.tools import Toolbox
from retail_intel.platform.catalog import (AgentTemplate, ClientConfig, active_agents, date_values, fill_args, load_client,
                                           load_streams, load_templates, owner_agent, readiness, validate)
from retail_intel.platform.dashboards import agent_dashboard, control_tower, fmt_value, kpi_values
from retail_intel.platform.registry import CONNECTORS, KPIS, TOOLS, Registry, load_plugins

PKG = Path(__file__).resolve().parents[1] / "retail_intel"
PLUGINS = ("retail_intel.tools.", "retail_intel.kpis.", "retail_intel.connectors.")
CORE = ["platform", "agent/orchestrator.py", "agent/tools.py", "agent/router.py", "pipeline.py"]


@pytest.fixture(scope="module")
def pdb(world, tmp_path_factory):
    p = tmp_path_factory.mktemp("platformdb") / "p.db"
    shutil.copy(world["db_path"], p)
    return str(p)


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text())
    pkg = "retail_intel." + ".".join(path.relative_to(PKG).with_suffix("").parts[:-1])
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            base = pkg.rsplit(".", n.level - 1)[0] if n.level else ""
            mod = (base + "." + n.module if n.level and n.module else base if n.level else n.module) or ""
            out += [mod] + [f"{mod}.{a.name}" for a in n.names]
        elif isinstance(n, ast.Import):
            out += [a.name for a in n.names]
    return out


# ------------------------------------------------------------------ modularity
def test_core_never_imports_a_specific_plugin():
    offenders = []
    for c in CORE:
        for f in ([PKG / c] if c.endswith(".py") else sorted((PKG / c).glob("*.py"))):
            offenders += [f"{f.relative_to(PKG)} imports {m}" for m in _imports(f) if m.startswith(PLUGINS)]
    assert not offenders, offenders


def test_plugins_register_themselves():
    load_plugins()
    assert len(TOOLS) >= 24 and "order_to_cash_gaps" in TOOLS and "run_readonly_sql" in TOOLS
    assert {"ongoing_stockouts", "paid_not_dispatched_value", "rupees_at_stake"} <= set(KPIS.names())
    assert {"csv_files", "zoho_books"} <= set(CONNECTORS.names())


def test_registry_rejects_a_duplicate_name():
    r = Registry("thing")
    r.add("a", 1)
    with pytest.raises(ValueError):
        r.add("a", 2)
    with pytest.raises(KeyError, match="Available"):
        r.get("b")


# ------------------------------------------------------------------ catalog
def test_every_template_is_valid():
    streams = load_streams()
    for t in load_templates().values():
        assert validate(t, streams) == [], (t.id, validate(t, streams))


def test_a_new_agent_is_one_file(tmp_path):
    (tmp_path / "promo_watch.toml").write_text('''
id = "promo_watch"
name = "Promo watcher"
role = "Marketing"
goal = "Watch promotions."
decisions = ["Which promotion to stop"]
north_star = "leakage_shortfall_28d"
outcome = "Shortfall after a stopped promo"
tools = ["discount_leakage"]
streams = ["order_lines"]
''')
    ts = load_templates([PKG / "catalog" / "agents", tmp_path])
    assert "promo_watch" in ts and validate(ts["promo_watch"]) == []


def test_validation_catches_bad_templates():
    bad = AgentTemplate(id="x", name="X", role="r", goal="g", tools=["no_such_tool"], streams=["no_such_stream"],
                        kpis=["no_such_kpi"], autonomy=7)
    problems = " | ".join(validate(bad))
    for word in ("no_such_tool", "no_such_stream", "no_such_kpi", "autonomy", "north-star"):
        assert word in problems


def test_readiness_follows_the_data_contract():
    ts, client = load_templates(), load_client()
    assert readiness(ts["replenishment"], client)["ready"]
    ap = readiness(ts["ap_three_way_match"], client)
    assert not ap["ready"] and {"purchase_orders", "bills"} <= set(ap["missing"])
    no_data = ClientConfig(id="c", name="C", connectors=[], enabled_agents=["replenishment"])
    assert "missing data" in readiness(ts["replenishment"], no_data)["reason"]
    assert [a.id for a in active_agents(client, ts)] == ["chief_of_staff", "replenishment", "margin_control", "revenue_assurance"]


def test_client_setup_can_come_from_secrets(monkeypatch):
    monkeypatch.setenv("CLIENT_CONFIG_TOML", 'id="acme"\nname="Acme"\nconnectors=["csv_files"]\nenabled_agents=["replenishment"]\n')
    assert load_client().id == "acme"


def test_findings_go_to_the_specialist_first():
    ag = active_agents()
    assert owner_agent("stockout", ag) == "replenishment"
    assert owner_agent("discount_leakage", ag) == "margin_control"
    assert owner_agent("paid_not_dispatched", ag) == "revenue_assurance"
    assert owner_agent("checkout_drop", ag) == "chief_of_staff"          # nobody specialises: catch-all


def test_placeholders_fill_or_skip():
    v = date_values("2026-11-23")
    assert fill_args({"start": "$d28", "end": "$today", "limit": 5}, v) == {"start": "2026-10-27", "end": "2026-11-23", "limit": 5}
    assert fill_args({"day": "$day"}, {"day": None}) is None


# ------------------------------------------------------------------ role agents on one engine
def test_role_agent_only_sees_its_own_tools(pdb):
    t = load_templates()["replenishment"]
    turns = iter([ModelTurn("", [ToolCall("c1", "website_funnel", {"start": "2026-11-01", "end": "2026-11-23"})], Usage()),
                  ModelTurn("Stock is fine [S1].", [], Usage())])
    m = ScriptedModel(lambda conv, tools: next(turns))
    r = Agent(pdb, m, template=t).ask("What should I reorder this week?")
    assert r.agent == "replenishment" and r.intent == "inventory"
    conv = m.conversations[0]
    assert "Replenishment planner" in conv.system and "DECISIONS YOU SUPPORT" in conv.system
    assert all(set(o) <= set(t.tools) for o in conv.offered) and "website_funnel" not in conv.offered[0]
    assert "website_funnel" not in r.tools_used                          # refused: not this role's tool


def test_off_role_question_uses_role_evidence(pdb):
    t = load_templates()["revenue_assurance"]
    r = Agent(pdb, None, template=t).ask("hello, anything I should know?")
    assert r.stopped == "no_model" and r.tools_used == ["order_to_cash_gaps"]


def test_fast_paths_belong_to_roles(pdb):
    ts = load_templates()
    assert Agent(pdb, None, template=ts["chief_of_staff"]).ask("Give me the briefing").pattern == "fast_path"
    r = Agent(pdb, None, template=ts["margin_control"]).ask("Give me the briefing")   # no briefing shortcut for this role
    assert "get_briefing" not in r.tools_used and "discount_leakage" in r.tools_used


def test_role_budget_comes_from_the_template(pdb):
    a = Agent(pdb, None, template=load_templates()["replenishment"])
    assert a.budget.max_steps == 5 and a.budget.max_cost_usd == 0.15


def test_runs_are_logged_per_agent(pdb):
    from retail_intel import db
    Agent(pdb, None, template=load_templates()["margin_control"]).ask("Did the Diwali sale lose us money?")
    conn = db.connect(pdb)
    assert conn.execute("select count(*) from agent_runs where agent='margin_control'").fetchone()[0] >= 1
    conn.close()


# ------------------------------------------------------------------ dashboards
def test_kpis_match_the_planted_problems(pdb, world):
    from retail_intel import db
    conn = db.connect(pdb)
    v = {k["name"]: k for k in kpi_values(conn, ["paid_not_dispatched", "dispatched_not_billed", "ongoing_stockouts"])}
    assert v["paid_not_dispatched"]["value"] == world["truth"]["stuck_orders"] if "stuck_orders" in world["truth"] else v["paid_not_dispatched"]["value"] > 0
    assert v["dispatched_not_billed"]["value"] > 0 and v["ongoing_stockouts"]["value"] >= 1
    conn.close()


def test_control_tower_and_role_dashboard(pdb):
    from retail_intel import db
    conn = db.connect(pdb)
    ag = active_agents()
    ct = control_tower(conn, ag)
    assert list(ct["agents"]["agent_id"]) == [a.id for a in ag]
    assert ct["agents"]["decisions_waiting"].sum() == len(ct["waiting"])     # every waiting decision has an owner
    d = agent_dashboard(conn, ag[1], ag, Toolbox(pdb))
    assert d["north_star"]["name"] == "stockout_lost_sales"
    assert all(t["error"] is None and len(t["rows"]) for t in d["tables"])
    assert set(d["recommendations"]["type"]) <= set(ag[1].finding_types)
    conn.close()


def test_money_format():
    assert fmt_value(157894, "inr") == "Rs 1.58 L" and fmt_value(2.1e7, "inr") == "Rs 2.10 Cr"
    assert fmt_value(None, "inr") == "not known" and fmt_value(53.52, "pct") == "53.5%"


def test_off_role_question_stays_inside_the_role(pdb):
    t = load_templates()["replenishment"]
    m = ScriptedModel(lambda conv, tools: ModelTurn("That is for the Chief of staff agent.", [], Usage()))
    r = Agent(pdb, m, template=t).ask("Why did checkout conversion drop in early August?")
    assert r.intent == "web"
    offered = m.conversations[0].offered[0]
    assert offered and set(offered) <= set(t.tools) and "website_funnel" not in offered
    assert "website_funnel" not in r.tools_used                  # the web plan's evidence was not fetched either


def test_specialist_always_has_its_core_evidence_and_only_its_findings(pdb):
    t = load_templates()["revenue_assurance"]
    r = Agent(pdb, None, template=t).ask("Are any paid orders stuck?")
    assert r.intent == "operations" and "order_to_cash_gaps" in r.tools_used
    lf = next(e for e in r.evidence if e["tool"] == "list_findings")
    assert lf["args"]["types"] == t.finding_types
    assert {row["type"] for row in lf["result"]["rows"]} <= set(t.finding_types)
    assert len(r.tools_used) == len(set(map(str, [(e["tool"], e["args"]) for e in r.evidence])))   # nothing fetched twice


def test_order_to_cash_profile_explains_the_gap(pdb, world):
    from retail_intel import db
    from retail_intel.metrics.finance import order_to_cash
    conn = db.connect(pdb)
    gaps = {g["gap"]: g for g in order_to_cash(conn)["gaps"]}
    stuck = gaps["paid_not_dispatched"]
    assert stuck["orders"] == world["truth"]["counts"]["stuck_orders_old"]
    assert stuck["profile"]["where_stuck"] == {"in warehouse, status PENDING": stuck["orders"]}
    assert stuck["profile"]["pattern"].startswith("spread")                  # planted at random all year
    assert stuck["profile"]["orders_with_a_product_out_of_stock_that_day"] == 0
    unbilled = gaps["dispatched_not_billed"]
    assert unbilled["orders"] == world["truth"]["counts"]["manual_orders"] and unbilled["billed_value_ex_gst"] is None
    assert unbilled["profile"]["order_number_prefix"] == {"MN": unbilled["orders"]}   # manual orders skip billing
    conn.close()


def test_stuck_order_question_gets_the_profile_from_any_agent(pdb):
    r = Agent(pdb, None, template=load_templates()["chief_of_staff"]).ask("Why are 73 paid orders pending to dispatch?")
    assert "order_to_cash_gaps" in r.tools_used
    r2 = Agent(pdb, None).ask("How is dispatch speed this month?")             # unrelated operations question: not fetched
    assert "order_to_cash_gaps" not in r2.tools_used


# ------------------------------------------------------------------ question families
def test_order_list_matches_the_planted_truth(pdb, world):
    from retail_intel import db
    from retail_intel.metrics.orders import order_list
    conn = db.connect(pdb)
    t = world["truth"]
    assert order_list(conn, "cancelled")["orders"] == t["counts"]["cancelled_orders"]
    assert order_list(conn, "not_dispatched", min_age_days=4)["orders"] == t["counts"]["stuck_orders_old"]
    rto_unbilled = conn.execute("select count(*) from order_xref where dispatched_not_billed=1 and wms_status='RTO'").fetchone()[0]
    assert order_list(conn, "rto")["orders"] + rto_unbilled == t["counts"]["rto_orders"]   # billing sees all but the manual ones
    d0, d1 = t["planted"]["dispatch_delay"]["start"], t["planted"]["dispatch_delay"]["end"]
    during = order_list(conn, "delayed", start=d0, end=d1)["orders"]
    week_before = order_list(conn, "delayed", start="2026-09-07", end="2026-09-13")["orders"]
    assert during > 10 * max(week_before, 1)
    g = order_list(conn, "cancelled", group_by="payment_mode")
    assert int(g["groups"]["orders"].sum()) == g["orders"]
    with pytest.raises(ValueError):
        order_list(conn, "lost")
    conn.close()


def test_sell_through_adds_up(pdb):
    from retail_intel import db
    from retail_intel.metrics.orders import sell_through
    conn = db.connect(pdb)
    st = sell_through(conn, "2026-08-26", "2026-11-23")
    assert st["sell_through_pct"].between(0, 100).all() and (st["gmroi_annual"] > 0).all()
    sold = conn.execute("""select sum(qty) from fact_order_line where recognized = 1
                           and order_date between '2026-08-26' and '2026-11-23'""").fetchone()[0]
    assert st["units_sold"].sum() == pytest.approx(sold)
    conn.close()


def test_question_library_is_valid_and_classifies():
    from retail_intel.platform.questions import classify, load_families, validate_families
    fams = load_families()
    assert validate_families(fams) == []
    assert classify(["fulfilment_speed", "order_to_cash_gaps", "list_findings"], fams,
                    question="Are any paid orders stuck?")[0][0] == "order_to_cash"
    assert classify(["get_sales_summary", "stockout_events"], fams, intent="diagnose")[0][0] == "what_changed_why"
    assert classify(["run_readonly_sql"], fams) == ([], True)


def test_certified_example_questions_reach_their_family(pdb):
    """Each certified family's example questions, asked to the general analyst with NO model, must fetch that family's
    tools. Catches a family that exists on paper but that routing never reaches (it found 3 routing bugs on day one)."""
    from retail_intel.platform.questions import classify, load_families
    fams = load_families()
    misses = []
    for fid, f in fams.items():
        if f["status"] != "certified" or fid in ("owner_instructions", "decisions_and_outcomes"):
            continue
        for q in f["examples"][:2]:
            r = Agent(pdb, None).ask(q)
            got, _ = classify(r.tools_used, fams, None, r.intent)
            if fid not in got:
                misses.append(f"{fid}: '{q}' -> {got[:2]} via {r.intent}")
    assert not misses, misses


def test_definitions_register_and_rule_changes(pdb):
    from retail_intel import db
    from retail_intel.platform.definitions import change_rule, register, sign_off
    from retail_intel.reconcile.order_matching import build_order_xref
    conn = db.connect(pdb)
    reg = register(conn).set_index("name")
    assert reg.loc["Stuck order", "value"] == "4 days" and reg.loc["Stuck order", "signed_off"] == "not signed off"
    sign_off(conn, "Stuck order", "Sanjay")
    before = conn.execute("select sum(paid_not_dispatched) from order_xref").fetchone()[0]
    change_rule(conn, "stuck_after_days", 30, "Sanjay")
    build_order_xref(conn)
    after = conn.execute("select sum(paid_not_dispatched) from order_xref").fetchone()[0]
    assert after < before                                     # the owner's rule really drives the count
    reg = register(conn).set_index("name")
    assert reg.loc["Stuck order", "value"] == "30 days" and "changed since" in reg.loc["Stuck order", "signed_off"]
    assert conn.execute("select new_value from rule_changes").fetchone()[0] == "30"
    change_rule(conn, "stuck_after_days", 4, "Sanjay"); build_order_xref(conn)
    with pytest.raises(ValueError):
        sign_off(conn, "Stuck order", "  ")
    conn.close()


def test_exploratory_answers_are_labelled(pdb):
    turns = iter([ModelTurn("", [ToolCall("c1", "top_skus", {"start": "2030-01-01", "end": "2030-01-02"})], Usage()),
                  ModelTurn("", [ToolCall("c2", "run_readonly_sql", {"query": "select count(*) n from dim_product"})], Usage()),
                  ModelTurn("There are 200 products [S2].", [], Usage())])
    r = Agent(pdb, ScriptedModel(lambda conv, tools: next(turns))).ask("How many products do we sell in total?")
    assert r.exploratory and "Exploratory answer" in r.answer
