# Stock Broker — Atlas Instructions

Source of truth for product scope: `~/Downloads/stock-broker-prd.html` (Stock Broker PRD v2.1).

This folder is the **Atlas end-user desk** for Stock Broker: four workspace chats, agent prompts, workflows, and Python tool contracts.

## End-user workspace (four chats)

| Chat | Team slug | Agent | Does |
|------|-----------|-------|------|
| Learning | `learning` | Learning Guide | KB + generic market questions (no price predictions) |
| Paper trading | `paper-trading` | Paper Trader | Signals → paper fills |
| Live trading | `live-trading` | Live Trader | Assigned broker + live status/orders |
| Research | `research` | _(leader-only — no agent)_ | Tool-required stock / F&O analysis (no orders) |

Research is for analysis; live orders stay on Live trading. This is **not** a Sensibull/Tradetron option-chain product.

Ops publisher / param / feed / compliance agents are **out of the end-user desk**. **Signals ops** (`signals-ops`) is **admin-only** — metric board and entry publish; see `teams/signals_ops.md` and `tools/SIGNAL_ENGINE.md`.

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
  OPTIONS_LAB_GUIDE.md   # Operator guide for Options Lab
  teams/          learning.md  paper_trading.md  live_trading.md  research.md  signals_ops.md
  agents/         learning_guide.md  paper_trader.md  live_trader.md  signal_operator.md
  workflows/
  tools/          stock_broker_toolkit.py  research_toolkit.py  signal_engine_toolkit.py  groww_toolkit.py  kite_toolkit.py
                  KITE.md  SIGNAL_ENGINE.md  …
```

**Options Lab (operators):**
- Markdown: [`OPTIONS_LAB_GUIDE.md`](./OPTIONS_LAB_GUIDE.md)
- Static HTML (screenshots): [`guides/options-lab/index.html`](./guides/options-lab/index.html) — open in a browser

## Auto-provision mapping (domain: `stock_broker`)

| Kind | Slug | File |
|------|------|------|
| Agent | `learning-guide` | `agents/learning_guide.md` |
| Agent | `paper-trader` | `agents/paper_trader.md` |
| Agent | `live-trader` | `agents/live_trader.md` |
| Agent | `signal-operator` | `agents/signal_operator.md` |
| Team | `learning` | `teams/learning.md` |
| Team | `paper-trading` | `teams/paper_trading.md` |
| Team | `live-trading` | `teams/live_trading.md` |
| Team | `research` | `teams/research.md` |
| Team | `signals-ops` | `teams/signals_ops.md` |
| Workflow | `paper-from-signal` | `workflows/paper_from_signal.md` |
| Workflow | `live-approval` | `workflows/live_approval.md` |

## How to load into Atlas

1. Create agents from `agents/*.md` (persona only — no tool binding on agents).
2. Create teams from `teams/*.md`; attach the matching member agent where applicable.
3. **Bind tools on teams** (Team Builder) per each team’s **Assigned tools** section — not on agents.
4. Create Editable Python tools as needed:
   - `tools/stock_broker_toolkit.py` — bind on **Paper trading** (and optionally **Live trading** for algo).
   - `tools/research_toolkit.py` — bind on **Research**. See `tools/RESEARCH.md`.
   - **`tools/kite_toolkit.py`** — bind on **Signals ops** (required for live signal engine) and **Live trading**. See `tools/KITE.md`.
   - `tools/signal_engine_toolkit.py` — optional on **Signals ops** (admin desk uses native API). See `tools/SIGNAL_ENGINE.md`.
5. Validate → Publish. Desk widgets load **Widget → Team → assigned tools** on manual refresh.

## Canonical API namespaces (from PRD)

| Domain | Path |
|--------|------|
| Signals (customer) | `/v1/signals/*` |
| Paper | `/v1/paper/*` |
| Live | `/v1/live/*` |
| Demat / brokers | `/v1/demat/*`, `/v1/brokers/*` |
| Algo | `/v1/algo/arm\|disarm\|status\|packs\|deploy` |
