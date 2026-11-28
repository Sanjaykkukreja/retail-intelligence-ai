"""Permanent storage: what people did survives a restart (the app's database file is wiped on every redeploy).
Runs against a REAL local Postgres (the engine Supabase uses) when one is available; skipped otherwise."""
import shutil

import pytest

from retail_intel import db
from retail_intel.actions import decide, list_recommendations
from retail_intel.agent.orchestrator import Agent
from retail_intel.findings import answer_question
from retail_intel.platform.definitions import change_rule, register, sign_off
from retail_intel.platform.questions import request_promotion
from retail_intel.platform.state import StateStore
from tests import pg_shim

pytestmark = pytest.mark.skipif(not pg_shim.available(), reason="no local Postgres server")


@pytest.fixture()
def store():
    name = pg_shim.make_db("ria_test")
    s = StateStore("test", "glowbasket", connect=lambda url: pg_shim.Connection(name))
    s.ensure()
    return s


def fresh(world, tmp_path, name):
    p = tmp_path / name
    shutil.copy(world["db_path"], p)
    return str(p)


def test_everything_people_did_survives_a_redeploy(world, tmp_path, store):
    p1 = fresh(world, tmp_path, "before.db")
    conn = db.connect(p1)
    r = Agent(p1, None).ask("Which products are out of stock right now?")
    rec_id = int(list_recommendations(conn, "proposed")["id"].iloc[0])
    decide(conn, rec_id, True, "PO raised")
    q = conn.execute("select finding_id from questions where status='open' limit 1").fetchone()[0]
    answer_question(conn, q, "supplier delay")
    change_rule(conn, "dispatch_sla_hours", 36, "Sanjay")
    sign_off(conn, "Net revenue", "Sanjay")
    request_promotion(conn, "Orders by city", "order_list", "add city", "Sanjay")
    sent = store.save(conn)
    assert sent["agent_runs"] == 1 and sent["agent_messages"] == 2 and sent["recommendations"] >= 1
    conn.close()

    # redeploy: a brand-new database built from the same data, nothing people did is in it
    p2 = fresh(world, tmp_path, "after.db")
    c2 = db.connect(p2)
    assert not db.table_exists(c2, "agent_runs")
    restored = StateStore("test", "glowbasket", connect=store._connect).restore(c2)
    assert restored["agent_runs"] == 1 and restored["agent_messages"] == 2
    assert c2.execute("select question from agent_runs").fetchone()[0] == "Which products are out of stock right now?"
    assert tuple(c2.execute("select status, decision_note from recommendations where id=?", (rec_id,)).fetchone()) == ("approved", "PO raised")
    assert c2.execute("select answer from questions where finding_id=?", (q,)).fetchone()[0] == "supplier delay"
    reg = register(c2).set_index("name")
    assert reg.loc["Dispatch promise", "value"] == "36 hours" and reg.loc["Net revenue", "signed_off"].startswith("Sanjay")
    assert c2.execute("select count(*) from question_promotions").fetchone()[0] == 1
    # the conversation continues where it stopped
    from retail_intel.agent import memory
    assert len(memory.history(c2, r.session_id)) == 2
    # new recommendations still get fresh numbers after a restore (auto-numbering intact)
    c2.execute("insert into recommendations (finding_id, status) values ('x:y:z', 'proposed')")
    assert c2.execute("select id from recommendations where finding_id='x:y:z'").fetchone()[0] > rec_id
    c2.close()


def test_saves_only_what_is_new(world, tmp_path, store):
    p = fresh(world, tmp_path, "inc.db")
    conn = db.connect(p)
    Agent(p, None).ask("How is website conversion trending?")
    assert store.save(conn)["agent_runs"] == 1
    assert store.save(conn)["agent_runs"] == 0                  # nothing new, nothing sent
    Agent(p, None).ask("What is our return rate by category?")
    assert store.save(conn)["agent_runs"] == 1
    conn.close()


def test_clients_are_kept_apart(world, tmp_path, store):
    p = fresh(world, tmp_path, "a.db")
    conn = db.connect(p)
    Agent(p, None).ask("How is website conversion trending?")
    store.save(conn)
    other = StateStore("test", "acme", connect=store._connect)
    c2 = db.connect(fresh(world, tmp_path, "b.db"))
    assert "agent_runs" not in other.restore(c2)
    assert dict((c, n) for c, n, _ in store.spend())["glowbasket"] in ("1", 1)


def test_an_outage_does_not_lose_or_duplicate(world, tmp_path, store):
    p = fresh(world, tmp_path, "o.db")
    conn = db.connect(p)
    Agent(p, None).ask("How is website conversion trending?")
    down = StateStore("test", "glowbasket", connect=lambda url: pg_shim.Connection("ria_test", fail=True))
    with pytest.raises(ConnectionError):
        down.save(conn)
    assert down._marks.get("agent_runs", 0) == 0               # nothing marked as saved
    assert store.save(conn)["agent_runs"] == 1                 # sent once the database is back
    store._marks.clear()
    store.save(conn)                                           # sending the same rows again creates no duplicates
    c2 = db.connect(fresh(world, tmp_path, "o2.db"))
    assert StateStore("test", "glowbasket", connect=store._connect).restore(c2)["agent_runs"] == 1
