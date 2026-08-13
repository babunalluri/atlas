# Stock Broker — Atlas Instructions

Source of truth for product scope: `~/Downloads/stock-broker-prd.html` (Stock Broker PRD v2.1).

This folder is the **Atlas end-user desk** for Stock Broker: three workspace chats, agent prompts, workflows, and Python tool contracts.

## End-user workspace (exactly three chats)

| Chat | Team slug | Agent | Does |
|------|-----------|-------|------|
| Learning | `learning` | Learning Guide | KB + generic market questions (no price predictions) |
| Paper trading | `paper-trading` | Paper Trader | Signals → paper fills |
| Live trading | `live-trading` | Live Trader | Assigned broker + live status/orders |

Ops publisher / param / feed / compliance agents are **out of this desk**. Tools stay vendor-specific (Groww, Kite, …); teams and agents stay generic.

Already-provisioned tenants still have the old Ops Desk / Concierge pack until you add the three teams above (or create a new Stock Broker workspace). The desk chat only lists `learning`, `paper-trading`, and `live-trading`.

End users open **`/t/{slug}/chat`** and get that same three-chat desk. Those teams are auto-assigned on provision and when a user is created.

## What this covers vs the full PRD

| PRD area | Satisfied by Instructions? | Notes |
|----------|----------------------------|--------|
| End-user Learn / Paper / Live copilots | **Yes** | Three chats above |
| Signal / paper / demat **API contracts** | **Partial** | Platform toolkit stubs + broker adapters |
| Flutter customer app / Ops Console | **No** | Separate apps |
| Academy / Classplus | **Atlas KB** on Learning | Do not rebuild LMS |
| Broker OAuth / execution plane | **No** | Bind the tenant’s broker toolkit on Live trading |

## Layout

```text
Instructions/StockBroker/
  README.md
  SKILL.md
  teams/          learning.md  paper_trading.md  live_trading.md
  agents/         learning_guide.md  paper_trader.md  live_trader.md
  workflows/
  tools/          stock_broker_toolkit.py  groww_toolkit.py  kite_toolkit.py
```

## Auto-provision mapping (domain: `stock_broker`)

| Kind | Slug | File |
|------|------|------|
| Agent | `learning-guide` | `agents/learning_guide.md` |
| Agent | `paper-trader` | `agents/paper_trader.md` |
| Agent | `live-trader` | `agents/live_trader.md` |
| Team | `learning` | `teams/learning.md` |
| Team | `paper-trading` | `teams/paper_trading.md` |
| Team | `live-trading` | `teams/live_trading.md` |
| Workflow | `paper-from-signal` | `workflows/paper_from_signal.md` |
| Workflow | `live-approval` | `workflows/live_approval.md` |

## How to load into Atlas

1. Create the three agents from `agents/*.md`.
2. Create the three teams; attach the matching member agent.
3. Create Editable Python tools:
   - `tools/stock_broker_toolkit.py` — platform APIs (signals/paper/algo). Bind on Paper trading (and Live if you want algo status).
   - One **broker adapter** on **Live trading** (orders/holdings) and optionally on **Learning** (read-only quotes for “what’s TCS doing?”): Groww, Kite, or any later toolkit. See `tools/GROWW.md` / `tools/KITE.md`.
4. Validate → Publish. Desk widgets load **Widget → Team → Agent → assigned tool** on manual refresh.

## Canonical API namespaces (from PRD)

| Domain | Path |
|--------|------|
| Signals (customer) | `/v1/signals/*` |
| Paper | `/v1/paper/*` |
| Live | `/v1/live/*` |
| Demat / brokers | `/v1/demat/*`, `/v1/brokers/*` |
| Algo | `/v1/algo/arm\|disarm\|status\|packs\|deploy` |
