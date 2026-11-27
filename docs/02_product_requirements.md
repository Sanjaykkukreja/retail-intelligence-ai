# 02 Product requirements (V1)

## Who and what
Owner of a beauty D2C brand (target roughly Rs 5-50 Cr) with a website, a billing system and a warehouse, and no analytics team. Job to be done: *tell me what to do today, in rupees, and ask me when you are unsure.*

## In scope for V1
1. Daily briefing: yesterday's numbers versus normal, top actions ranked by rupee impact, open questions, data health.
2. Beauty-specific findings: stock-outs while demand continues (lost sales), batches that will not sell before expiry, slow movers, discount leakage below target margin, dispatch delays, checkout failures, return reasons (shade mismatch), orders paid but never shipped, shipments never invoiced.
3. Owner questions with saved answers (the agent asks, the owner answers, answers persist).
4. Quality gate and human approval of product mappings.
5. Recommendations only. The owner approves every action.

## Out of scope for V1
Writing back to any client system, marketing spend (CAC/ROAS), multi-store transfers, customer cohorts, forecasting, multi-tenant SaaS, live connectors.

## Acceptance (all automated)
Revenue and COGS match the independent ledger per day; every planted problem is detected; no wrong product match is auto-accepted; the pipeline refuses stale or unmapped data; the SQL tool cannot read raw tables or change anything.

## Where the build differs from the original brief
| Brief | This build | Why |
|---|---|---|
| SaaS at Rs 2-20k a month | Not decided. Pipeline serves both | Onboarding messy systems is the real cost; Rs 2-3k a month cannot pay for it. Test a paid diagnostic first. |
| All D2C categories | Beauty only | Batch and expiry logic is the moat; other categories dilute it. |
| Six agents | One agent plus code | Most "agents" (ingest, reconcile, metrics) are deterministic jobs. |
| Five stores and transfers | Online and one warehouse | Not needed to prove value; add after a real client asks. |
| 95% SQL accuracy target | Core numbers via tested functions, SQL only as fallback | One wrong number in twenty destroys an owner's trust. |
| Multi-tenant database | One database per client | Simpler and safer for consulting. |
| Section 13 co-founder pitch | Not addressed | If a partner is intended, ownership and IP need a written agreement first. |

## Open decisions for the owner of this project
1. Consulting first, SaaS first, or both? (The code supports either; pricing and sales do not.)
2. Which three beauty brands will share a real export for the first diagnostic?
3. Which Claude model answers (Sonnet 5.5 by default, or Opus 5.5) after running the 30-question eval on each?
