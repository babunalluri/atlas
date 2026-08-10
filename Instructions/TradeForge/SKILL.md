# TradeForge — Skill Reference

Atlas skill pack for TradeForge Academy ops + customer assistance.

- **Toolkit (TradeForge APIs):** `tools/tradeforge_toolkit.py` (`TradeForgeToolkit`) — platform mocks until TF API is live
- **Toolkit (Groww demat/live):** `tools/groww_toolkit.py` — Groww Trading API (`api.groww.in`); see `tools/GROWW.md`
- **Toolkit (Zerodha Kite):** `tools/kite_toolkit.py` — Kite Connect v3 (`api.kite.trade`); see `tools/KITE.md` (optional / multi-broker)
- **Teams:** `teams/learning.md` (end-user Learn via KB), `teams/customer_support.md`, `teams/ops_desk.md`
- **Agents:** see table below
- **Workflows:** learn (KB) → publish → paper → live approval → kill switch
- **PRD:** TradeForge Academy v2.1 (modules A–E, UC-1 / UC-2 / UC-3)
- **Learn policy:** Do **not** rebuild Classplus; end-user education uses **Atlas Knowledge Base**.

---

## Personas → Atlas agents

| Persona (PRD) | Agent file | Primary tools | Mutating? |
|---|---|---|---|
| Signal Publisher | `agents/signal_publisher.md` | list drafts, publish, suppress, push preview | yes (publish/suppress) |
| Param Editor | `agents/param_editor.md` | get/update schema, draft params, diff | yes (draft save) |
| Feed Monitor | `agents/feed_monitor.md` | feed health, stale signals | no |
| Compliance / Live Approver | `agents/compliance_officer.md` | live queue, approve/deny, kill switch | yes |
| Learning Guide | `agents/learning_guide.md` | Atlas Knowledge Base (retrieve/answer) | no |
| Customer Concierge | `agents/customer_concierge.md` | signals/paper + Groww holdings/positions/margin/orders | paper + Groww place/cancel = yes (HITL) |

Operators **cannot** approve live or trip kill switch (PRD RBAC). Those stay on Compliance / Admin agents only.

---

## Teams

| Team | Members | Mission |
|---|---|---|
| Learning (end user) | Learning Guide | Teach from **KB** — no Classplus rebuild |
| Customer Support | Concierge | Signals → paper → Groww / live status |
| Ops Desk | Publisher + Param Editor + Feed Monitor + Compliance | UC-3 publish/suppress; UC-2 governance |

---

## Workflows

| Workflow | UC | File |
|---|---|---|
| Learn via Knowledge Base | Module A (Atlas) | `workflows/learn_via_kb.md` |
| Publish / suppress signal | UC-3 | `workflows/publish_signal.md` |
| Signal → paper order | UC-1 | `workflows/paper_from_signal.md` |
| Live approval + arm | UC-2 | `workflows/live_approval.md` |
| Kill switch / auto-disarm | UC-2 | `workflows/kill_switch.md` |

---

## Tool capability map

| Capability | Read / mutate | Used by |
|---|---|---|
| `list_signals` | read | Concierge, Publisher, Feed |
| `get_signal` | read | Concierge, Publisher |
| `list_draft_signals` | read | Publisher |
| `publish_signal` | mutate | Publisher |
| `suppress_signal` | mutate | Publisher |
| `preview_push` | read | Publisher |
| `get_param_schema` | read | Param Editor |
| `update_param_draft` | mutate | Param Editor |
| `diff_param_versions` | read | Param Editor |
| `get_feed_health` | read | Feed Monitor |
| `list_live_requests` | read | Compliance |
| `approve_live_request` | mutate | Compliance |
| `deny_live_request` | mutate | Compliance |
| `get_algo_status` | read | Compliance, Concierge |
| `arm_algo` / `disarm_algo` | mutate | Compliance (or customer via product UI; agent only if policy allows) |
| `kill_switch` | mutate | Compliance / Admin only |
| `get_paper_hub` | read | Concierge |
| `place_paper_order` | mutate | Concierge |
| `list_positions` | read | Concierge |
| `list_strategy_packs` | read | Concierge |
| `get_account_health` | read | Concierge, Compliance |

All mutating calls require Atlas HITL approval when attached as sandbox capabilities.

---

## Hard rules (every agent)

1. Never invent fills, approvals, or broker tokens.
2. Never show or log OAuth / refresh tokens.
3. Suppressed signals must not be described as active to customers.
4. Live arm requires: live approved + valid demat token + risk caps + ≥1 pack.
5. Customer live trading demat is **Groww** — guide Groww OAuth / reconnect; do not push other brokers unless Ops policy says otherwise.
6. SEBI RA/IA disclosures: remind before live paths; do not bypass Compliance.
7. Prefer tool results over memory for prices, P&L, and approval status.
