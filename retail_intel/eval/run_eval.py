"""Evaluation harness (course: evaluating the agent before trusting it).

For each of 30 owner questions it checks:
  intent_ok    the router picked an acceptable question type
  tools_ok     the evidence includes at least one of the right tools
  mention_ok   the answer names the key fact (needs a model, or a fast-path answer)
  grounded     no figure in the answer is missing from tool results (needs a model)
plus tokens, cost and latency per question. Without an API key only routing and tool selection are scored.
Use the same set to compare providers or models: same questions, same data, compare score and cost.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from .. import db
from ..agent.orchestrator import Agent

EVAL_SET = Path(__file__).with_name("eval_set.json")


def run_eval(db_path: str, model=None, items: list[dict] | None = None, write: bool = True,
             router_model=None) -> tuple[pd.DataFrame, dict]:
    items = items or json.loads(EVAL_SET.read_text())
    agent = Agent(db_path, model, router_model=router_model)
    rows = []
    for it in items:
        r = agent.ask(it["question"])
        answered_by_text = r.stopped not in ("no_model",)
        mention = None
        if it.get("must_mention") and answered_by_text:
            low = r.answer.lower()
            mention = all(any(m.lower() in low for m in group) for group in it["must_mention"])
        rows.append(dict(id=it["id"], question=it["question"], intent=r.intent, pattern=r.pattern,
                         cache_read_tokens=r.usage.cache_read_tokens,
                         intent_ok=r.intent in it["intents"], tools_ok=any(t in r.tools_used for t in it["tools_any"]),
                         mention_ok=mention, grounded=r.grounded, input_tokens=r.usage.total_input_tokens,
                         output_tokens=r.usage.output_tokens, model_calls=r.usage.calls, cost_usd=r.cost_usd,
                         latency_s=r.latency_s, stopped=r.stopped))
    df = pd.DataFrame(rows)

    def rate(col):
        s = df[col].dropna()
        return round(100 * s.astype(bool).mean(), 1) if len(s) else None

    summary = dict(model=getattr(model, "model", "none"), questions=len(df), intent_ok_pct=rate("intent_ok"),
                   tools_ok_pct=rate("tools_ok"), mention_ok_pct=rate("mention_ok"), grounded_pct=rate("grounded"),
                   total_input_tokens=int(df["input_tokens"].sum()), total_output_tokens=int(df["output_tokens"].sum()),
                   avg_input_tokens_per_question=round(df["input_tokens"].mean(), 0),
                   total_cost_usd=None if df["cost_usd"].isna().all() else round(df["cost_usd"].sum(), 4),
                   avg_latency_s=round(df["latency_s"].mean(), 2))
    if write:
        conn = db.connect(db_path)
        out = df.copy()
        out["model"] = summary["model"]
        db.write(conn, out, "eval_results", if_exists="append")
        conn.close()
    return df, summary
