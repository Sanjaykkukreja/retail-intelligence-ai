"""The agent: classify -> plan -> gather evidence -> reason with tools -> verify -> answer -> remember.

Patterns from the course, all in one loop:
  fast path      some questions (briefing, data health, recommendation list) need no model call at all
  concurrent     independent evidence is fetched in parallel before the model starts (diagnosis, operations)
  sequential     a follow-up step uses the first result (expiring batch -> markdown scenarios for that batch)
  conditional    the model gets a narrow tool set; the SQL fallback is only unlocked after a tool fails or is empty
  early stop     the loop ends as soon as the model answers; budgets cap steps, tool calls, tokens and cost
  synthesis      sources are labelled [S1..]; a grounding check rejects figures that no tool produced
"""
from __future__ import annotations

import json
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime

from .. import db
from ..briefing import build_briefing
from ..metrics.common import as_of as _as_of
from . import memory, router
from .grounding import unsupported_numbers
from .models import ModelClient, Usage, cost_usd
from .prompts import REPAIR, SYSTEM
from .tools import Toolbox, is_empty

EVIDENCE_CHARS_PER_SOURCE = 6000
TOOL_RESULT_CHARS = 8000


@dataclass
class Budget:
    max_steps: int = 6
    max_tool_calls: int = 10
    max_input_tokens: int = 200_000
    max_cost_usd: float | None = None


@dataclass
class AgentResult:
    answer: str
    intent: str
    method: str
    pattern: str
    evidence: list = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    cost_usd: float | None = None
    grounded: bool | None = None
    unsupported: list = field(default_factory=list)
    stopped: str = "answered"
    latency_s: float = 0.0
    session_id: str = ""
    run_id: str = ""

    @property
    def tools_used(self) -> list[str]:
        return [e["tool"] for e in self.evidence]


class Agent:
    @classmethod
    def from_env(cls, db_path, budget: Budget | None = None) -> "Agent":
        """Claude Sonnet answers, Claude Haiku routes; with no API key the agent runs in evidence-only mode."""
        from .models import make_model
        return cls(db_path, make_model("answer"), budget, router_model=make_model("router"))

    def __init__(self, db_path, model: ModelClient | None = None, budget: Budget | None = None,
                 router_model: ModelClient | None = None):
        self.db_path = str(db_path)
        self.model = model
        self.router_model = router_model or model     # Haiku when configured: classification is a cheap job
        self.budget = budget or Budget()
        self.box = Toolbox(self.db_path)

    # ------------------------------------------------------------------------------------------------ public
    def ask(self, question: str, session_id: str | None = None) -> AgentResult:
        t0 = time.time()
        session_id = session_id or memory.new_session()
        conn = db.connect(self.db_path)
        try:
            hist = memory.history(conn, session_id)
            cls = router.classify(question, self.router_model)
            ctx = router.make_ctx(conn, question, _as_of(conn))
            plan = router.PLANS[cls.intent]
            res = AgentResult("", cls.intent, cls.method, plan.pattern, usage=Usage().add(cls.usage), session_id=session_id)

            if plan.fast_path and self._fast_path_applies(plan, question):
                res.answer, res.pattern = self._fast_path(conn, plan.fast_path, res), "fast_path"
            else:
                self._gather(plan, ctx, res)
                if self.model is None:
                    res.answer = ("AI analyst is not configured (no API key), so here is the evidence it would have used:\n\n"
                                  + self._render_evidence(res.evidence))
                    res.stopped = "no_model"
                else:
                    self._reason(conn, question, hist, plan, res)
            res.cost_usd = cost_usd(res.usage)
            res.latency_s = round(time.time() - t0, 2)
            self._persist(conn, question, res)
            return res
        finally:
            conn.close()

    # ------------------------------------------------------------------------------------------------ steps
    @staticmethod
    def _fast_path_applies(plan, question: str) -> bool:
        if plan.fast_path == "recommendations":      # a decision needs the model to read which # and what note
            return not re.search(r"\b(approve|reject|accept|decline)\b.*\d|#\d", question.lower())
        return True

    def _fast_path(self, conn, kind: str, res: AgentResult) -> str:
        if kind == "briefing":
            self._add_evidence(res, self.box.call_ex("get_briefing", {}))
            return build_briefing(conn)["markdown"]
        if kind == "data_quality":
            tr = self.box.call_ex("data_quality", {})
            self._add_evidence(res, tr)
            rows = tr.result["rows"] if tr.ok else []
            bad = [r for r in rows if r["severity"] in ("warn", "block") and r["issue_count"]]
            head = "Data checks: " + ("all clear." if not bad else f"{len(bad)} need attention.")
            return head + "\n" + "\n".join(f"- [{r['severity']}] {r['check_name'].replace('_', ' ')}: {r['issue_count']} ({r['detail']})"
                                           for r in bad)
        if kind == "recommendations":
            tr = self.box.call_ex("list_recommendations", {"status": "proposed"})
            self._add_evidence(res, tr)
            rows = tr.result["rows"] if tr.ok else []
            if not rows:
                return "No recommendations are waiting for a decision."
            return (f"{len(rows)} recommendations are waiting for your decision:\n"
                    + "\n".join(f"- #{r['id']} [{r['severity']}] {r['title']}" for r in rows)
                    + "\n\nSay e.g. \"approve 3, PO raised\" or \"reject 5, intentional clearance\".")
        raise ValueError(kind)

    def _gather(self, plan, ctx, res: AgentResult) -> None:
        calls = plan.gather(ctx)
        if calls:
            with ThreadPoolExecutor(max_workers=min(6, len(calls))) as ex:      # concurrent orchestration
                results = list(ex.map(lambda c: self.box.call_ex(c[0], c[1]), calls))
            for tr in results:
                self._add_evidence(res, tr)
        if plan.followup:                                                       # sequential orchestration
            for name, args in plan.followup(ctx, res.evidence):
                self._add_evidence(res, self.box.call_ex(name, args))

    def _reason(self, conn, question, hist, plan, res: AgentResult) -> None:
        b = self.budget
        system = SYSTEM + memory.long_term_context(conn)
        user = (f"QUESTION: {question}\n\nEVIDENCE already gathered for this question "
                f"(pattern: {plan.pattern}):\n{self._format_evidence(res.evidence) or '(none - use the tools)'}")
        conv = self.model.conversation(system, hist, user)
        fallback = plan.allow_sql_now or any((not e["ok"]) or is_empty(e["result"]) for e in res.evidence)
        tool_calls, repaired, answer = 0, False, None
        while True:
            if res.usage.calls >= b.max_steps:
                res.stopped = "step_limit"; break
            if res.usage.total_input_tokens >= b.max_input_tokens:
                res.stopped = "token_budget"; break
            if b.max_cost_usd is not None and (cost_usd(res.usage) or 0) >= b.max_cost_usd:
                res.stopped = "cost_budget"; break
            offered = plan.tools + (["run_readonly_sql"] if fallback else [])
            turn = conv.step(self.box.schemas(offered))
            res.usage.add(turn.usage)
            if not turn.tool_calls:                                             # early termination: model is done
                answer = turn.text.strip()
                bad = unsupported_numbers(answer, [json.dumps(e["result"], default=str) for e in res.evidence] + [question])
                if bad and not repaired and res.usage.calls < b.max_steps:
                    repaired = True
                    conv.add_user(REPAIR.format(numbers=", ".join(bad)))
                    continue
                res.unsupported, res.grounded = bad, not bad
                break
            results = []
            for c in turn.tool_calls:
                if c.name not in offered:
                    out = {"error": f"tool '{c.name}' is not available for this question"}
                    results.append((c, json.dumps(out)))
                    fallback = True
                    continue
                if tool_calls >= b.max_tool_calls:
                    results.append((c, json.dumps({"error": "tool budget reached; answer with the evidence you have"})))
                    res.stopped = "tool_limit"
                    continue
                tr = self.box.call_ex(c.name, c.arguments)
                tool_calls += 1
                sid = self._add_evidence(res, tr)
                if not tr.ok or is_empty(tr.result):
                    fallback = True                                             # conditional fallback unlocks SQL
                results.append((c, json.dumps({"source": sid, "result": tr.result}, default=str)[:TOOL_RESULT_CHARS]))
            conv.add_tool_results(results)
        if answer is None:
            answer = (f"I stopped before finishing ({res.stopped.replace('_', ' ')}). What I found so far:\n\n"
                      + self._render_evidence(res.evidence))
        elif res.unsupported:
            answer += "\n\n_Check: these figures could not be traced to a tool result: " + ", ".join(res.unsupported) + "_"
        res.answer = answer

    # ------------------------------------------------------------------------------------------------ helpers
    @staticmethod
    def _add_evidence(res: AgentResult, tr) -> str:
        sid = f"S{len(res.evidence) + 1}"
        res.evidence.append(dict(source=sid, tool=tr.name, args=tr.args, ok=tr.ok, cached=tr.cached, ms=tr.ms, result=tr.result))
        return sid

    @staticmethod
    def _format_evidence(evidence: list) -> str:
        parts = []
        for e in evidence:
            body = json.dumps(e["result"], default=str)[:EVIDENCE_CHARS_PER_SOURCE]
            parts.append(f"[{e['source']}] {e['tool']}({json.dumps(e['args'], default=str)})"
                         + ("" if e["ok"] else " FAILED") + f":\n{body}")
        return "\n\n".join(parts)

    @staticmethod
    def _render_evidence(evidence: list) -> str:
        return "\n".join(f"- [{e['source']}] {e['tool']} {e['args']}: {json.dumps(e['result'], default=str)[:600]}" for e in evidence)

    def _persist(self, conn, question: str, res: AgentResult) -> None:
        res.run_id = uuid.uuid4().hex[:12]
        conn.execute("""create table if not exists agent_runs (run_id text, session_id text, ts text, question text, intent text,
                        method text, pattern text, tools text, model text, input_tokens integer, output_tokens integer,
                        model_calls integer, cost_usd real, grounded integer, unsupported text, stopped text, latency_s real)""")
        for col in ("cache_read_tokens", "cache_write_tokens"):          # added when caching arrived; old tables get it
            try:
                conn.execute(f"alter table agent_runs add column {col} integer")
            except Exception:
                pass
        conn.execute("""insert into agent_runs (run_id, session_id, ts, question, intent, method, pattern, tools, model,
                        input_tokens, output_tokens, model_calls, cost_usd, grounded, unsupported, stopped, latency_s,
                        cache_read_tokens, cache_write_tokens) values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                     (res.run_id, res.session_id, datetime.now().isoformat(timespec="seconds"), question, res.intent, res.method,
                      res.pattern, json.dumps(res.tools_used), getattr(self.model, "model", "none"), res.usage.total_input_tokens,
                      res.usage.output_tokens, res.usage.calls, res.cost_usd, None if res.grounded is None else int(res.grounded),
                      json.dumps(res.unsupported), res.stopped, res.latency_s, res.usage.cache_read_tokens,
                      res.usage.cache_write_tokens))
        conn.commit()
        memory.append(conn, res.session_id, "user", question)
        memory.append(conn, res.session_id, "assistant", res.answer)
