"""The catalog: data streams, agent templates and client setups, all as plain TOML files.

  catalog/streams.toml          the standard data shapes every agent and connector share (the data contract)
  catalog/agents/*.toml         one file per role agent: goal, tools, streams needed, KPIs, autonomy, dashboard
  catalog/clients/demo.toml     one client's setup: connector, enabled agents, autonomy overrides

A real client's setup holds private settings, so it never goes in the public repo. Point to it with the
CLIENT_CONFIG environment variable (a file path) or CLIENT_CONFIG_TOML (the file's text, for Streamlit Secrets).

Adding an agent = adding one TOML file (plus any new tools/KPIs as plug-in files). No core code changes.
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from .registry import CONNECTORS, KPIS, TOOLS, load_plugins

CATALOG_DIR = Path(__file__).resolve().parents[1] / "catalog"
AGENTS_DIR = CATALOG_DIR / "agents"
AUTONOMY = {0: "L0 inform", 1: "L1 recommend", 2: "L2 draft", 3: "L3 act after approval", 4: "L4 act within limits"}


# ----------------------------------------------------------------------------------------------- streams
@dataclass
class Stream:
    name: str
    label: str
    fields: list[str]
    freshness_hours: int
    personal_data: bool = False


def load_streams(path: Path | None = None) -> dict[str, Stream]:
    raw = tomllib.loads(Path(path or CATALOG_DIR / "streams.toml").read_text())
    return {k: Stream(k, v["label"], v["fields"], int(v.get("freshness_hours", 24)), bool(v.get("personal_data", False)))
            for k, v in raw["streams"].items()}


# ----------------------------------------------------------------------------------------------- agents
@dataclass
class AgentTemplate:
    id: str
    name: str
    role: str
    goal: str
    status: str = "ready"                       # ready | planned
    focus: str = ""                             # role-specific instructions for the model
    decisions: list[str] = field(default_factory=list)       # the decisions this role owns (design around decisions)
    cadence: str = ""                                         # when the role acts: daily check, weekly review ...
    north_star: str = ""                                      # the one KPI this agent is judged on
    guardrails: list[str] = field(default_factory=list)       # KPIs it must not damage while improving the north star
    outcome: str = ""                                         # how we prove its advice paid off
    tools: list[str] = field(default_factory=list)
    streams: list[str] = field(default_factory=list)
    kpis: list[str] = field(default_factory=list)
    finding_types: list[str] = field(default_factory=list)   # "*" = every finding
    autonomy: int = 1
    fast_paths: list[str] = field(default_factory=list)
    gather: list[dict] = field(default_factory=list)          # evidence fetched when the question plan gives none
    budget: dict = field(default_factory=dict)                # max_steps, max_tool_calls, max_cost_usd
    dashboard: dict = field(default_factory=dict)             # tables = [{title, tool, args}]
    starter_questions: list[str] = field(default_factory=list)
    source: str = ""

    @property
    def autonomy_label(self) -> str:
        return AUTONOMY.get(self.autonomy, f"L{self.autonomy}")

    def owns(self, finding_type: str) -> bool:
        return "*" in self.finding_types or finding_type in self.finding_types


def load_templates(dirs: list[Path] | None = None) -> dict[str, AgentTemplate]:
    """Every *.toml in the agent folders becomes a template. Later folders may add agents, not replace them."""
    out: dict[str, AgentTemplate] = {}
    for d in dirs or [AGENTS_DIR]:
        for p in sorted(Path(d).glob("*.toml")):
            raw = tomllib.loads(p.read_text())
            t = AgentTemplate(**raw, source=str(p))
            if t.id in out:
                raise ValueError(f"agent id '{t.id}' defined twice ({out[t.id].source} and {p})")
            out[t.id] = t
    return out


def validate(t: AgentTemplate, streams: dict[str, Stream] | None = None) -> list[str]:
    """Problems with a template: unknown tools, KPIs or streams; a ready agent with no tools."""
    load_plugins()
    streams = streams or load_streams()
    problems = [f"unknown tool '{x}'" for x in t.tools if x not in TOOLS]
    problems += [f"unknown KPI '{x}'" for x in t.kpis if x not in KPIS]
    problems += [f"unknown stream '{x}'" for x in t.streams if x not in streams]
    problems += [f"unknown north-star KPI '{x}'" for x in [t.north_star] if x and x not in KPIS]
    problems += [f"unknown guardrail KPI '{x}'" for x in t.guardrails if x not in KPIS]
    if t.status == "ready" and not (t.decisions and t.north_star and t.outcome):
        problems.append("ready agent must state its decisions, north-star KPI and outcome measure")
    problems += [f"dashboard uses unknown tool '{tb.get('tool')}'" for tb in t.dashboard.get("tables", []) if tb.get("tool") not in TOOLS]
    problems += [f"gather uses unknown tool '{g.get('tool')}'" for g in t.gather if g.get("tool") not in TOOLS]
    if t.status == "ready" and not t.tools:
        problems.append("ready agent has no tools")
    if t.autonomy not in AUTONOMY:
        problems.append(f"autonomy {t.autonomy} is not 0-4")
    return problems


# ----------------------------------------------------------------------------------------------- clients
@dataclass
class ClientConfig:
    id: str
    name: str
    connectors: list[str]
    enabled_agents: list[str]
    connector_settings: dict = field(default_factory=dict)    # {connector: {setting: value}}  (no secrets in files)
    autonomy: dict = field(default_factory=dict)              # {agent_id: level} overrides
    default_agent: str = "chief_of_staff"


def load_client(path: str | Path | None = None) -> ClientConfig:
    text = os.environ.get("CLIENT_CONFIG_TOML")
    if path is None and not text:
        path = os.environ.get("CLIENT_CONFIG") or CATALOG_DIR / "clients" / "demo.toml"
    raw = tomllib.loads(text if (text and path is None) else Path(path).read_text())
    return ClientConfig(**raw)


def available_streams(client: ClientConfig) -> set[str]:
    load_plugins()
    got: set[str] = set()
    for name in client.connectors:
        cls = CONNECTORS.get(name)
        if cls.status == "ready":
            got |= set(cls.streams)
    return got


def readiness(t: AgentTemplate, client: ClientConfig) -> dict:
    """Can this agent run for this client? Ready only if built and every stream it needs is connected."""
    missing = [s for s in t.streams if s not in available_streams(client)]
    ready = t.status == "ready" and not missing
    reason = ("ready" if ready else "agent is planned, not built yet" if t.status != "ready"
              else "missing data: " + ", ".join(missing))
    return dict(ready=ready, missing=missing, reason=reason, enabled=t.id in client.enabled_agents)


def active_agents(client: ClientConfig | None = None, templates: dict | None = None) -> list[AgentTemplate]:
    """Enabled for this client AND ready to run, with the client's autonomy override applied."""
    client = client or load_client()
    templates = templates or load_templates()
    out = []
    for aid in client.enabled_agents:
        t = templates.get(aid)
        if t and readiness(t, client)["ready"]:
            if aid in client.autonomy:
                t.autonomy = int(client.autonomy[aid])
            out.append(t)
    return out


def owner_agent(finding_type: str, agents: list[AgentTemplate]) -> str | None:
    """The first specialist that owns a finding type; the catch-all ("*") agent only if no specialist does."""
    for t in agents:
        if "*" not in t.finding_types and t.owns(finding_type):
            return t.id
    return next((t.id for t in agents if "*" in t.finding_types), None)


# ----------------------------------------------------------------------------------------------- arguments
def fill_args(args: dict, values: dict) -> dict | None:
    """Replace "$name" placeholders in tool arguments. Returns None if a needed value is missing."""
    out = {}
    for k, v in (args or {}).items():
        if isinstance(v, str) and v.startswith("$"):
            if values.get(v[1:]) is None:
                return None
            out[k] = values[v[1:]]
        else:
            out[k] = v
    return out


def date_values(as_of: str) -> dict:
    """Placeholders dashboards can use: $today, $d7, $d28, $d90 (start dates of trailing windows)."""
    a = date.fromisoformat(as_of)
    return dict(today=as_of, **{f"d{n}": (a - timedelta(days=n - 1)).isoformat() for n in (7, 28, 90)})
