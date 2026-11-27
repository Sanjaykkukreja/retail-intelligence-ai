# Changelog (newest first)

Each build session adds one row. Like Pattern Lab's manifest: what changed, test result, what is still open.

| Date | Change | Tests | Open after this |
|---|---|---|---|
| 2026-11-25 | All AI moved to Claude: Sonnet 5.5 answers, Haiku 5.5 routes; prompt caching on system prompt and tools; cost per call incl. cache reads/writes; OpenAI adapter now optional; docs and guide updated | 42 passed | Live Claude API run |
| 2026-11-25 | Cloud-ready: app builds demo data on first start; reads AI key from Streamlit Secrets. Build guide written. | 40 passed | Live API run; screen layout check in browser |
| 2026-11-24 | Agent rebuilt on the five components (OpenAI Responses + Claude client, tools, memory, task manager, interface); routing, concurrent/sequential/fallback patterns, caching, budgets, grounding check, 30-question eval | 40 passed | Live API run |
| 2026-11-23 | Data layer: 3 messy synthetic sources, master SKU matching, quality gate, core tables, metrics, findings, daily briefing | 19 passed | Agent layer |
