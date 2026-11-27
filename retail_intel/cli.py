"""Command line:  python -m retail_intel <command> [args]

Data
  generate                 create the pretend GlowBasket data (three messy systems)
  pipeline                 load, reconcile, check quality, build core tables, find issues, update tasks
  daily                    pipeline + outcome checks + briefing saved to data/briefings/ (schedule this)
  all                      generate + pipeline + brief
Owner
  brief                    print today's briefing
  questions                questions waiting for you
  answer ID TEXT           save your answer to a question
  recs [status]            recommendations (proposed / approved / rejected / done / expired)
  approve ID [note]        approve a recommendation          reject ID [note]   reject it
  done ID [note]           mark an approved recommendation as done
  review-queue             product matches that need a human decision
AI analyst (needs ANTHROPIC_API_KEY; see README step 9)
  ask "QUESTION"           one question
  chat                     a conversation that remembers earlier turns (type 'exit' to stop)
  eval                     score the agent on 30 test questions (routing only if no API key)
"""
from __future__ import annotations

import argparse

import pandas as pd

from . import db
from .config import DB_PATH, SOURCE_DIR, TRUTH_DIR

COMMANDS = ["generate", "pipeline", "daily", "all", "brief", "questions", "answer", "recs", "approve", "reject", "done",
            "review-queue", "ask", "chat", "eval"]


def _agent():
    from .agent.orchestrator import Agent
    agent = Agent.from_env(DB_PATH)
    if agent.model is None:
        print("(No ANTHROPIC_API_KEY set: the analyst will show the evidence it gathered instead of a written answer.)\n")
    return agent


def _print_result(r) -> None:
    print(r.answer)
    cost = f", ~${r.cost_usd:.4f}" if r.cost_usd is not None else ""
    print(f"\n[{r.intent} | {r.pattern} | tools: {', '.join(r.tools_used) or 'none'} | "
          f"{r.usage.total_input_tokens}+{r.usage.output_tokens} tokens ({r.usage.cache_read_tokens} cached) in "
          f"{r.usage.calls} model calls{cost} | {r.latency_s}s"
          + ("" if r.grounded in (None, True) else " | UNGROUNDED FIGURES FLAGGED") + "]")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="retail_intel", description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("command", choices=COMMANDS)
    ap.add_argument("args", nargs="*")
    ns = ap.parse_args(argv)
    pd.set_option("display.width", 200)
    pd.set_option("display.max_colwidth", 90)
    a = ns.args

    if ns.command in ("generate", "all"):
        from .synth.generate import generate
        t = generate(SOURCE_DIR, TRUTH_DIR)
        print(f"Generated {t['counts']['n_skus']} SKUs, {t['counts']['billing_orders']} orders in {SOURCE_DIR}")
    if ns.command in ("pipeline", "all"):
        from .pipeline import run_pipeline
        r = run_pipeline()
        print("Pipeline status:", r["status"])
        if r["status"] != "ok":
            print("STOPPED. Fix these first:\n", r["blockers"].to_string(index=False))
            return 1
        print("Quality warnings:\n", r["dq"][r["dq"]["severity"] == "warn"][["check_name", "issue_count"]].to_string(index=False))
        print(f"{len(r['findings'])} findings saved.")
    if ns.command == "daily":
        from .daily import run_daily
        print("Briefing written to", run_daily())
    if ns.command in ("brief", "all"):
        from .briefing import build_briefing
        print(build_briefing(db.connect(DB_PATH), a[0] if a else None)["markdown"])
    if ns.command == "questions":
        q = db.read(db.connect(DB_PATH), "select finding_id, status, question, answer from questions order by status")
        print(q.to_string(index=False))
    if ns.command == "answer":
        from .findings import answer_question
        if len(a) < 2:
            print('Usage: python -m retail_intel answer "<finding id>" "<your answer>"'); return 2
        answer_question(db.connect(DB_PATH), a[0], " ".join(a[1:])); print("Saved.")
    if ns.command == "recs":
        from .actions import list_recommendations
        print(list_recommendations(db.connect(DB_PATH), a[0] if a else None)
              [["id", "status", "severity", "title", "est_impact_inr", "outcome_note"]].to_string(index=False))
    if ns.command in ("approve", "reject", "done"):
        from .actions import decide, mark_done
        if not a:
            print(f"Usage: python -m retail_intel {ns.command} <id> [note]"); return 2
        conn = db.connect(DB_PATH)
        note = " ".join(a[1:])
        out = mark_done(conn, int(a[0]), note) if ns.command == "done" else decide(conn, int(a[0]), ns.command == "approve", note)
        print(out)
    if ns.command == "review-queue":
        from .reconcile.sku_mapping import review_queue
        q = review_queue(db.connect(DB_PATH))
        print(q.to_string(index=False) if len(q) else "Nothing waiting. All SKU mappings are accepted.")
    if ns.command == "ask":
        _print_result(_agent().ask(" ".join(a)))
    if ns.command == "chat":
        agent, session = _agent(), None
        while True:
            try:
                q = input("\nYou: ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if q.lower() in ("exit", "quit", ""):
                break
            r = agent.ask(q, session)
            session = r.session_id
            print("\nAnalyst:"); _print_result(r)
    if ns.command == "eval":
        from .agent.models import make_model
        from .eval.run_eval import run_eval
        df, s = run_eval(str(DB_PATH), make_model("answer"), router_model=make_model("router"))
        print(df[["id", "intent", "pattern", "intent_ok", "tools_ok", "mention_ok", "grounded", "input_tokens", "latency_s"]]
              .to_string(index=False))
        print("\nSUMMARY:", {k: (float(v) if hasattr(v, "item") else v) for k, v in s.items()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
