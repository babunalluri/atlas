# Stock Broker — Atlas Instructions

Source of truth for product scope: `~/Downloads/stock-broker-prd.html` (Stock Broker PRD v2.1).

This folder is the **Atlas functional layer** for Stock Broker: team prompts, agent prompts, workflows, and Python tool contracts. Paste / load these into Atlas Team Builder, Agent Builder, and Tool Builder (`tenant_python`).

## What this covers vs the full PRD

| PRD area | Satisfied by Instructions? | Notes |
|----------|----------------------------|--------|
| Ops desk copilots (publish, params, feed, compliance) | **Yes (prompts + tools)** | UC-3 style operator assistance |
| Customer concierge (learn / signals / paper Q&A) | **Yes (prompts + tools)** | Guided flows; not the Flutter app |
| Signal / paper / demat / algo **API contracts** | **Partial** | Toolkit stubs + canonical paths; needs live Stock Broker API |
| Flutter customer app (28 screens) | **No** | Separate mobile codebase |
| Ops Console React UI (14 screens) | **No** | Separate web app (or Atlas UI only for agents) |
| Academy content / Learn | **Atlas Knowledge Base** via Learning team — **do not rebuild Classplus** | Attach curated KB to Learning Guide |
| OpenBull execution + **Groww** demat OAuth | **No** | Private execution plane; tools call adapters when APIs exist. Customer trading broker = Groww (product decision; HTML PRD listed Groww as gap — overridden here). |
| SignalComputeWorker / N-param engines | **No** | Lives in Stock Broker backend workers; example packs referenced in SKILL |
| Razorpay, FCM, Postgres/Redis product DB | **No** | Product infrastructure |

**Bottom line:** Instructions do **not** ship the full Stock Broker product. They ship the Atlas agents/teams/workflows/tools that operate *against* Stock Broker once APIs are available.

## Layout

```text
Instructions/StockBroker/
  README.md                 # this file
  SKILL.md                  # index: personas, packs, tool map
  teams/                    # team orchestration prompts (.md)
  agents/                   # per-agent system prompts (.md)
  workflows/                # step contracts for key UCs (.md)
  tools/                    # Python toolkit for Atlas Tool Builder (.py)
```

## Auto-provision mapping (domain: `stock_broker`)

When a super admin or org admin creates a tenant with domain **Stock Broker**, Atlas auto-provisions:

| Kind | Slug | File |
|------|------|------|
| Agent | `signal-publisher` | `agents/signal_publisher.md` |
| Agent | `param-editor` | `agents/param_editor.md` |
| Agent | `feed-monitor` | `agents/feed_monitor.md` |
| Agent | `compliance-officer` | `agents/compliance_officer.md` |
| Agent | `learning-guide` | `agents/learning_guide.md` |
| Agent | `customer-concierge` | `agents/customer_concierge.md` |
| Team | `ops-desk` | `teams/ops_desk.md` |
| Team | `customer-support` | `teams/customer_support.md` |
| Team | `learning` | `teams/learning.md` |
| Workflow | `publish-signal` | `workflows/publish_signal.md` |
| Workflow | `live-approval` | `workflows/live_approval.md` |

Additional workflow runbooks in `workflows/` (paper, kill switch, learn via KB) can be loaded manually.

## How to load into Atlas

1. Create agents from `agents/*.md` (instructions field).
2. Create teams from `teams/*.md`; attach member agents.
3. Create Editable Python tools:
   - `tools/stock_broker_toolkit.py` — Stock Broker platform APIs (mock until live).
   - `tools/groww_toolkit.py` — Groww broker (`api.groww.in`); see `tools/GROWW.md`. Allowlist host + bind access token / api_key+secret.
   - `tools/kite_toolkit.py` — Zerodha Kite Connect (`api.kite.trade`); see `tools/KITE.md`. Needs form-urlencoded sandbox proxy + api_key/access_token.
4. Validate → Publish. Attach Groww tool to Concierge / Compliance; Stock Broker toolkit to Ops agents.
5. Use `workflows/*.md` as runbooks for operators or as workflow node descriptions.

## Canonical API namespaces (from PRD)

| Domain | Path |
|--------|------|
| Signals (customer) | `/v1/signals/*` |
| Signals / params (ops) | `/v1/ops/signals/*`, `/v1/ops/params/*` |
| Paper | `/v1/paper/*` |
| Live | `/v1/live/*` |
| Demat / brokers | `/v1/demat/*`, `/v1/brokers/*` |
| Algo | `/v1/algo/arm\|disarm\|status\|packs\|deploy` |
| Ops | `/v1/ops/*` |

## Example strategy packs (PRD §0f)

`EX-SMA-X` · `EX-RSI-MR` · `EX-MACD-M` · `EX-VWAP-B` · Breakout · Crypto funding
