# 07 Actions and the assistant (design, 28 Nov 2026; not built yet)

## Actions: a small catalog of generic action types, configured into playbooks

Same pattern as agents: an action type is code (a plug-in, written once). A playbook is configuration that binds a trigger to a configured action.

### Action types (plug-ins)

| Type | What it does | Risk | Default autonomy |
|---|---|---|---|
| notify | Email (later WhatsApp, Slack) to a person or role from the directory | Low | L3 act after approval |
| handoff | Pass items to a department as a tracked task: owner, due date, attached list | Low | L3 |
| create_document | Draft a PO, debit note or journal in the client's system (via connector) or as a file | Medium | L2 draft |
| update_record | Change a status or field in a client system | Medium | L2 |
| message_customers | Templated message to customers (consent, DPDP, WhatsApp template rules) | High | L3, always reviewed, built last |

### Playbook (one per trigger)

```toml
[[playbooks]]
id = "stuck_paid_orders"
agent = "revenue_assurance"
trigger = { finding = "paid_not_dispatched" }   # or { kpi = "...", above = 20 } or { schedule = "daily 09:00" }
action = "handoff"
to = "role:warehouse_lead"                        # from the people/roles directory, never typed by the model
attach = "order_to_cash_gaps.paid_not_dispatched" # Python builds the list (CSV); the model only writes the note
due_days = 2
autonomy = 3
outcome = "kpi:paid_not_dispatched falls"
```

### How an action runs

Trigger fires -> the agent drafts (Claude writes the note; Python attaches the data) -> goes to the approvals inbox, by autonomy level -> the executor sends or creates -> audit log entry -> outcome check feeds the agent's scorecard.

### Safety rules
- Recipients come only from a people/roles directory, never from model text.
- Every action has an idempotency key, so the same thing is never sent twice.
- Dry-run mode per client.
- Rate limits.
- Everything is logged: who approved, what was sent, when.
- Payments and vendor bank changes never go above L3.
- An agent is never judged on a number it can move by acting alone: "stuck orders fell" counts only if refunds and cancellations did not rise (guardrails).

### Depends on
Persistent storage (built: `platform/state.py`) for the action queue, the audit log and the directory. Email needs a sending account (a Gmail app password over SMTP, or a transactional service such as Resend), stored in Secrets.

## The assistant: one more agent on the same engine, not a separate product

- **What it is:** a catch-all assistant that reads across all agents' data and the client's documents: SOPs, vendor contracts and rate cards, policies, past reports.
- **Documents** become a tool, `search_documents`. Files are uploaded, split, embedded and stored (pgvector in Postgres). Answers cite the document and page.
- **Delegation:** the assistant can call specialist agents as tools, instead of telling the owner to go and ask them.
- **Shared tool:** the same document tool helps other agents. For example, the bills matcher checks a vendor bill against the contract's rate card.

## Order
1. Supabase (built 28 Nov; live once the secret is added)
2. Actions v1: notify plus handoff, with an approvals inbox and an audit log
3. Zoho Books connector
4. Assistant with documents and delegation
5. create_document (draft PO)
6. Customer messaging
