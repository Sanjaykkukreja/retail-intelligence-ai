"""Problem classification, routing and the tool decision matrix (course: "Customer Support Workflow Design").

1. classify()     cheap rules first; only if the rules are ambiguous AND a model is configured, ask the model
                  (conditional fallback applied to classification itself)
2. parse_dates()  turns "mid-September", "last month", "3-9 August" into a date window
3. PLANS          for each intent: orchestration pattern, the narrowed tool set the model may use,
                  evidence to gather up front, and any follow-up step
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable

from .. import db
from .models import ModelClient, Usage

# ----------------------------------------------------------------------------- classification
RULES = [  # (intent, weight, pattern) - order also breaks ties
    ("briefing", 3, r"\bbrief(ing)?\b|how('?s| is| was) (my |the )?business|how('re| are| is) (we|things) doing|summar(y|ise|ize)|what should i (focus|do|look)|"
                    r"top (\d|three|five) (things|actions|priorities)|morning (report|summary|update)"),
    ("recommendations", 3, r"\brecommendations?\b|\bapprov|\breject|pending decisions?|waiting for my (approval|decision)|action list"),
    ("owner_input", 3, r"\b(was|is) (a |an )?(planned|intentional|deliberate|supplier delay)|\bthe reason (was|is)\b|"
                       r"\bnew stock arrives\b|\barrives? (on|by)?\s*\d|\bour (margin )?target is\b|\bset (the |our )?(margin|target)"),
    ("data_quality", 3, r"data (quality|health)|\breliable\b|\btrust\b|\bduplicat|\bsku map|\bmapping\b|reconcil|mismatch|missing data"),
    ("diagnose", 2, r"\bwhy\b|\bdecline|\bdrop|\bdip\b|\bfell\b|\bfall\b|\bjump|\bspike|root cause|what happened|went wrong"),
    ("expiry", 2, r"expir|shelf.?life|markdown|clearance|liquidat|\bbatch(es)?\b"),
    ("inventory", 2, r"\bstock|restock|inventory|reorder|replenish|days of (cover|supply)|slow.?mov|overstock|excess"),
    ("pricing", 2, r"discount|\bpromo|coupon|pricing|\bprice|margin target|diwali sale|lose us money|leak"),
    ("operations", 2, r"dispatch|\bship|deliver|warehouse|\brto\b|cancel|fulfil|stuck|invoice|unbilled"),
    ("web", 2, r"website|traffic|session|conversion|checkout|\bcarts?\b|abandon|add.to.cart|funnel|page views|payment gateway"),
    ("returns", 2, r"\breturn|refund|sent back|send back|shade|reaction"),
    ("sales", 1, r"\bsales\b|\bsell(ing|s)?\b|revenue|\borders?\b|\baov\b|average order|top (\d+ )?(products|skus|sellers)|best.?sell|"
                 r"worst|categor|gross margin|\bmargin\b|compare|normal day"),
]
INTENTS = [r[0] for r in RULES] + ["general"]
DOMAIN = {"expiry", "inventory", "pricing", "operations", "web", "returns"}


@dataclass
class Classification:
    intent: str
    method: str                 # rules | model | default
    scores: dict
    usage: Usage = field(default_factory=Usage)


def classify_rules(q: str) -> dict[str, int]:
    ql = q.lower()
    return {intent: w for intent, w, pat in RULES if re.search(pat, ql)}


def classify(q: str, model: ModelClient | None = None) -> Classification:
    s = classify_rules(q)
    if not s:
        return _model_or_default(q, model, s)
    if "diagnose" in s:                                  # "why" + a specific domain -> the domain plan is sharper
        dom = {k: v for k, v in s.items() if k in DOMAIN}
        if dom:
            best = max(dom.values())
            winners = [i for i, _, _ in RULES if dom.get(i) == best]
            return Classification(winners[0], "rules", s)
        top3 = {k: v for k, v in s.items() if v >= 3}
        if top3:
            return Classification(max(top3, key=top3.get), "rules", s)
        return Classification("diagnose", "rules", s)
    best = max(s.values())
    winners = [i for i, _, _ in RULES if s.get(i) == best]
    if len(winners) > 1 and model is not None:
        return _model_or_default(q, model, s, winners)
    return Classification(winners[0], "rules", s)


def _model_or_default(q, model, scores, candidates=None) -> Classification:
    if model is None:
        return Classification("general", "default", scores)
    options = candidates or INTENTS
    text, usage = model.complete("Classify the business question. Reply with exactly one word from this list: "
                                 + ", ".join(options), q)
    pick = next((o for o in options if o in (text or "").lower()), options[0] if candidates else "general")
    return Classification(pick, "model", scores, usage)


# ----------------------------------------------------------------------------- date windows
MON = (r"\b(january|february|march|april|may|june|july|august|september|october|november|december|"
       r"jan|feb|mar|apr|jun|jul|aug|sept|sep|oct|nov|dec)\b")
MNUM = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


@dataclass
class Window:
    start: str
    end: str
    day: str | None
    label: str


def _iso(d: date) -> str:
    return d.isoformat()


def parse_dates(q: str, as_of: str, default_days: int = 28) -> Window:
    ql, a = q.lower(), date.fromisoformat(as_of)

    def year_for(m, y):
        return int(y) if y else (a.year if m <= a.month else a.year - 1)

    def cap(d):
        return min(d, a)

    isos = re.findall(r"\d{4}-\d{2}-\d{2}", q)
    if len(isos) >= 2:
        return Window(min(isos), max(isos), None, f"{min(isos)} to {max(isos)}")
    if len(isos) == 1:
        d = date.fromisoformat(isos[0])
        return Window(_iso(d - timedelta(days=7)), isos[0], isos[0], isos[0])
    m = re.search(r"(\d{1,2})(?:st|nd|rd|th)?\s*(?:-|to|and|–)\s*(\d{1,2})(?:st|nd|rd|th)?\s+" + MON + r"\s*(\d{4})?", ql)
    if m:
        mon = MNUM[m.group(3)[:3]]
        y = year_for(mon, m.group(4))
        s, e = date(y, mon, int(m.group(1))), date(y, mon, int(m.group(2)))
        return Window(_iso(s), _iso(cap(e)), None, f"{s:%d %b} to {e:%d %b %Y}")
    m = re.search(r"(\d{1,2})(?:st|nd|rd|th)?\s+" + MON + r"\s*(\d{4})?", ql) or \
        re.search(MON + r"\s+(\d{1,2})(?:st|nd|rd|th)?\b,?\s*(\d{4})?", ql)
    if m:
        g = m.groups()
        dd, mn, yy = (g[0], g[1], g[2]) if g[0].isdigit() else (g[1], g[0], g[2])
        mon = MNUM[mn[:3]]
        d = date(year_for(mon, yy), mon, int(dd))
        return Window(_iso(d - timedelta(days=7)), _iso(d), _iso(d), f"{d:%d %b %Y}")
    m = re.search(r"\b(early|mid|late)[-\s]+" + MON + r"\s*(\d{4})?", ql)
    if m:
        mon = MNUM[m.group(2)[:3]]
        y = year_for(mon, m.group(3))
        last = calendar.monthrange(y, mon)[1]
        lo, hi = {"early": (1, 10), "mid": (10, 20), "late": (20, last)}[m.group(1)]
        return Window(_iso(date(y, mon, lo)), _iso(cap(date(y, mon, hi))), None, f"{m.group(1)}-{m.group(2)}")
    if re.search(r"\byesterday\b|\btoday\b|right now|\bnow\b|currently", ql) and not re.search(r"this (week|month)", ql):
        return Window(_iso(a - timedelta(days=default_days - 1)), as_of, as_of, f"latest day {as_of}")
    m = re.search(r"last (\d+) days", ql)
    if m:
        n = int(m.group(1))
        return Window(_iso(a - timedelta(days=n - 1)), as_of, None, f"last {n} days")
    if "last week" in ql or "this week" in ql:
        return Window(_iso(a - timedelta(days=6)), as_of, None, "last 7 days")
    if "this month" in ql:
        return Window(_iso(a.replace(day=1)), as_of, None, "month to date")
    if "last month" in ql:
        e = a.replace(day=1) - timedelta(days=1)
        return Window(_iso(e.replace(day=1)), _iso(e), None, f"{e:%B %Y}")
    if "this year" in ql:
        return Window(_iso(a.replace(month=1, day=1)), as_of, None, "year to date")
    if re.search(r"quarter|last 3 months|90 days", ql):
        return Window(_iso(a - timedelta(days=89)), as_of, None, "last 90 days")
    m = re.search(MON + r"\s*(\d{4})?", ql)
    if m:
        mon = MNUM[m.group(1)[:3]]
        y = year_for(mon, m.group(2))
        s, e = date(y, mon, 1), date(y, mon, calendar.monthrange(y, mon)[1])
        return Window(_iso(s), _iso(cap(e)), None, f"{s:%B %Y}")
    return Window(_iso(a - timedelta(days=default_days - 1)), as_of, None, f"last {default_days} days")


# ----------------------------------------------------------------------------- product mentions
def product_hint(conn, q: str) -> str | None:
    """Return a product-name fragment mentioned in the question (e.g. 'vitamin c serum', 'niacinamide')."""
    ql = q.lower()
    names = [r[0] for r in conn.execute("select name from dim_product").fetchall()]
    best, best_n = None, 0
    for n in names:
        words = [w for w in re.findall(r"[a-z]+", n.lower()) if len(w) >= 3 and not re.match(r"\d", w)]
        hits = [w for w in words if re.search(r"\b" + w, ql)]
        if len(hits) >= 2 and len(hits) > best_n:
            best, best_n = " ".join(n.lower().split()[:-1]), len(hits)    # drop the size
    if best:
        return best
    for n in names:                                                      # single distinctive first word
        first = n.lower().split()[0]
        if len(first) >= 5 and re.search(r"\b" + first, ql):
            return first
    return None


# ----------------------------------------------------------------------------- decision matrix
READ_TOOLS = ["get_sales_summary", "compare_day", "top_skus", "category_performance", "discount_leakage", "stock_position",
              "stockout_events", "expiry_exposure", "markdown_scenarios", "slow_movers", "website_funnel", "fulfilment_speed",
              "return_reasons", "list_findings", "data_quality", "owner_answers", "list_recommendations"]


@dataclass
class Ctx:
    q: str
    win: Window
    as_of: str
    product: str | None


@dataclass
class Plan:
    pattern: str                                   # fast_path | sequential | conditional | concurrent
    tools: list[str]                               # what the model may call (narrow = cheaper, better choices)
    gather: Callable[[Ctx], list] = lambda c: []   # evidence fetched before the model is called
    followup: Callable[[Ctx, list], list] | None = None   # sequential step that needs the gathered evidence
    fast_path: str | None = None                   # answer without any model call
    allow_sql_now: bool = False


def _w(c: Ctx) -> dict:
    return dict(start=c.win.start, end=c.win.end)


def _prior(c: Ctx) -> dict:
    s, e = date.fromisoformat(c.win.start), date.fromisoformat(c.win.end)
    n = (e - s).days + 1
    return dict(start=_iso(s - timedelta(days=n)), end=_iso(s - timedelta(days=1)))


def _diagnose_gather(c: Ctx) -> list:
    g = [("compare_day", dict(day=c.win.day))] if c.win.day else []
    return g + [("get_sales_summary", _w(c)), ("get_sales_summary", _prior(c)), ("website_funnel", _w(c)),
                ("fulfilment_speed", _w(c)), ("stockout_events", {}), ("discount_leakage", _w(c)),
                ("list_findings", dict(current_only=False, limit=12))]


def _expiry_followup(c: Ctx, evidence: list) -> list:
    if c.product:
        return [("markdown_scenarios", dict(sku=c.product))]
    ex = next((e for e in evidence if e["tool"] == "expiry_exposure" and e["ok"]), None)
    rows = ex["result"].get("rows", []) if ex and isinstance(ex["result"], dict) else []
    return [("markdown_scenarios", dict(sku=rows[0]["master_sku_id"], batch_no=rows[0]["batch_no"]))] if rows else []


PLANS: dict[str, Plan] = {
    "briefing": Plan("fast_path", [], fast_path="briefing"),
    "data_quality": Plan("fast_path", [], fast_path="data_quality"),
    "recommendations": Plan("conditional", ["list_recommendations", "decide_recommendation"],
                            gather=lambda c: [("list_recommendations", {})], fast_path="recommendations"),
    "owner_input": Plan("conditional", ["open_questions", "owner_answers", "record_answer", "update_business_rule"],
                        gather=lambda c: [("open_questions", {})]),
    "diagnose": Plan("concurrent", READ_TOOLS, gather=_diagnose_gather),
    "sales": Plan("conditional", ["get_sales_summary", "compare_day", "top_skus", "category_performance", "list_findings"],
                  gather=lambda c: [("compare_day", dict(day=c.win.day))] if c.win.day else [("get_sales_summary", _w(c))]),
    "inventory": Plan("conditional", ["stock_position", "stockout_events", "slow_movers", "expiry_exposure", "list_findings"],
                      gather=lambda c: [("stock_position", dict(name_contains=c.product)), ("stockout_events", {})] if c.product
                      else [("stockout_events", {}), ("stock_position", {})]),
    "expiry": Plan("sequential", ["expiry_exposure", "markdown_scenarios", "slow_movers"],
                   gather=lambda c: [("expiry_exposure", {})], followup=_expiry_followup),
    "pricing": Plan("conditional", ["discount_leakage", "category_performance", "top_skus", "get_sales_summary"],
                    gather=lambda c: [("discount_leakage", _w(c) if "diwali" not in c.q.lower()
                                       else dict(start=f"{c.as_of[:4]}-10-15", end=c.as_of))]),
    "operations": Plan("concurrent", ["fulfilment_speed", "list_findings", "data_quality"],
                       gather=lambda c: [("fulfilment_speed", _w(c)), ("list_findings", dict(limit=10)), ("data_quality", {})]),
    "web": Plan("conditional", ["website_funnel", "get_sales_summary", "list_findings"], gather=lambda c: [("website_funnel", _w(c))]),
    "returns": Plan("conditional", ["return_reasons", "top_skus"], gather=lambda c: [("return_reasons", _w(c))]),
    "general": Plan("conditional", READ_TOOLS),
}


def make_ctx(conn, q: str, as_of: str) -> Ctx:
    return Ctx(q, parse_dates(q, as_of), as_of, product_hint(conn, q))
