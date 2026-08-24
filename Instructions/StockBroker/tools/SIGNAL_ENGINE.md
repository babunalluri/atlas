# Signal engine toolkit

Admin-only metric board and BUY entry evaluation for the Stock Broker desk.

## Bind (team only)

Bind in **Team Builder** on **Signals ops** (`signals-ops`) — not on the Signal Operator agent.

| Toolkit | Required? | Notes |
|---|---|---|
| `signal_engine_toolkit.py` | optional (chat) | Native admin desk uses `/admin/signals/*` |
| Kite or Groww (read-only) | **Kite required** | `get_quote`, `get_ltp`, `get_ohlc`, `get_historical_candles` — backend engine reads **Signals ops** team bindings only |

Do **not** bind on Learning, Paper, Live, or Research.

## Live setup checklist (no mock)

1. **Signals ops** team — published  
2. **Kite toolkit** — bound on **Signals ops** team only, valid `access_token`  
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

The admin trading desk opens **`GET /admin/signals/stream`** (SSE) at ~**8×/sec**. With `KITE_TICKER_ENABLED` and the book-first path, the background worker evaluates **Tier A** (underlying / FUT / CE / PE LTP+OI) from the live ticker book at **`SIGNAL_ACTIVE_TICK_MS` (200ms ≈ 5 Hz)**. When the ticker heartbeat is dead, REST gap-fill is capped at **`TIER_A_REST_GAP_FILL_MS` (5s)** so the fast loop cannot stampede the sandbox — the badge may show stale/rest honestly during that window. Crude / India VIX / Dow / index-aux use **medium/slow caches** refreshed off that critical path. Dow Jones and crude no longer block LTP ticks.

Single snapshot: `GET /admin/signals/state` (same payload, coalesced).

### Follow-up: quotes outside the sandbox

This pass keeps Kite **REST** (`get_quote` / IV) on the existing sandbox toolkit path. Once ticker-first is stable in production, prefer a **read-only in-process Kite client** in the backend for quote/LTP/historical — keep the sandbox for tenant-authored tools only. Not implemented here (larger auth/safety surface).

## Shared cache (Redis)

When `REDIS_URL` points at a real Redis instance, signal-engine caches are **shared across workers**:

| Key pattern | Purpose | TTL |
|---|---|---|
| `atlas:signals:{tenant}:snapshot` | Full metrics payload for SSE | 15s TTL; stale after 2s |
| `atlas:signals:{tenant}:m:*` | Tiered feed/quote/setup entries | tier-based |
| `atlas:signals:{tenant}:sess` | OI baseline, IV session, publish dedup | session |
| `atlas:signals:watch:{tenant}` | Active admin desk (SSE) | 2s, renewed each frame |

A background ticker (`SIGNAL_ENGINE_TICKER_ENABLED`, default **off**) pre-computes snapshots for watched tenants when enabled. Desk Compose also enables `OPTIONS_LAB_TICKER_ENABLED` and `KITE_TICKER_ENABLED` (both default **off** in settings so non-desk deploys do not open Kite WS or 8 Hz loops by accident). Starting the engine touches the watcher; the config API schedules a post-commit snapshot warm so SSE can serve Redis instead of blocking on the first cold `state()` fan-out. The worker also keeps the shared Kite WebSocket hub subscribed to Signal symbols (`source=signal`, merged with Options Lab) so LTP/OI overlay stays hot. Tier A evaluates from the live book first; REST `get_quote` is only for gaps/IV. Crude / VIX / aux refresh on a medium-tier background timer. Snapshots carry `computed_at_ms` / `data_age_ms` (desk badge uses age for Stale vs Running). Compute locks use a **10s TTL** with a **3s heartbeat** so a killed worker self-heals quickly without stampeding while alive. `KITE_TICKER_ENABLED` refuses to start (and `sync_tenant` is a no-op) when `WEB_CONCURRENCY > 1` (production image defaults to 1 worker). With no admin desk open, the ticker idle-polls every 2s instead of ~5 Hz.

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

The engine ships **124 metric rows** — **115 Trade Desk Checklist** items plus 9 core gated helpers (ATM, OI % chg, RSI, etc.). Metrics include `check_no`, `category`, and `gates_entry` (only gated rules block `entry_ready`).

UI groups metrics by checklist category on the admin desk.

### Custom desk rules (legacy summary)

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

## Yahoo Finance (global macro — slow tier)

Global indices, metals, and crypto for the Trade Desk checklist use **public Yahoo Finance** via `signal_engine_yahoo.py` — not Kite.

Yahoo aggressively rate-limits scripted access (`429 Too Many Requests`). The engine therefore:

| Rule | Value |
|---|---|
| Poll interval | **1 hour** (slow tier) — never on the 8 Hz stream |
| Fetch method | One batched `yf.download` (5d daily candles), **not** per-ticker `.info` |
| Session | `curl_cffi` Chrome impersonation (plain `requests` gets blocked more often) |
| Chunking | 8 symbols per batch, 2 s pause between batches (~24 symbols ≈ 30 s) |
| On 429 | **30 min cooldown** — serve last cached values, do not retry |
| Mock mode | Deterministic demo values — no Yahoo calls |

Prefer **Kite** for all Indian live data (NIFTY, VIX, MCX crude, USDINR). Use Yahoo only for offshore indices where Kite has no symbol.

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
| Dow Jones / global indices | no | no | Yahoo Finance slow tier (see below) or manual |
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
