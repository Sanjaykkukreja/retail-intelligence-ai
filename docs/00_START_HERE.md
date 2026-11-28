# Retail Intelligence AI - start here

Consulting-first AI analyst for beauty D2C brands, built entirely on Claude. Pilot business: GlowBasket (synthetic data from three messy systems: website, billing, warehouse).

## Status (28 Nov 2026)
- Live on Streamlit Community Cloud with Claude (Sonnet 5.5 answers, Haiku 5.5 routes).
- Platform step 1 done: four role agents (Chief of staff, Replenishment planner, Margin controller, Revenue assurance) on one engine, plus two planned finance agents; control tower, role dashboards, agent catalog. 65 automated tests pass.
- Next: Supabase so conversations, approvals and spend survive a redeploy; then the Zoho Books connector.
- Not yet checked: the new screens in a real browser (they run against a stand-in in tests).

## Where everything lives
| What | Where |
|---|---|
| Code (latest is GitHub; Drive copy is from 27 Nov) | Google Drive: `G:\My Drive\AI projects\SMB Automation\retail-intelligence-ai` |
| Code (zip) | Same folder: `retail-intelligence-ai-2026-11-25-UNZIP-ME.zip` |
| Code on GitHub (public; deploys to Streamlit from here) | https://github.com/Sanjaykkukreja/retail-intelligence-ai |
| Build and deploy guide (step by step, phone-friendly) | Claude Doc: https://claude.ai/artifact/7Cr1BkiX2gR5BAQ1ptd6xk |
| Design docs, README, changelog, sample briefing | This project |
| Platform strategy (role agents, modular design, market) | `docs/05_platform_strategy.md` |
| How to add an agent, tool, KPI or connector | `docs/06_platform_modules.md` |

Older copies in the Drive folder (`retail-intelligence-ai.zip`, `03_agent_design.md` at the top level) are from 24 Nov and are superseded.

## How a build session starts
1. Open the chat inside this project, with the SMB Automation folder connected in the Claude desktop app. Claude reads the code from the folder directly; no zip upload needed. Without the folder, attach the latest zip.
2. Claude runs the tests (65 today) and confirms the state before changing anything.
3. One goal per session; close with all tests passing and one CHANGELOG row.

## Locked rules (full list in the guide)
- Raw tables are never edited; formulas live only in core/ and metrics/.
- The AI never calculates; every figure must trace to a tool result.
- Finding and recommendation IDs never change.
- A new tool ships with its test and an eval question in the same session.
- The Claude API key lives only in Streamlit Secrets.

## How the course maps onto Claude
The Coursera course teaches on OpenAI. Its patterns carry over unchanged: one agent calling tools through the API directly (Claude Messages API in place of the Responses API), classify and route, sequential / fallback / concurrent orchestration, caching, early stop, budgets. See `03_agent_design.md`.

## Open decisions
- Consulting first, SaaS first, or both.
- Which three beauty brands will share real exports.
- Which Claude model answers (Sonnet 5.5 by default, or Opus 5.5), decided by the 30-question eval.
