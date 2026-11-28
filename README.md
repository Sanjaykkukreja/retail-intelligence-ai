# Retail Intelligence AI (pilot: GlowBasket)

A platform of **role agents** for beauty D2C and retail brands: a Chief of staff, a Replenishment planner, a Margin controller and a Revenue assurance (finance) agent, all running on one shared engine, with a control tower over them. Each agent is a settings file that states the decisions it owns, the one number it is judged on, the numbers it must not damage, and how we prove its advice paid off. New agents, tools, KPIs and connectors plug in as files (see `docs/06_platform_modules.md`).

Underneath, the data layer joins three systems that name every product differently (website, billing, warehouse), checks the data, tells the owner what needs attention in rupees, tracks each recommendation through approval to outcome, and answers free-form questions through a tool-using agent.

Built on the standard five agent components (reasoning model, tools, memory, task manager, interface), all on Claude: Claude Sonnet 5.5 reasons and answers, Claude Haiku 5.5 routes unclear questions. See `docs/03_agent_design.md` for how each maps to the course.

**Tested (65 automated tests):** the plug-in structure (core never hard-wires a plug-in), agent catalog validity, data-contract readiness, role agents staying within their tools, role dashboards and control tower; data pipeline and revenue tie-out; detection of every planted problem; the agent's routing, concurrent / sequential / fallback patterns, budgets, caching, grounding check, memory, approvals; recommendation lifecycle; daily job; Claude API request format, prompt caching and cost (against fakes).
**Not yet tested:** live calls to the Claude API (needs your API key), and the screen layout in a browser.
**All data is synthetic.** It proves the logic, not that a real client's data will behave.

## Setup, step by step (Windows or Mac)

1. **Install Python 3.11 or newer** from python.org/downloads. On Windows tick **"Add python.exe to PATH"** on the first screen.
2. **Unzip** this folder somewhere easy, e.g. `Documents/retail-intelligence-ai`.
3. **Open a terminal in that folder.**
   - Windows: open the folder in File Explorer, click the address bar, type `cmd`, press Enter.
   - Mac: open Terminal, type `cd ` (with a space), drag the folder into the window, press Enter.
4. **Create and switch on a private Python environment:**
   - Windows: `python -m venv .venv` then `.venv\Scripts\activate`
   - Mac: `python3 -m venv .venv` then `source .venv/bin/activate`
   `(.venv)` appears at the start of the line. Repeat the activate command in every new terminal window.
5. **Install packages:** `pip install -r requirements.txt`
6. **Build everything:** `python -m retail_intel all` (about a minute; ends by printing the briefing).
7. **Run the tests:** `python -m pytest` and expect `42 passed` (about 1.5 minutes).
8. **Open the screen:** `streamlit run app/streamlit_app.py`. A browser tab opens. Stop with Ctrl+C.
9. **Turn on the AI analyst** (everything else works without it).
   a. Create a Claude API key in the Claude Console (platform.claude.com): API Keys, Create Key. Add credit under Billing. API use is billed separately from a Claude chat plan.
   b. Set it. Windows: `setx ANTHROPIC_API_KEY "sk-ant-..."`, then close and reopen the terminal and repeat step 4's activate. Mac: `export ANTHROPIC_API_KEY="sk-ant-..."`.
   c. Models are already set: `claude-sonnet-5-5` answers, `claude-haiku-5-5` routes. To change, set `ANTHROPIC_MODEL` or `ANTHROPIC_ROUTER_MODEL` (e.g. `claude-opus-5-5` for harder reasoning at twice Sonnet's price).
   d. Cost per question is shown automatically, including prompt-cache savings. Prices live in `retail_intel/agent/models.py` (`PRICES`), checked 25 Nov 2026; update them if Anthropic's pricing page changes.
   e. Try: `python -m retail_intel ask "Why did cancellations jump in mid-September?"`
   f. Then score it: `python -m retail_intel eval` and **send me the summary line**. That is the first live test.

If `python` is not found, try `py` (Windows) or `python3` (Mac). If anything errors, copy the full message back to Claude.

## Everyday commands
| Command | What it does |
|---|---|
| `python -m retail_intel daily` | Refresh data, update findings and tasks, check outcomes, save the briefing to `data/briefings/` |
| `python -m retail_intel brief` | Print today's briefing |
| `python -m retail_intel agents` | Which agents run for this client, and what data the others are missing |
| `python -m retail_intel chat --agent replenishment` | Talk to one role agent (or leave out `--agent` for the general analyst) |
| `python -m retail_intel recs` | Recommendations and their status |
| `python -m retail_intel approve 3 "PO raised"` | Approve (also `reject`, `done`) |
| `python -m retail_intel questions` / `answer ID "text"` | Questions for you, and your answers |
| `python -m retail_intel eval` | Score the agent on 30 test questions |

## Run it every morning
- Mac: `crontab -e`, add `30 7 * * * cd /full/path/retail-intelligence-ai && .venv/bin/python -m retail_intel daily`
- Windows: Task Scheduler, Create Basic Task, Daily 7:30, Program `C:\full\path\retail-intelligence-ai\.venv\Scripts\python.exe`, Arguments `-m retail_intel daily`, Start in `C:\full\path\retail-intelligence-ai`.
The daily run makes no AI calls, so it costs nothing in API fees.

## Layout
```
retail_intel/
  ingest/ reconcile/ core/ metrics/   data layer: raw copy -> master SKU + order matching + quality gate -> clean tables -> tested metrics
  findings.py briefing.py             what needs attention, in rupees, with a question and an action
  actions.py daily.py                 task manager: recommendation lifecycle; the daily job
  platform/                           plug points (registry), catalog loader + readiness, connector plug, dashboards
  catalog/                            agents/*.toml (role agents) | streams.toml (data contract) | clients/demo.toml
  tools/ kpis/ connectors/            plug-ins: one file per area, picked up automatically
  agent/                              models (API client) | toolbox runtime | memory | router (decision matrix) | orchestrator | grounding | prompts
  eval/                               30-question evaluation set and runner
app/ tests/ docs/ data/ (generated; safe to delete, rebuilt by step 6)
```
