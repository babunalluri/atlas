# Stock Broker — Skill Reference

Atlas skill pack for the Stock Broker **end-user desk**: four chats.

- **Workspace chats:** Learning · Paper trading · Live trading · Research
- **Toolkit (platform):** `tools/stock_broker_toolkit.py` — signals, paper hub, algo status. Bind on Paper and/or Live as needed.
- **Research toolkit:** `tools/research_toolkit.py` + `tools/RESEARCH.md` — compute-only snapshots and defined F&O payoffs. Bind on **Research**. Not a full option-chain product.
- **Broker adapters:** vendor APIs stay vendor-specific. Do **not** auto-bind Groww. Assign whichever toolkit the tenant uses on **Live trading** (holdings/orders) and optionally **Learning** / **Research** (read-only quotes):
  - Groww — `tools/groww_toolkit.py` + `tools/GROWW.md`
  - Zerodha Kite — `tools/kite_toolkit.py` + `tools/KITE.md`
  - Any later broker — publish the toolkit, assign it on those teams
- **Learn policy:** Knowledge Base for lessons. Generic ticker questions (“predict TCS…”) stay in **Learning**: read-only quotes if a broker toolkit is assigned; **never** predict or guarantee prices. Do **not** rebuild Classplus.
- **Research policy:** Analysis only. You MUST call tools for any price, position, Greek, IV, payoff, or what-if. Live orders stay on Live trading (HITL).
- **Signal engine (admin):** `tools/signal_engine_toolkit.py` + `tools/SIGNAL_ENGINE.md` — Trade Desk Checklist metrics, entry rules, notify all users. Bind **Kite** on **Signals ops** (`signals-ops`); admin desk streams `GET /admin/signals/stream` ~8×/sec. Global macro via backend Yahoo slow tier (~1 h).

---

## Tool binding policy

**Bind tools on teams** in Team Builder — not on individual agents. Agents discover whatever the team assigns. This reduces turnaround: publish the team once, all members inherit bindings.

| Doc | Owns |
|---|---|
| `teams/*.md` | Which toolkits to bind, required vs optional |
| `agents/*.md` | Persona, routing, procedures (no bind lists) |
| `tools/*.md` | Toolkit capabilities and settings |

---

## Personas → Atlas agents

| Window | Agent file | Tools | Mutating? |
|---|---|---|---|
| Learning | `agents/learning_guide.md` | KB + assigned read-only quotes (`get_ltp` / `get_quote` / `get_ohlc`) | no |
| Paper trading | `agents/paper_trader.md` | signals + paper hub / `place_paper_order` | paper only (HITL) |
| Live trading | `agents/live_trader.md` | assigned broker + algo status | live place/cancel (HITL) |
| Research | `agents/researcher.md` | research toolkit + assigned read-only quotes | no |

No ops-publisher, param-editor, feed-monitor, or compliance-officer agents on the **end-user desk**. **Signals ops** (`signals-ops`) is admin-only — see `teams/signals_ops.md`. Live eligibility and global kill stay in-product / HITL policy — the Live trader **explains**, it does not approve or trip kill.

---

## Teams

| Team | Slug | Member | Mission |
|---|---|---|---|
| Learning | `learning` | Learning Guide | KB + generic market Qs (no predictions) |
| Paper trading | `paper-trading` | Paper Trader | Signal → paper fill |
| Live trading | `live-trading` | Live Trader | Assigned broker + live status |
| Research | `research` | Researcher | Tool-required stock / F&O analysis (no orders) |
| Signals ops | `signals-ops` | Signal Operator | Admin Trade Desk metrics + entry publish (Kite required; Yahoo globals in backend) |

---

## Workflows

| Workflow | File |
|---|---|
| Learn via Knowledge Base | `workflows/learn_via_kb.md` |
| Signal → paper order | `workflows/paper_from_signal.md` |
| Live status / arm / reconnect | `workflows/live_approval.md` |
| Auto-disarm / reconnect | `workflows/kill_switch.md` |

---

## Tool capability map

### Platform (Paper / Live as assigned)

| Capability | Read / mutate | Window |
|---|---|---|
| `list_signals` / `get_signal` | read | Paper |
| `get_paper_hub` / `list_positions` | read | Paper |
| `place_paper_order` | mutate | Paper |
| `get_algo_status` / `list_strategy_packs` | read | Live |
| `arm_algo` / `disarm_algo` | mutate | Live (HITL, policy) |

### Research (Research team)

| Capability | Read / mutate | Notes |
|---|---|---|
| `research_stock_snapshot` | read | Trend / MA / momentum / S-R from supplied prints |
| `research_compare_symbols` | read | Two-symbol snapshot from supplied LTPs |
| `research_option_payoff` | read | Defined structures only; no live chain |

### Broker adapters (Live) — generic aliases

Broker APIs differ. Prefer these when they exist on the **assigned** toolkit; otherwise call the closest bound name:

| Intent | Typical names |
|---|---|
| Account / token health | `get_account_health`, `get_profile` |
| Holdings | `get_holdings` |
| Positions | `get_positions` |
| Margin / funds | `get_user_margin`, `get_margins`, `get_user_margins`, `get_funds` |
| Orders | `list_orders`, `get_orders` |
| Quotes (Learning read-only; Live any) | `get_ltp`, `get_quote`, `get_ohlc` |
| Place / cancel (HITL) | `place_order`, `cancel_order`, vendor `modify_order` |

---

## Assigned broker policy

1. Discover tools bound on **this team** (Team Builder).
2. Never assume Groww, Kite, or any vendor.
3. If no broker tool is bound on Live trading, say so. Do not invent holdings or P&L.
4. Token expiry / 401 → auto-disarm; reconnect **that** broker.
5. Never echo OAuth, access tokens, secrets, or OTPs.
6. Prefer tool results over memory for prices, fills, and arm status.
7. No guaranteed-returns language. SEBI-aware.
