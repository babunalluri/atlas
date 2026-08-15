# Stock Broker — Atlas Instructions

Source of truth for product scope: `~/Downloads/stock-broker-prd.html` (Stock Broker PRD v2.1).

This folder is the **Atlas end-user desk** for Stock Broker: four workspace chats, agent prompts, workflows, and Python tool contracts.

## End-user workspace (four chats)

| Chat | Team slug | Agent | Does |
|------|-----------|-------|------|
| Learning | `learning` | Learning Guide | KB + generic market questions (no price predictions) |
| Paper trading | `paper-trading` | Paper Trader | Signals → paper fills |
| Live trading | `live-trading` | Live Trader | Assigned broker + live status/orders |
| Research | `research` | Researcher | Tool-required stock / F&O analysis (no orders) |

Research is for analysis; live orders stay on Live trading. This is **not** a Sensibull/Tradetron option-chain product.

Ops publisher / param / feed / compliance agents are **out of this desk**. Tools stay vendor-specific (Groww, Kite, …); teams and agents stay generic. Do not auto-bind Groww.

Already-provisioned tenants still have the old Ops Desk / Concierge pack until you add the four teams above (or create a new Stock Broker workspace). Desk pills follow assigned published teams in pack order: `learning`, `paper-trading`, `live-trading`, `research`.

End users open **`/t/{slug}/chat`** and get those chats when the teams are assigned. Those teams are auto-assigned on provision and when a user is created.

## What this covers vs the full PRD

| PRD area | Satisfied by Instructions? | Notes |
|----------|----------------------------|--------|
| End-user Learn / Paper / Live / Research copilots | **Yes** | Four chats above |
| Signal / paper / demat **API contracts** | **Partial** | Platform toolkit stubs + broker adapters |
| Flutter customer app / Ops Console | **No** | Separate apps |
| Academy / Classplus | **Atlas KB** on Learning | Do not rebuild LMS |
| Broker OAuth / execution plane | **No** | Bind the tenant’s broker toolkit on Live trading |

## Layout

```text
Instructions/StockBroker/
  README.md
  SKILL.md
  teams/          learning.md  paper_trading.md  live_trading.md  research.md
  agents/         learning_guide.md  paper_trader.md  live_trader.md  researcher.md
  workflows/
  tools/          stock_broker_toolkit.py  research_toolkit.py  groww_toolkit.py  kite_toolkit.py
```

## Auto-provision mapping (domain: `stock_broker`)

| Kind | Slug | File |
|------|------|------|
| Agent | `learning-guide` | `agents/learning_guide.md` |
| Agent | `paper-trader` | `agents/paper_trader.md` |
| Agent | `live-trader` | `agents/live_trader.md` |
| Agent | `researcher` | `agents/researcher.md` |
| Team | `learning` | `teams/learning.md` |
| Team | `paper-trading` | `teams/paper_trading.md` |
| Team | `live-trading` | `teams/live_trading.md` |
| Team | `research` | `teams/research.md` |
| Workflow | `paper-from-signal` | `workflows/paper_from_signal.md` |
| Workflow | `live-approval` | `workflows/live_approval.md` |

## How to load into Atlas

1. Create the four agents from `agents/*.md`.
2. Create the four teams; attach the matching member agent.
3. Create Editable Python tools:
   - `tools/stock_broker_toolkit.py` — platform APIs (signals/paper/algo). Bind on Paper trading (and Live if you want algo status).
   - `tools/research_toolkit.py` — compute-only snapshots and defined F&O payoffs. Bind on **Research**. See `tools/RESEARCH.md`.
   - One **broker adapter** on **Live trading** (orders/holdings) and optionally on **Learning** / **Research** (read-only quotes): Groww, Kite, or any later toolkit. Do not auto-bind Groww. See `tools/GROWW.md` / `tools/KITE.md`.
4. Validate → Publish. Desk widgets load **Widget → Team → Agent → assigned tool** on manual refresh.

## Canonical API namespaces (from PRD)

| Domain | Path |
|--------|------|
| Signals (customer) | `/v1/signals/*` |
| Paper | `/v1/paper/*` |
| Live | `/v1/live/*` |
| Demat / brokers | `/v1/demat/*`, `/v1/brokers/*` |
| Algo | `/v1/algo/arm\|disarm\|status\|packs\|deploy` |
