# 04 Roadmap, gates and client onboarding

## Phases (each ends with something runnable; do not skip a gate)
| # | Phase | Gate to pass |
|---|---|---|
| 1 | Data layer on synthetic data | DONE: 19 pipeline tests pass |
| 2 | Agent (5 components, course patterns) | DONE offline: 23 agent tests pass with a scripted model |
| 3 | Run on your machine | README steps 1-8 complete; send any error back |
| 3b | First live model run | `eval` summary sent back: routing, tool choice, key facts, grounding >= 90%; tokens, cache reads and cost per question recorded; Claude model chosen (Sonnet 5.5 unless Opus 5.5 is needed) |
| P1 | Platform: role agents on one engine, plug points, data contract, control tower | DONE: 20 platform tests; 4 role agents run, 2 planned agents show missing data |
| P2 | Saved history and spend (Supabase) | Conversations, runs, approvals survive a redeploy |
| P3 | Connector framework + Zoho Books (read-only, test org) | Streams filled by Zoho; mapping screen; agents run on Zoho data |
| P4 | Actions at L2-L3 (draft PO, approvals inbox, audit log) | Nothing reaches a client system without approval and a log entry |
| P5 | First finance agent on bills: PO-GRN-bill match | Catches planted overbilling on synthetic bills |
| 4 | **One real brand's exports** (CSV from website, billing, WMS) | SKU map accepted; revenue matches their own report within 0.5%; quality gate behaviour reviewed |
| 5 | Paid diagnostic with 3 brands | At least 2 pay for the findings; note which finding they acted on |
| 6 | Onboarding tooling | Only the parts rebuilt three times: mapping helper, connector templates, validation report |
| 7 | Productise | Only if phase 5 shows repeat demand |

## Real data checklist (phase 4)
Needed per source, as CSV: **website** item views, add-to-cart, checkouts, purchases (with transaction_id) per item per day, sessions per day, item list; **billing** invoice lines (order reference, SKU code, barcode, qty, MRP, discount, tax, status), refunds with reason; **warehouse** stock by SKU, batch and expiry per day, receipts with landed cost, dispatches with received and dispatched times. If column names differ, add a small renamer in `ingest/`; do not change the core.
First calls to make: who owns each system, how often does the data change, is there a barcode in both billing and warehouse, how are returns recorded, what discount rules exist.

## Discovery questions for a new client
1. Which systems hold orders, invoices, stock? Who can export them, how often?
2. Is there one product code everywhere? If not, is there a barcode?
3. What margin do you want to protect? What is your dispatch promise? What shelf-life rules apply?
4. What decisions do you make weekly (reorder, discount, ad spend) and from which numbers today?
5. Which past incident cost you most (stock-out, expiry write-off, bad sale)? We test whether the system would have caught it.

## Connection ladder (prefer top)
API/MCP, then read-only database user, then scheduled CSV to a shared folder, then manual upload. Always read-only; sign an NDA and a data-processing agreement first; keep personal customer data out (the pilot only needs customer_id).

## Test inventory (tests/test_pipeline.py, 19 tests)
Mapping accuracy and no wrong auto-accept; renamed items; planted data-quality counts; web tracking gap; daily revenue, COGS and orders vs ledger; core vs raw; stock-out events vs warehouse ledger; hero and live stock-out; dispatch-delay window; checkout-failure window; leakage SKUs; expiry vs ledger; slow movers vs independent calculation; gate blocks stale feed; gate blocks unmapped best seller; all tools and SQL lockdown; briefing content; answers survive reruns.

Agent tests (tests/test_agent.py, 23 tests): retry policy; Claude request format with prompt caching and cache-aware cost; Claude as default provider; Haiku routes while Sonnet answers; optional OpenAI adapter format; model-only-when-unsure classification; date windows; 30-question routing; fast path makes no model call; concurrent diagnosis then grounded answer; sequential expiry then markdown; invented figure challenged then flagged; repair turn fixes it; SQL fallback only after failure; tools outside the plan refused; budget stops a runaway loop; cache hit and invalidation; usage and cost logged; short- and long-term memory; approval through the agent; recommendation lifecycle and outcome check; daily job.

Platform tests (tests/test_platform.py, 20 tests): core never imports a plug-in; plug-ins register; duplicate names rejected; every agent file valid; a new agent is one file; bad files caught; readiness follows the data contract; client setup from Secrets; findings routed to the specialist; placeholders; role agent sees only its tools; off-role question stays inside the role; role core evidence always fetched and findings filtered; fast paths per role; budget per role; runs logged per agent; KPIs match planted problems; control tower and role dashboard; money format.

## Known limits
Synthetic data only. Estimates (lost sales, markdown outcomes) assume steady demand and an unmeasured elasticity. Thresholds (1.8x delay, 75% conversion drop) are tuned on this data and need re-tuning per client.
