"""Agent tests. A scripted fake model drives every control path; fake SDK objects check the API wire format.
No network, no API key. What these do NOT prove: how well a real model reasons. That is what run_eval is for."""
import json
import re
import shutil
import sys
import types
from types import SimpleNamespace as NS

import pytest

from retail_intel import db
from retail_intel.actions import check_outcomes, decide, list_recommendations, mark_done, sync_recommendations
from retail_intel.agent import memory
from retail_intel.agent.models import (AnthropicClient, ModelTurn, OpenAIResponsesClient, ScriptedModel, ToolCall, Usage,
                                       make_model, provider_from_env, with_retries)
from retail_intel.agent.orchestrator import Agent, Budget
from retail_intel.agent.router import classify, parse_dates
from retail_intel.agent.tools import Toolbox
from retail_intel.daily import run_daily
from retail_intel.eval.run_eval import run_eval
from retail_intel.findings import answer_question, run_all_findings


@pytest.fixture(scope="module")
def adb(world, tmp_path_factory):
    """A private copy of the pipeline database, so agent writes do not disturb other tests."""
    p = tmp_path_factory.mktemp("agentdb") / "a.db"
    shutil.copy(world["db_path"], p)
    return str(p)


def say(text):
    return ModelTurn(text, [], Usage())


def call(name, **args):
    return ModelTurn("", [ToolCall(f"c_{name}", name, args)], Usage())


# ------------------------------------------------------------------ component 1: API client
class RateLimited(Exception):
    status_code = 429


def test_retry_policy_retries_only_what_is_worth_retrying():
    attempts = {"n": 0}

    def flaky():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise RateLimited()
        return "ok"

    assert with_retries(flaky, sleep=lambda s: None) == "ok" and attempts["n"] == 3
    with pytest.raises(ValueError):
        with_retries(lambda: (_ for _ in ()).throw(ValueError("bad request")), sleep=lambda s: None)
    with pytest.raises(RateLimited):
        with_retries(lambda: (_ for _ in ()).throw(RateLimited()), attempts=2, sleep=lambda s: None)


def test_optional_openai_adapter_wire_format():
    sent = []
    replies = [NS(output=[NS(type="function_call", call_id="c1", name="get_sales_summary",
                              arguments='{"start": "2026-11-01", "end": "2026-11-23"}')],
                  output_text="", usage=NS(input_tokens=120, output_tokens=15)),
               NS(output=[NS(type="message")], output_text="Net sales were Rs 16.5 L [S1].", usage=NS(input_tokens=300, output_tokens=40))]
    fake = NS(responses=NS(create=lambda **kw: (sent.append(kw), replies[len(sent) - 1])[1]))
    m = OpenAIResponsesClient(model="test-model", client=fake)
    conv = m.conversation("SYSTEM", [{"role": "user", "content": "earlier"}], "How were sales?")
    t1 = conv.step([dict(name="get_sales_summary", description="d", input_schema={"type": "object", "properties": {}})])
    assert sent[0]["instructions"] == "SYSTEM" and sent[0]["model"] == "test-model"
    assert sent[0]["tools"][0] == {"type": "function", "name": "get_sales_summary", "description": "d",
                                   "parameters": {"type": "object", "properties": {}}}
    assert t1.tool_calls[0].arguments == {"start": "2026-11-01", "end": "2026-11-23"} and t1.usage.input_tokens == 120
    conv.add_tool_results([(t1.tool_calls[0], '{"net_revenue": 1650000}')])
    t2 = conv.step([])
    assert {"type": "function_call_output", "call_id": "c1", "output": '{"net_revenue": 1650000}'} in sent[1]["input"]
    assert "tools" not in sent[1] and t2.text.startswith("Net sales") and not t2.tool_calls


def test_claude_adapter_wire_format_caching_and_cost():
    sent = []
    replies = [NS(content=[NS(type="tool_use", id="t1", name="stockout_events", input={"min_days": 2})],
                  usage=NS(input_tokens=90, output_tokens=12, cache_creation_input_tokens=1000, cache_read_input_tokens=0)),
               NS(content=[NS(type="thinking", thinking="..."), NS(type="text", text="Two SKUs are out of stock.")],
                  usage=NS(input_tokens=200, output_tokens=20, cache_creation_input_tokens=0, cache_read_input_tokens=1000))]
    fake = NS(messages=NS(create=lambda **kw: (sent.append(kw), replies[len(sent) - 1])[1]))
    m = AnthropicClient(model="claude-sonnet-5-5", client=fake)
    conv = m.conversation("SYS", [], "What is out of stock?")
    tools = [dict(name=n, description="d", input_schema={"type": "object", "properties": {}}) for n in ("a", "stockout_events")]
    t1 = conv.step(tools)
    # prompt caching: system prompt and the LAST tool carry the cache marker
    assert sent[0]["system"] == [{"type": "text", "text": "SYS", "cache_control": {"type": "ephemeral"}}]
    assert "cache_control" not in sent[0]["tools"][0] and sent[0]["tools"][-1]["cache_control"] == {"type": "ephemeral"}
    assert t1.usage.cache_write_tokens == 1000 and t1.usage.cost_usd == 0.0028        # (90x2 + 1000x2x1.25 + 12x10) / 1e6
    conv.add_tool_results([(t1.tool_calls[0], "[]")])
    t2 = conv.step([])
    assert sent[1]["messages"][-1] == {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "[]"}]}
    assert t2.text == "Two SKUs are out of stock."                                     # thinking block ignored in text
    assert t2.usage.cache_read_tokens == 1000 and t2.usage.cost_usd == 0.0007         # cache read at 0.05x for Sonnet 5.5
    assert t2.usage.total_input_tokens == 1200


def test_claude_is_the_default_provider(monkeypatch):
    fake_mod = types.ModuleType("anthropic")
    fake_mod.Anthropic = lambda **kw: NS(messages=NS(create=None))
    monkeypatch.setitem(sys.modules, "anthropic", fake_mod)
    for k in ("AGENT_PROVIDER", "ANTHROPIC_API_KEY", "ANTHROPIC_MODEL", "ANTHROPIC_ROUTER_MODEL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    assert provider_from_env() == "" and make_model() is None          # an OpenAI key alone does not switch providers
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-x")
    assert provider_from_env() == "anthropic"
    assert make_model("answer").model == "claude-sonnet-5-5" and make_model("router").model == "claude-haiku-5-5"


# ------------------------------------------------------------------ routing
def test_model_is_only_asked_to_classify_when_rules_cannot():
    m = ScriptedModel(lambda conv, tools: say("inventory"))
    c = classify("xyzzy plugh frobnicate", m)
    assert c.intent == "inventory" and c.method == "model" and c.usage.calls == 1
    c2 = classify("Which batches are close to expiry?", m)
    assert c2.method == "rules" and c2.usage.calls == 0


def test_haiku_router_classifies_and_sonnet_answers(adb):
    haiku = ScriptedModel(lambda c, t: say("inventory"), model="claude-haiku-5-5")
    sonnet = ScriptedModel(lambda c, t: say("See [S1]."), model="claude-sonnet-5-5")
    r = Agent(adb, sonnet, router_model=haiku).ask("xyzzy plugh frobnicate")
    assert r.intent == "inventory" and r.method == "model"
    assert len(haiku.conversations) == 1 and len(sonnet.conversations) == 1 and r.usage.calls == 2
    assert "QUESTION" in sonnet.conversations[0].user_text and "QUESTION" not in haiku.conversations[0].user_text


def test_date_windows():
    a = "2026-11-23"
    assert (parse_dates("between 3 and 9 August 2026", a).start, parse_dates("between 3 and 9 August 2026", a).end) == ("2026-08-03", "2026-08-09")
    assert parse_dates("what happened on 14 September 2026", a).day == "2026-09-14"
    w = parse_dates("why did cancellations jump in mid-September?", a)
    assert (w.start, w.end) == ("2026-09-10", "2026-09-20")
    assert (parse_dates("top products last month", a).start, parse_dates("top products last month", a).end) == ("2026-10-01", "2026-10-31")
    assert parse_dates("sales in December", a).start == "2025-12-01"            # December not yet reached -> last year


def test_eval_set_routing_and_tool_choice(adb):
    df, s = run_eval(adb, model=None, write=False)
    assert s["intent_ok_pct"] == 100 and s["tools_ok_pct"] == 100, df[~(df.intent_ok & df.tools_ok)]


# ------------------------------------------------------------------ orchestration patterns
def test_fast_path_never_calls_the_model(adb):
    def boom(conv, tools):
        raise AssertionError("model should not be called")
    r = Agent(adb, ScriptedModel(boom)).ask("Give me the morning briefing")
    assert r.pattern == "fast_path" and r.usage.calls == 0 and "Daily Briefing" in r.answer


def test_concurrent_diagnosis_then_grounded_answer(adb):
    def policy(conv, tools):
        rev = re.search(r'"net_revenue": ([\d.]+)', conv.user_text).group(1)
        return say(f"Net revenue in the window was {float(rev):,.0f} [S1]; checkout conversion collapsed [S3].")
    m = ScriptedModel(policy)
    r = Agent(adb, m).ask("Why did sales fall between 3 and 9 August 2026?")
    assert r.intent == "diagnose" and r.pattern == "concurrent"
    assert {"website_funnel", "fulfilment_speed", "stockout_events", "discount_leakage"} <= set(r.tools_used)
    assert r.usage.calls == 1 and r.grounded is True and r.unsupported == []


def test_sequential_expiry_then_markdown(adb):
    r = Agent(adb, ScriptedModel(lambda c, t: say("See [S2]."))).ask("Which batches are close to expiry?")
    assert r.tools_used[:2] == ["expiry_exposure", "markdown_scenarios"] and r.pattern == "sequential"


def test_invented_number_is_challenged_then_flagged(adb):
    m = ScriptedModel(lambda conv, tools: say("Sales were Rs 987654 last week."))
    r = Agent(adb, m).ask("What were net sales and gross margin in October 2026?")
    conv = m.conversations[-1]
    assert len(conv.followups) == 1 and "987654" in conv.followups[0]          # one repair request
    assert r.grounded is False and "987654" in r.unsupported[0] and "could not be traced" in r.answer


def test_repair_turn_fixes_the_answer(adb):
    def policy(conv, tools):
        if not conv.followups:
            return say("Sales were Rs 987654.")
        rev = re.search(r'"net_revenue": ([\d.]+)', conv.user_text).group(1)
        return say(f"Net revenue was {rev} [S1].")
    r = Agent(adb, ScriptedModel(policy)).ask("What were net sales and gross margin in October 2026?")
    assert r.grounded is True and r.usage.calls == 2


def test_conditional_fallback_unlocks_sql_only_after_a_failure(adb):
    def policy(conv, tools):
        if conv.steps == 0:
            return call("markdown_scenarios", sku="no-such-product")
        if conv.steps == 1:
            return call("run_readonly_sql", query="select count(*) as n from dim_product")
        return say("There are 200 products [S2].")
    m = ScriptedModel(policy)
    r = Agent(adb, m).ask("Tell me something interesting about our catalogue")
    offered = m.conversations[-1].offered
    assert r.intent == "general" and "run_readonly_sql" not in offered[0] and "run_readonly_sql" in offered[1]
    assert r.evidence[0]["ok"] is False and r.evidence[1]["ok"] is True


def test_tools_outside_the_plan_are_refused(adb):
    before = list_recommendations(db.connect(adb))
    m = ScriptedModel(lambda conv, tools: call("decide_recommendation", rec_id=1, approve=True) if conv.steps == 0 else say("ok"))
    r = Agent(adb, m).ask("Which category makes the most margin?")
    assert "not available" in m.conversations[-1].tool_results[0][1]
    assert list_recommendations(db.connect(adb)).equals(before)


def test_budget_stops_a_runaway_loop(adb):
    m = ScriptedModel(lambda conv, tools: call("get_sales_summary", start="2026-01-01", end=f"2026-01-{conv.steps + 2:02d}"))
    r = Agent(adb, m, Budget(max_steps=5, max_tool_calls=3)).ask("Tell me something interesting about our catalogue")
    model_tool_calls = [e for e in r.evidence if e["tool"] == "get_sales_summary"]
    assert len(model_tool_calls) == 3 and r.stopped in ("step_limit", "tool_limit") and r.answer.startswith("I stopped")


# ------------------------------------------------------------------ caching and cost
def test_cache_hits_then_invalidates_on_new_data(adb):
    box = Toolbox(adb)
    args = dict(start="2026-03-01", end="2026-03-31")
    assert box.call_ex("get_sales_summary", args).cached is False
    assert box.call_ex("get_sales_summary", args).cached is True
    conn = db.connect(adb)
    conn.execute("insert into pipeline_runs values ('9999-01-01T00:00:00', 'ok')")
    conn.commit()
    assert box.call_ex("get_sales_summary", args).cached is False


def test_usage_and_cost_are_logged(adb):
    r = Agent(adb, ScriptedModel(lambda c, t: say("See [S1]."), model="claude-sonnet-5-5")).ask("How is website conversion trending?")
    assert r.usage.input_tokens > 0 and r.cost_usd is not None and r.cost_usd > 0
    row = db.read(db.connect(adb), "select * from agent_runs where run_id=?", (r.run_id,)).iloc[0]
    assert row["intent"] == "web" and row["input_tokens"] == r.usage.total_input_tokens


# ------------------------------------------------------------------ component 3: memory
def test_short_term_memory_carries_the_conversation(adb):
    m = ScriptedModel(lambda c, t: say("Noted [S1]."))
    agent = Agent(adb, m)
    first = agent.ask("How is website conversion trending?")
    agent.ask("And what about returns by category?", session_id=first.session_id)
    hist = m.conversations[-1].history
    assert hist[0] == {"role": "user", "content": "How is website conversion trending?"} and hist[1]["role"] == "assistant"


def test_long_term_memory_includes_owner_answers(adb):
    conn = db.connect(adb)
    fid = conn.execute("select finding_id from questions where status='open' limit 1").fetchone()[0]
    answer_question(conn, fid, "Supplier delay, new PO lands 3 Dec")
    assert "Supplier delay, new PO lands 3 Dec" in memory.long_term_context(conn)
    m = ScriptedModel(lambda c, t: say("ok"))
    Agent(adb, m).ask("Which products are out of stock right now?")
    assert "Supplier delay, new PO lands 3 Dec" in m.conversations[-1].system


# ------------------------------------------------------------------ component 4: task manager
def test_owner_can_approve_through_the_agent(adb):
    conn = db.connect(adb)
    rec_id = int(list_recommendations(conn, "proposed")["id"].iloc[0])
    m = ScriptedModel(lambda c, t: call("decide_recommendation", rec_id=rec_id, approve=True, note="PO raised")
                      if c.steps == 0 else say(f"Approved #{rec_id}."))
    r = Agent(adb, m).ask(f"approve {rec_id}, PO raised")
    assert r.intent == "recommendations" and r.pattern != "fast_path"
    assert tuple(conn.execute("select status, decision_note from recommendations where id=?", (rec_id,)).fetchone()) == ("approved", "PO raised")


def test_recommendation_lifecycle_and_outcome_check(adb):
    conn = db.connect(adb)
    recs = db.read(conn, "select id, type, status from recommendations")
    so = int(recs[(recs["type"] == "stockout") & (recs["status"] == "proposed")]["id"].iloc[0])
    decide(conn, so, True, "expedite")
    with pytest.raises(ValueError):
        decide(conn, so, False)                                              # cannot decide twice
    mark_done(conn, so, "supplier chased")
    out = check_outcomes(conn).set_index("id")
    assert "still out of stock" in out.loc[so, "outcome_note"]
    sync_recommendations(conn, run_all_findings(conn))                       # a re-run never overwrites a decision
    assert conn.execute("select status from recommendations where id=?", (so,)).fetchone()[0] == "done"
    sync_recommendations(conn, [])                                           # problems gone -> undecided ones expire
    left = db.read(conn, "select status from recommendations")["status"]
    assert "proposed" not in set(left) and "expired" in set(left)


def test_daily_job_writes_the_briefing(world, tmp_path):
    path = run_daily(world["src"], tmp_path / "d.db", tmp_path / "briefings", overrides_path=None)
    text = path.read_text()
    assert path.name == f"briefing_{world['truth']['as_of']}.md" and "Decisions waiting for you" in text
