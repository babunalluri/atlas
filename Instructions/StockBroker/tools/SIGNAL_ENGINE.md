# Signal engine toolkit

Admin-only metric board and BUY entry evaluation for the Stock Broker desk.

## Bind (team only)

Bind in **Team Builder** on **Signals ops** (`signals-ops`) — not on the Signal Operator agent.

| Toolkit | Required? | Notes |
|---|---|---|
| `signal_engine_toolkit.py` | optional (chat) | Native admin desk uses `/admin/signals/*` |
| Kite or Groww (read-only) | recommended | `get_ltp`, `get_quote`, `get_ohlc` for live metrics |

Do **not** bind on Learning, Paper, Live, or Research.

## Live setup checklist (no mock)

1. **Signals ops** team — published  
2. **Kite toolkit** — bound on team, valid `access_token`  
3. **Signal engine tool** — admin selects underlying on the desk **Signal config** panel, or PATCH `/admin/signals/config`:

```json
{
  "mock": false,
  "underlying_symbol": "NSE:BANKNIFTY",
  "underlying_label": "BANKNIFTY",
  "fut_symbol": "NFO:BANKNIFTY26AUGFUT",
  "ce_symbol": "NFO:BANKNIFTY26AUG56000CE",
  "pe_symbol": "NFO:BANKNIFTY26AUG56000PE",
  "crude_symbol": "MCX:CRUDEOILM",
  "india_vix_symbol": "NSE:INDIA VIX",
  "pcr": 1.25,
  "max_pain": 24400,
  "ivp": 45,
  "dow_change_pct": -0.5,
  "strike_step": 50
}
```

4. Allowlist `api.kite.trade` in `REST_TOOL_ALLOWED_HOSTS`  
5. Admin desk — warnings list shows anything still missing  

Replace option symbols with **current expiry** strikes from Kite. Update `pcr`, `max_pain`, `ivp`, `dow_change_pct` each session.

## Admin desk

The admin trading desk opens **`GET /admin/signals/stream`** (SSE) at ~**8×/sec**. The engine coalesces ticks per tenant and throttles broker quotes to ~**2×/sec**. Dow Jones and crude use slower cache tiers inside the backend engine.

Single snapshot: `GET /admin/signals/state` (same payload, coalesced).

## Shared cache (Redis)

When `REDIS_URL` points at a real Redis instance, signal-engine caches are **shared across workers**:

| Key pattern | Purpose | TTL |
|---|---|---|
| `atlas:signals:{tenant}:snapshot` | Full metrics payload for SSE | ~250ms |
| `atlas:signals:{tenant}:m:*` | Tiered feed/quote/setup entries | tier-based |
| `atlas:signals:{tenant}:sess` | OI baseline, IV session, publish dedup | session |
| `atlas:signals:watch:{tenant}` | Active admin desk (SSE) | 2s, renewed each frame |

A background ticker (`SIGNAL_ENGINE_TICKER_ENABLED`, default **off**) pre-computes snapshots for watched tenants when enabled. With no admin desk open, the ticker idle-polls every 2s instead of 8 Hz.

With `REDIS_URL=memory://` (local/test), caches fall back to in-process dicts — fine for single-worker dev.

## Tool settings

| Key | Default | Purpose |
|---|---|---|
| `mock` | `false` | **Live mode** — set `true` only for rehearsal |
| `underlying_symbol` | _(required)_ | Cash/index — admin selects (any Kite/Groww symbol) |
| `underlying_label` | _(optional)_ | Display name (e.g. BANKNIFTY) |
| `fut_symbol` / `nifty_fut_symbol` | _(empty)_ | FUT for OI |
| `ce_symbol` / `pe_symbol` | _(empty)_ | ATM option symbols (Kite `NFO:…`) |
| `crude_symbol` | `MCX:CRUDEOILM` | Crude oil (Kite MCX) |
| `india_vix_symbol` | `NSE:INDIA VIX` | India VIX LTP (medium tier) |
| `dow_change_pct` | _(empty)_ | Manual/session Dow % change (slow tier) |
| `max_pain` | _(empty)_ | Max pain strike until chain API wired |
| `pcr` | _(empty)_ | Put–call ratio (manual or chain) |
| `ivp` | _(empty)_ | IV percentile 0–100 |
| `oi_pct_chg` | _(empty)_ | OI % change on NIFTY FUT |
| `iv_chg` | _(empty)_ | ATM IV change vs open |
| `india_vix` | _(empty)_ | Override VIX (else fetched from symbol) |
| `fii_net` | _(empty)_ | Manual daily FII net (₹ Cr) — skip rule when unset |
| `strike_step` | `50` | ATM rounding |
| `entry_ce_premium` / `entry_pe_premium` | `100` | Entry label premiums |
| `exit_pct` | `5` | Exit target in entry label |
| `metrics_json` | _(empty)_ | Override/extend metric rows |

## Metric rows (default)

### Custom desk rules (spreadsheet)

| ID | Rule | Target |
|---|---|---|
| ADX | less than | 25 |
| OI | greater than | 50 |
| IV | % of day high | 50 |
| CrudeOil | below prev close | — |
| DowJones | abs change | ±0.5% |
| ATM | info | live strike |
| CE / PE | CE = PE | — |

### Sensibull-aligned (verified against screener / OI dashboard)

Sensibull shows **PCR, IVP, Max Pain, India VIX, OI % Chg, IV Chg** on its [options screener](https://web.sensibull.com/options-screener) and live OI charts. It does **not** auto-fire BUY signals — traders combine these with price action. Our engine encodes a **BUY CE** confluence:

| ID | Rule | Target | Sensibull reference |
|---|---|---|---|
| PCR | between | 1.0 – 1.3 | Bullish band; >1.3 overbought ([blog](https://blog.sensibull.com/2018/07/09/option-chain-tutorial/)) |
| IVP | less than | 70 | IV percentile — avoid extreme premium |
| India VIX | less than | 18 | Skip when VIX high (backtests / desk guides) |
| Max Pain | spot < max pain | — | Tier-1 bullish CE when PCR confirms |
| OI % Chg | greater than | 0 | Screener column — OI building |
| IV Chg | less than or equal | 0 | Screener column — IV contracting |

Entry when **all** evaluable rules pass:

```text
BUY= {ATM}, CE={n}, PE={n}, EXIT +{pct}%
```

## Accuracy (live engine)

The backend signal engine (`apps/backend/.../signal_engine.py`) applies these rules for sharper BUY evaluation:

| Behavior | Detail |
|---|---|
| **Auto ATM options** | When `auto_atm_symbols` is true (default), CE/PE symbols are derived each tick from `fut_symbol` + live ATM strike — no stale fixed strikes. |
| **Live buy line** | BUY banner uses live CE/PE premiums when quotes print (not static 100/100 placeholders). |
| **ATM PCR estimate** | When `pcr` is unset, engine computes PE-OI ÷ CE-OI from ATM option `get_quote` (partial chain; override from Sensibull when you need full-chain PCR). |
| **IV blend** | ATM IV averages CE + PE implied vol when both are present. |
| **ADX** | Kite `get_historical_candles` on underlying `instrument_token` (15m, ~5d), cached 60s. |
| **OI % chg** | Session baseline keyed by IST date (first FUT OI of the day vs current). |
| **Spot / basis** | `spot_chg`, `spot_vs_open` from underlying quote; `fut_basis` = FUT premium over spot %. |
| **RSI** | 14-period on 15m closes (same candle fetch as ADX), cached 60s. |
| **VIX chg** | India VIX LTP minus session-open VIX (medium tier). |
| **FII net** | Manual daily input on desk — rule skipped until set. |
| **Tiers** | Fast ~500ms broker · medium 60s · slow 1h · manual until changed |

Still set **max_pain**, **ivp**, and **dow_change_pct** manually (or via future chain API). PCR manual override wins over ATM-OI estimate.

## Data availability

| Metric | Groww | Kite | Notes |
|---|---|---|---|
| NIFTY LTP | yes | yes | CASH |
| OI / OI % chg | limited | yes | Full quote on FUT |
| IV / IV chg | no | partial | Known OPT symbol only |
| PCR / Max Pain | no | no | Needs option chain — set manually or future feed |
| IVP | no | no | Needs 250d IV history — set manually |
| India VIX | no | yes | `NSE:INDIA VIX` quote |
| Crude | no (no MCX) | yes | MCX segment |
| Dow Jones | no | no | Set `dow_change_pct` or external feed |
| ADX / RSI | compute | compute | Live via `get_historical_candles` (15m) |
| Spot % / vs open | yes | yes | From underlying full quote |
| Fut basis | yes | yes | FUT vs spot from batch quote |
| CE/PE OI | partial | partial | ATM option quote OI |
| VIX chg | no | yes | Session open vs live VIX |
| FII net | no | no | Manual desk input (`fii_net`) |
| PCR (ATM OI) | partial | partial | Auto when CE/PE quote includes OI |

Atlas is **not** Sensibull — no option-chain UI. Bind Kite for index/VIX; set `pcr`, `max_pain`, `ivp` from Sensibull or your chain feed until automated.

## Publish + notify

- Admin API: `POST /admin/signals/publish` when entry_ready (in-app fan-out to all users).
- Tool: `publish_entry_signal` (HITL) for agent-driven publish.
- Dedupes identical entry within a session.

## Extend metrics

Append to `metrics_json`:

```json
[
  {"id": "fii_net", "label": "FII net", "rule": "gt", "target": 0, "tier": "slow", "hint": "Sensibull FII/DII — manual daily input"}
]
```

Supported rules: `lt`, `gt`, `lte`, `gte`, `eq`, `abs_lte`, `between`, `below_prev_close`, `ce_pe_balance`, `iv_pct_day_high`, `spot_below_max_pain`, `info`.

Use `between` with `target` + `target_high` for band rules (e.g. PCR).

## Future (not in Atlas yet)

Sensibull also exposes **FII/DII**, **synthetic fut**, **Greeks**, and **multistrike OI charts**. Add via `metrics_json` + manual inputs or a dedicated chain feed API when available.
