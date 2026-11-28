# 06 Platform modules: how to add an agent, tool, KPI or connector

The platform is one application built from separate modules. Each module plugs in at a fixed point and registers itself. **Adding something means adding a file; it never means editing core code.** A test (`test_core_never_imports_a_specific_plugin`) fails if anyone hard-wires a plug-in into the core.

## The plug points

| Plug point | Where | What one file holds | Registered by |
|---|---|---|---|
| Role agent | `retail_intel/catalog/agents/*.toml` | goal, decisions, north star, guardrails, outcome, tools, data needed, KPIs, autonomy, budget, dashboard | being in the folder |
| Tool | `retail_intel/tools/*.py` | functions the agent may call | `@tool(...)` |
| KPI | `retail_intel/kpis/*.py` | one number for a dashboard | `@kpi(...)` |
| Connector | `retail_intel/connectors/*.py` | how to read (later write) one client system | `@connector` |
| Data stream | `retail_intel/catalog/streams.toml` | a standard data shape (the data contract) | being in the file |
| Client setup | `catalog/clients/demo.toml` (demo only) | connectors, switched-on agents, autonomy overrides | `CLIENT_CONFIG` setting |
| Question family | `retail_intel/catalog/question_families.toml` | trigger words, the standing check, example questions, status | being in the file (see `08_question_library.md`) |

The core that runs all of them: `platform/registry.py` (plug points), `platform/catalog.py` (reads agents, streams, clients; checks readiness), `platform/dashboards.py` (role pages and control tower), `agent/orchestrator.py` (one engine for every agent).

## How an agent is designed (the design discipline)

Every agent file must say, before any tools:
1. **decisions**: the decisions this role owns. Design around decisions, not job titles.
2. **cadence**: when the role acts (daily check, weekly review).
3. **north_star**: the one KPI it is judged on.
4. **guardrails**: KPIs it must not damage while improving the north star.
5. **outcome**: how we prove its advice paid off.

A ready agent missing any of decisions, north star or outcome fails validation.

## Add a new role agent (example: a returns analyst)

1. Copy `retail_intel/catalog/agents/margin_control.toml` to `returns_analyst.toml` in the same folder.
2. Change `id`, `name`, `role`, `goal`, `focus`, `decisions`, `cadence`, `north_star`, `guardrails`, `outcome`.
3. List the `tools` it may use (names from `python -m retail_intel agents` or the tool files), the `streams` it needs, and its `kpis`.
4. Set `finding_types` to the issues it owns (e.g. `["return_reason"]`).
5. Add it to `enabled_agents` in the client file.
6. Run `python -m pytest -q`. The test `test_every_template_is_valid` names any unknown tool, KPI or stream.
7. Run `python -m retail_intel agents`. It should say RUNS.

No Python needed if the tools and KPIs already exist.

## Add a tool

Add a function to a file in `retail_intel/tools/` (or a new file there):

```python
from ..platform.registry import tool

@tool("supplier_lead_times", "Average days from PO to goods received, per supplier.", {"supplier": {"type": "string"}})
def supplier_lead_times(conn, supplier=None):
    ...  # return a DataFrame or dict; numbers come from tested metric functions
```
Then add the tool name to the agents that may use it. Mark tools that write anything with `side_effect=True`.

## Add a KPI

```python
from ..platform.registry import kpi

@kpi("fill_rate_28d", "Order lines shipped complete, last 28 days", unit="pct", better="up")
def fill_rate_28d(conn):
    return ...
```

## Add a connector

Subclass `platform/connector.py: Connector`, list the `streams` it provides, implement `test` and `sync` (later `fetch` and `act`), and decorate with `@connector`. Set `status = "planned"` until it works; agents that need its streams then show as "missing data" rather than running on nothing.

## Add a question family

1. Add a `[families.<id>]` block to `catalog/question_families.toml`: role, label, status, tools, `match` (trigger words, a regular expression), `gather` (the check to run, with `$start`/`$end` for the question's period and `$?name` for optional settings), and 2+ example questions.
2. If no existing tool fits, add one first (see "Add a tool").
3. Run the tests. `test_certified_example_questions_reach_their_family` asks every example question and fails if routing does not reach your family.

## Definitions register

Every number depends on rules (margin target, dispatch promise, stuck-order days ...) and fixed definitions (net revenue, gross margin, stockout ...). They are listed in plain words in `platform/definitions.py` and on the Definitions page, where the owner signs them off and changes rules; every change is logged. A rule belongs in `config.DEFAULT_CONTEXT` and `definitions.RULES`, never as a constant inside a metric.

## Permanent history (Supabase or any Postgres)

`platform/state.py` keeps what people did (conversations, runs and cost, approvals, answers, rules, sign-offs, promotion requests) in one Postgres table, `ria_state`, keyed by client. Set `SUPABASE_DB_URL` (any Postgres connection string works; nothing Supabase-specific is used). Business data is never stored there; it is rebuilt from the client's systems. Tested against a real Postgres 16 server: restart, incremental saves, client separation, outage recovery.

## Autonomy ladder (per agent, per client)

L0 inform · L1 recommend (all agents today) · L2 draft (e.g. a draft PO) · L3 act after approval · L4 act within limits. Payments and vendor bank changes never go above L3. Set per client in the client file under `[autonomy]`.

## What is not modular yet (honest list)

- **Storage**: what people did is kept in Postgres (above); the analytical tables are still SQLite, rebuilt by the daily refresh. That is deliberate while data volumes are small.
- **The data layer** (reconcile, core, findings) still reads the CSV connector's raw tables. API connectors (Zoho Books first) will land data in the standard streams, and the core build will read streams instead.
- **Actions** (L2 and above) are not built: agents recommend; nothing writes to client systems.
- **Question routing** (`agent/router.py`) is shared by all agents. Role agents narrow it; they do not have their own routes yet.
