"""The question log: which questions people really ask, sorted into question families.

Every answered question is already logged in agent_runs (agent, question, tools used). Here each one is matched to
the families whose tools it used (catalog/question_families.toml). Questions that used the one-off query path, or
matched no family, are the long tail: the weekly review promotes the frequent ones into certified families.
"""
from __future__ import annotations

import json
import tomllib
from datetime import datetime
from pathlib import Path

import pandas as pd

from .. import db
from .catalog import CATALOG_DIR
from .registry import TOOLS, load_plugins

# Tools that carry no meaning about the question (memory and bookkeeping)
NEUTRAL = {"owner_answers", "open_questions", "list_findings", "data_quality"}


def load_families(path: Path | None = None) -> dict[str, dict]:
    return tomllib.loads(Path(path or CATALOG_DIR / "question_families.toml").read_text())["families"]


def validate_families(families: dict | None = None) -> list[str]:
    load_plugins()
    fams = families or load_families()
    problems = []
    for fid, f in fams.items():
        if f.get("status") not in ("certified", "build_next", "needs_data"):
            problems.append(f"{fid}: bad status {f.get('status')}")
        if f.get("status") == "certified":
            problems += [f"{fid}: unknown tool '{t}'" for t in f.get("tools", []) if t not in TOOLS]
            if not f.get("tools"):
                problems.append(f"{fid}: certified family needs tools")
        if f.get("status") == "needs_data" and not f.get("needs"):
            problems.append(f"{fid}: say which data is needed")
        if not f.get("examples"):
            problems.append(f"{fid}: give example questions")
    return problems


# Question types that name their family directly (the router already understood the question)
INTENT_FAMILY = {"briefing": "business_pulse", "diagnose": "what_changed_why", "recommendations": "decisions_and_outcomes",
                 "data_quality": "data_health", "owner_input": "owner_instructions", "web": "website_funnel"}


def matching_families(question: str, families: dict) -> list[str]:
    """Families whose trigger words appear in the question, in library order."""
    import re
    return [fid for fid, f in families.items() if f.get("match") and re.search(f["match"], question or "", re.I)]


def classify(tools_used: list[str], families: dict, agent: str | None = None,
             intent: str | None = None, question: str | None = None) -> tuple[list[str], bool]:
    """Families the answer belongs to (best match first), and whether it was exploratory.
    A tool that belongs to one family is strong evidence; a tool shared by many families is weak."""
    used = set(t for t in tools_used if t not in NEUTRAL)
    exploratory = "run_readonly_sql" in used
    share = {}
    for f in families.values():
        for t in f.get("tools", []):
            share[t] = share.get(t, 0) + 1
    scored = []
    for fid, f in families.items():
        score = sum(1 / share[t] for t in used & set(f.get("tools", [])))
        if score:
            scored.append((score, f.get("role") == agent, fid))
    fams = [fid for _, _, fid in sorted(scored, key=lambda x: (-x[0], not x[1], x[2]))]
    hint = INTENT_FAMILY.get(intent or "")
    if hint in families:
        fams = [hint] + [f for f in fams if f != hint]
    if question:                                    # the question's own words are the strongest signal
        named = [f for f in matching_families(question, families) if set(families[f].get("tools", [])) & used]
        fams = named + [f for f in fams if f not in named]
    return fams, exploratory


def question_log(conn, families: dict | None = None) -> pd.DataFrame:
    families = families or load_families()
    if not db.table_exists(conn, "agent_runs"):
        return pd.DataFrame(columns=["ts", "agent", "question", "family", "exploratory", "intent"])
    cols = [r[1] for r in conn.execute("pragma table_info(agent_runs)")]
    runs = db.read(conn, "select ts, " + ("agent, " if "agent" in cols else "'analyst' as agent, ")
                   + "question, intent, tools from agent_runs order by ts desc")
    out = []
    for _, r in runs.iterrows():
        fams, expl = classify(json.loads(r["tools"] or "[]"), families, r["agent"], r["intent"], r["question"])
        out.append(dict(ts=r["ts"], agent=r["agent"], question=r["question"], intent=r["intent"],
                        family=fams[0] if fams else "(none)", exploratory=expl or not fams))
    return pd.DataFrame(out)


def family_summary(log: pd.DataFrame, families: dict | None = None) -> pd.DataFrame:
    """Per family: how often asked, how often exploratory, plus families nobody has asked yet."""
    families = families or load_families()
    rows = []
    for fid, f in families.items():
        sub = log[log["family"] == fid] if len(log) else log
        rows.append(dict(family=fid, label=f["label"], role=f["role"], status=f["status"], asked=int(len(sub)),
                         last_asked=sub["ts"].max() if len(sub) else None,
                         sample=sub["question"].iloc[0] if len(sub) else f["examples"][0]))
    none = log[log["family"] == "(none)"] if len(log) else log
    rows.append(dict(family="(none)", label="Matched no family", role="-", status="long tail", asked=int(len(none)),
                     last_asked=none["ts"].max() if len(none) else None,
                     sample=none["question"].iloc[0] if len(none) else ""))
    return pd.DataFrame(rows).sort_values("asked", ascending=False)


def request_promotion(conn, question: str, suggested_family: str = "", note: str = "", requested_by: str = "") -> None:
    """Ask for a long-tail question to become a certified family (a task for the build team)."""
    conn.execute("""create table if not exists question_promotions (question text, suggested_family text, note text,
                    requested_by text, requested_at text, status text)""")
    conn.execute("insert into question_promotions values (?,?,?,?,?, 'requested')",
                 (question, suggested_family, note, requested_by, datetime.now().isoformat(timespec="seconds")))
    conn.commit()


def promotions(conn) -> pd.DataFrame:
    if not db.table_exists(conn, "question_promotions"):
        return pd.DataFrame(columns=["question", "suggested_family", "note", "requested_by", "requested_at", "status"])
    return db.read(conn, "select * from question_promotions order by requested_at desc")
