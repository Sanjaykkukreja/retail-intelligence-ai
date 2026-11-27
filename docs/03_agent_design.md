# 03 Agent design, mapped to the course ("Building Your First AI Agent")

## The five components
| Component | Where | What it does | Tested how |
|---|---|---|---|
| **1. Reasoning model** | `agent/models.py` | **Claude Messages API with tool use.** Claude Sonnet 5.5 answers; Claude Haiku 5.5 classifies unclear questions. Timeouts, our own retry policy (exponential backoff + jitter, only 429/5xx/529/timeouts), prompt caching of the system prompt and tool list, usage and cost per call including cache reads and writes | Retry policy; request/response format checked against fakes of both SDKs. **Not yet run against the live APIs.** |
| **2. Tools** | `agent/tools.py` | 23 tools: 19 read-only (tier 0), 1 SQL fallback (tier 1), 3 owner actions with side effects (record answer, approve/reject, change a business rule). Cached per data version; every call logged | All tools run; SQL lock-down; cache hit and invalidation |
| **3. Memory** | `agent/memory.py` | Short-term: last 6 turns of the chat session (stored). Long-term: business rules, owner's answers, owner's decisions, injected into every prompt | Session replay; an owner answer appears in the next prompt |
| **4. Task manager** | `agent/orchestrator.py` (per question) and `actions.py` (per recommendation) | Per question: plan, steps, budgets, stop rules. Per recommendation: proposed, then approved or rejected, then done, then outcome checked. `daily.py` runs the day | Lifecycle, no overwrite of decisions, outcome check, daily job |
| **5. Interface** | `app/streamlit_app.py`, `cli.py` | Chat with memory, briefing, recommendations with approve/reject, findings and questions, trends, stock, data health, agent activity (tokens, cost) | Page logic run against a stub; **layout not yet seen in a browser** |

The course teaches on OpenAI; this build runs entirely on Claude. The course's point still holds: "for a single agent that wields several tools, the Responses API is the right base; the Agents SDK is for several collaborating agents." The Claude equivalent is the Messages API with tool use, called directly with no framework. An optional OpenAI adapter remains in `models.py` only to compare with course examples; it is not installed or used by default.

## How one question flows
```
question -> classify (rules first, model only if unclear) -> plan from the decision matrix
  -> fast path? answer with no model call (briefing, data health, list of pending decisions)
  -> gather evidence: concurrent (independent tools in parallel) and/or sequential (step 2 uses step 1)
  -> model reasons with a NARROW tool set; SQL fallback unlocked only after a tool fails or comes back empty
  -> answer -> grounding check (every figure must exist in a tool result) -> one repair turn if not
  -> log run (tokens, cost, tools, latency, grounded) -> save to memory
```

## Orchestration patterns (course reading: "Architectural Blueprints")
| Pattern | Used for | Example |
|---|---|---|
| Sequential | A follow-up that needs the first result | Expiring batches, then markdown scenarios for the worst batch |
| Conditional / fallback | Cheap and specific first, broader only on failure | Metric tools first; `run_readonly_sql` only after an error or empty result. Classification: rules first, model only when the rules are unsure |
| Concurrent | Several independent sources for one problem | "Why did sales fall?" pulls sales vs the prior period, funnel, dispatch, stock-outs, discounts and findings in parallel |

## Decision matrix (classification -> plan)
| Intent | Pattern | Evidence gathered up front | Tools the model may add |
|---|---|---|---|
| briefing | fast path | briefing | none |
| data_quality | fast path | quality checks | none |
| recommendations | fast path, or conditional for "approve 3" | pending list | list, decide |
| diagnose ("why") | concurrent | 6-7 sources | all read tools |
| sales | conditional | sales summary or day comparison | sales tools |
| inventory | conditional | stock position (+ stock-out history) | stock tools |
| expiry | sequential | expiry, then markdown | expiry, markdown, slow movers |
| pricing | conditional | discount leakage | pricing tools |
| operations | concurrent | dispatch speed, findings, data checks | same |
| web, returns | conditional | funnel / return reasons | own tools |
| owner_input | conditional | open questions | record answer, update rule |
| general | conditional | none | all read tools; SQL after a failure |

## Cost management (course reading: "Performance and Cost Management")
Cost hierarchy, cheapest first: **no model call** (fast path) < **cached tool** < **metric tool** < **Haiku classification** < **Sonnet step** < **SQL fallback**.
- **Right model per job:** Haiku 5.5 ($0.10 / $0.50 per million input / output tokens, "from" price) only classifies questions the keyword rules cannot place; Sonnet 5.5 ($2 / $10) reasons and answers. Prices as listed on Anthropic's models page, 25 Nov 2026.
- **Prompt caching:** the system prompt and tool list are marked `cache_control`. Within 5 minutes, repeat calls read them at 0.05x the Sonnet input price; writing the cache costs 1.25x once. The minimum cacheable size for Sonnet and Haiku 5.5 is 512 tokens, which the tool list exceeds.
- **Early termination:** the loop stops when the model answers; the prompt tells it to stop when the evidence is enough.
- **Smart caching:** read-only tool results are cached per data version; a new pipeline run clears them.
- **Narrow tool sets:** each intent offers only its tools, so prompts are shorter and tool choice is better.
- **Budgets:** max 6 model steps, 10 tool calls, 200k input tokens, optional dollar cap per question.
- **Daily run uses no model at all.** API cost arises only when someone asks a question.

## Response synthesis
Sources are labelled [S1], [S2] ... and the prompt requires a label beside every figure. Authority order: tested metrics, then findings, then SQL. The grounding check (`agent/grounding.py`) extracts every figure, including Rs 1.58 L or 48%, and rejects any not found in a tool result. The model gets one chance to repair; after that the answer is shown with a visible warning.

## Evaluation (`eval/`)
30 owner questions with acceptable intents, required tools and key facts. `python -m retail_intel eval` scores routing, tool choice, whether the key fact is mentioned, grounding, tokens (and how many came from cache), cost and latency. Run it with Sonnet 5.5, then with `ANTHROPIC_MODEL=claude-opus-5-5`, and keep the cheaper one that passes.
**Honest caveat:** the 30 questions and the routing rules were written together, so 100% routing there is flattering. On 15 differently worded questions run once without tuning, rules alone routed 8/15. Unclear questions go to the model to classify, and misroutes fall back to the general plan with all tools.

## Not built yet
Streaming answers; parallel tool calls requested by the model in one turn (they run one after another); feeding the owner's answers back to change a finding's severity; a model-graded quality score in the evaluation.
