# Retail Intelligence AI - start here

Consulting-first AI analyst for beauty D2C brands, built entirely on Claude. Pilot business: GlowBasket (synthetic data from three messy systems: website, billing, warehouse).

## Status (25 Nov 2026)
- Phase 1 (data layer) and phase 2 (agent on the five course components) are done: 42 automated tests pass.
- All AI runs on Claude: Claude Sonnet 5.5 answers, Claude Haiku 5.5 classifies unclear questions, prompt caching cuts repeat cost. One key: `ANTHROPIC_API_KEY`.
- Phase 3 is next: put the app live on Streamlit Community Cloud from GitHub.
- Not yet tested: live Claude API calls (needs your key) and the screen layout in a browser.

## Where everything lives
| What | Where |
|---|---|
| Code (unzipped, latest) | Google Drive: `G:\My Drive\AI projects\SMB Automation\retail-intelligence-ai` |
| Code (zip) | Same folder: `retail-intelligence-ai-2026-11-25-UNZIP-ME.zip` |
| Build and deploy guide (step by step, phone-friendly) | Claude Doc: https://claude.ai/artifact/7Cr1BkiX2gR5BAQ1ptd6xk |
| Design docs, README, changelog, sample briefing | This project |

Older copies in the Drive folder (`retail-intelligence-ai.zip`, `03_agent_design.md` at the top level) are from 24 Nov and are superseded.

## How a build session starts
1. Open the chat inside this project, with the SMB Automation folder connected in the Claude desktop app. Claude reads the code from the folder directly; no zip upload needed. Without the folder, attach the latest zip.
2. Claude runs the 42 tests and confirms the state before changing anything.
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
