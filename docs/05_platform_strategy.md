# 05 — Platform strategy: role agents for Indian retail and D2C

Status: agreed 28 Nov 2026. Build step 1 (role agents on one engine, plug points, control tower) was done the same day; see CHANGELOG and `06_platform_modules.md`.

## 1. The bet

- Build role agents for Indian retail and D2C. They work across the client's systems (website, billing, warehouse, marketplaces, books), which single-vendor agents such as Zoho Zia, SAP Joule or Ramp can't see together.
- Sell it as a productized service. The platform is one codebase. Each client gets a setup (connecting their data to agents), the agents they choose, and ongoing tuning.
- Python computes every number. Claude routes, explains and drafts. Humans approve actions.
- Even if it doesn't become a large company, it builds real, current AI product and agent skills.

### Where we can beat bigger vendors: design that carries operator judgment and proves outcomes

Good screens can be copied in a quarter. These are hard to copy:
1. **Roles designed around decisions**, not job titles or one app's screens. Each agent states the decisions it owns and its cadence.
2. **Every KPI tied to rupees.** One north-star number per agent, plus guardrail numbers it must not damage.
3. **The outcome loop is the product.** Recommend, decide, measure. Each agent's scorecard shows how much of its advice was accepted and whether the problems went away.
4. **Retail know-how in the Python rules:** festive seasons, expiry, case packs, marketplace fees. This draws on Sanjay's 24 years in retail and supply chain.

The platform enforces these. A ready agent without decisions, a north star and an outcome measure fails validation.

## 2. Design rule: modular, not monolithic

**What this means here:** one application, built from separate modules with clear plug points. It is *not* many small services yet. Microservices would add servers, network failures and deployment work that one person can't maintain. Modules with clean boundaries give the same flexibility. Any module can later move into its own service (the sync worker will be the first) without a rewrite.

Every module follows four rules:
1. It talks to others only through a declared interface, never by reaching into another module's tables or internals.
2. It registers itself in a registry. Core code never imports a specific connector, agent or action by name.
3. It can be switched on or off per client in config.
4. It ships with its own tests. The suite checks that core code imports no plug-in module.

**The test of success:** adding a new agent, connector or action means adding files. It never means editing core code.

### The modules (plug points)

| Module | What it is | Interface (what it must provide) | Example plug-ins |
|---|---|---|---|
| Connectors | Read and write one client system | `streams`, `test`, `sync`, `fetch`, `act` | CSV/Drive, Zoho Books, Shopify, GA4, Unicommerce, marketplace reports |
| Streams | Standard data shapes all agents share (the data contract) | Field list, freshness rule, personal-data flag | orders, order_lines, stock, receipts (GRN), purchase_orders, bills, payments, settlements, web_sessions |
| Storage | Where the thin core lives | `read`, `write`, `purge(older_than)` | SQLite (local, tests), Postgres/Supabase (cloud) |
| Metrics and heuristics | Python calculations on streams | Pure functions, exposed as tools and KPIs | run-rate, stockout, leakage, order-to-cash, PO–GRN–bill match, settlement gap |
| Tools | What an agent may call | Name, input schema, read-only or side-effect flag | `stock_position`, `order_to_cash_gaps`, `draft_po` |
| Agent templates | A role agent, defined as config (TOML) | decisions, cadence, north star, guardrails, outcome, goal, streams needed, tools allowed, KPIs, autonomy level, budget, dashboard spec | chief of staff, replenishment, margin control, revenue assurance |
| Runtime | One engine runs every template | router, plans, budgets, caching, grounding check, memory | (single shared module) |
| Actions | Things that change client systems | autonomy level, approval rule, audit log entry, undo if possible | draft PO, post journal, flag bill, send reminder |
| Interface | Screens | Pages generated from each template's dashboard spec, plus the control tower and approvals inbox | Streamlit now; a web front end later is a swap, not a rebuild |
| Client config | One client's setup | enabled agents, connector settings, autonomy levels | Kept in a private repo or Secrets, never in the public repo |

### How a request flows

Connector → stream (checked by the quality gate) → thin core store, or a live fetch → metrics and tools → runtime with the template → answer, dashboard or action proposal → approval → connector `act()` → audit log.

## 3. Data strategy: a hybrid

- **Thin core store:** a scheduled incremental sync of only the fields that active agents' contracts need. Small: tens of MB per client per year.
- **Live fetch:** used for lookups, and always right before an action.
- **Temporary staging:** used for heavy one-off analysis, and deleted automatically within 24 hours.
- **Minimal personal data:** hashed customer IDs by default, to fit the DPDP Rules.
- **Why not fetch everything live:**
  - Zoho Books limits: 100 calls/min, and 1,000–10,000 calls/day depending on plan.
  - Live data would skip the quality gate.
  - Speed.
  - Vendors gating agent access: SAP restricted outside agents from April 2026, and ServiceNow charges per action.

## 4. Autonomy ladder (set per agent, per client)

L0 inform · L1 recommend · L2 draft · L3 act after approval · L4 act within limits.

Payments and vendor bank changes never go above L3.

## 5. Roles

- **P1:** replenishment/inventory planner, merchandising and margin controller, chief of staff (control tower), finance reconciliation.
- **P2:** operations and fulfilment.
- **Later or avoid:** sales outreach (crowded, consent risk), customer support, HR.

**Finance focus.** These are agents that need retail data generic AP tools lack:
- order-to-cash reconciliation (built)
- PO–GRN–invoice match
- marketplace settlement reconciliation
- margin and discount control
- working capital (slow stock, expiry, receivables)
- B2B collections

Post results into the client's existing books or AP tool. Don't replace it.

## 6. Market and pricing

- **Best fit:** brands with ₹50–500 Cr revenue, 3+ systems and a small ops/finance team.
- **Smaller businesses:** a fixed 3-agent pack.
- **Pricing:** platform fee + per-agent fee + one-time setup, with an optional share of measured savings.

## 7. Risks

- **Solo capacity:** a technical partner is needed by client 3.
- **Vendor API gating:** keep CSV/Drive import as a permanent fallback.
- **Liability for actions:** approvals, an audit log and contract terms.
- **Real data breaks demo assumptions:** budget 2–3 weeks per first client.

## 8. Build order

1. **Modular refactor** (DONE 28 Nov):
   - tool, KPI and connector registries
   - 4 TOML role agents on one engine, plus 2 planned finance agents
   - data contract and readiness checks
   - control tower, role dashboards, agent catalog

   **Next: Supabase** for conversations, runs, approvals and spend, via a storage interface that works with both SQLite and Postgres.
2. **Connector interface and streams.** CSV/Drive connector, a stream-mapping screen, then Zoho Books on a free test organisation.
3. **Actions module at L2–L3:** draft PO, approvals inbox, audit log.
4. **First bills agent:** PO–GRN–invoice match on synthetic receipts and bills.
5. **UI design pass.** The control tower itself already exists from step 1.
6. **Two pilot clients.**

## Sources

- Gartner agentic AI predictions: https://www.rcrwireless.com/20250627/business/agentic-ai
- Zoho Zia Agents: https://www.businesswire.com/news/home/20250204066995/en/
- Ramp AP agents: https://ramp.com/blog/ramp-ap-agents-announcement
- India AP tools: https://open.money/blog/?p=24806
- DPDP Rules 2025: https://yourstory.com/2025/11/digital-personal-data-protection-dpdp-rules-2025-rollout-over-18-months
- Agent pay-to-play: https://www.pymnts.com/artificial-intelligence-2/2026/servicenow-sap-and-workday-make-ai-agents-pay-to-play/
- Supabase pricing: https://supabase.com/pricing.md
- Zoho Books API: https://www.zoho.com/books/api/v4/introduction/
