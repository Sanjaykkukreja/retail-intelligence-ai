# 01 Architecture

## Layers (each one only reads the layer before it)
1. **Sources**: website (GA4-style), billing (Zoho-style), warehouse (WMS). Each has its own product codes.
2. **Ingest** (`ingest/`): copies files into `raw_*` tables untouched, so we can always see what a client system really sent.
3. **Reconcile** (`reconcile/`):
   - `sku_mapping`: one master SKU per product. Barcode and exact title are accepted automatically; order-evidence voting resolves renamed website items; look-alike titles are only *proposed* for a human. Human decisions go in `data/sku_map_overrides.csv`.
   - `order_matching`: every order across the three systems, flagging paid-not-dispatched, dispatched-not-billed, website-not-billed.
   - `quality`: severity info / warn / block. Block stops the run.
4. **Core** (`core/`): typed clean tables. Definitions are written once in `build_core.py`: *recognized line*, *net revenue* (ex-GST, after discount and refunds), *COGS* (batch landed cost), *gross margin*.
5. **Metrics** (`metrics/`): plain Python functions. The only source of numbers.
6. **Findings and briefing**: detectors turn metrics into a finding with rupee impact, a question for the owner, and an action. No AI needed.
7. **Agent** (`agent/`): one tool-using agent on the Claude Messages API: Sonnet 5.5 answers, Haiku 5.5 routes. It never calculates; see `03_agent_design.md`.
8. **Task manager** (`actions.py`, `daily.py`): recommendation lifecycle and the daily run.
9. **Platform** (`platform/`, `catalog/`): role agents as settings files on one engine; plug points for tools, KPIs and connectors; the data contract (streams); readiness checks; role dashboards and the control tower. See `06_platform_modules.md`.

## Decisions and why
| Decision | Reason |
|---|---|
| SQLite now, plain SQL | Zero setup for a non-developer. SQL is portable to Postgres when a client needs it. |
| One database per client, no multi-tenancy | Matches consulting delivery, simpler, safer for client data. Revisit only when productising. |
| Claude Messages API directly, no agent framework | One agent with tools needs only the API's tool use. The course makes the same call for OpenAI (Responses API over the Agents SDK); this build applies it to Claude. |
| No web API layer, no own MCP servers in V1 | Nothing needs them yet. Tools are plain functions, so they can be wrapped later. |
| Gross margin, not "contribution margin" | We do not have shipping, payment fees or marketing cost, so the label would overclaim. |
| Raw layer is never edited | Any disputed number can be traced to the client's own file. |
| Modular application, not separate services | Plug points give the flexibility; one deployable keeps it maintainable by one person. The sync worker is the first candidate to split out. |
| Agents are configuration (TOML), not code | 20 or 100 agents stay maintainable: one engine, one toolbox, one eval harness. TOML needs no extra library. |
| Agents designed around decisions, judged on a north star with guardrails | Outcomes, not activity: every agent shows whether its advice was accepted and whether the problem went away. |
| All AI on Claude, behind one adapter interface (`agent/models.py`) | One provider, one key, one bill. The interface still lets a model be swapped (Haiku, Sonnet, Opus) by changing one setting, chosen by the 30-question eval. |

## Known simplifications
Returned-to-origin and refunded units are not restocked. GST is a flat 18%. One warehouse, online only (the brief's five stores and transfers are deferred). Website counts are thinned ~7% to mimic real tracking loss.
