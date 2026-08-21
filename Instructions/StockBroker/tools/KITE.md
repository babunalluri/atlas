# Zerodha Kite Connect toolkit — Atlas setup

Source: `tools/kite_toolkit.py`  
API docs: https://kite.trade/docs/connect/v3/  
Host to allowlist: `api.kite.trade`

This file is the **Kite Connect** adapter only. Teams and agents stay vendor-agnostic: assign this toolkit when the tenant’s live venue is Zerodha. Do not assume Groww (or any other broker) is present.

## Load into Atlas

1. Add `api.kite.trade` to `REST_TOOL_ALLOWED_HOSTS` / `BACKEND_ALLOWED_OUTBOUND_HOSTS`.
2. Tool Builder → Editable Python → paste `kite_toolkit.py`.
3. Settings / credential JSON:

```json
{
  "base_url": "https://api.kite.trade",
  "kite_version": "3",
  "api_key": "<kite api key>",
  "api_secret": "<kite api secret>",
  "access_token": "<daily session token after login>"
}
```

4. Session flow:
   - `get_login_url` → open in browser
   - User logs in → redirect returns `request_token`
   - `create_session(request_token=...)` → store `access_token` (expires ~06:00 IST)
5. Validate → Publish. Mark mutating: `place_order`, `modify_order`, `cancel_order`, `convert_position`, `invalidate_session`.

Requires sandbox image/backend that supports **form-urlencoded** HttpProxy (`form` body) — Kite orders use form POST, not JSON.

## Capabilities

| Capability | Mutating | Purpose |
|---|---|---|
| `get_login_url` | no | Build Kite login URL |
| `create_session` | no* | request_token → access_token |
| `invalidate_session` | **yes** | Logout API session |
| `get_profile` / `get_user_margins` / `get_account_health` | no | Account |
| `get_holdings` / `get_positions` | no | Portfolio |
| `convert_position` | **yes** | MIS↔NRML etc. |
| `place_order` / `modify_order` / `cancel_order` | **yes** | Orders |
| `list_orders` / `get_order_history` / `list_trades` / `get_order_trades` | no | Order book |
| `get_order_margins` | no | Pre-trade margin (JSON) |
| `get_instruments` | no | CSV dump (`exchange=NFO`) for F&O underlyings |
| `get_quote` / `get_ltp` / `get_ohlc` | no | Market data |
| `get_historical_candles` | no | ADX / trend (15m candles) |

\*Sensitive auth helper.

## Segments / exchanges

Kite supports whatever is on the user profile, commonly: **NSE, BSE, NFO, BFO, CDS, MCX**.  
Products: CNC, NRML, MIS, … Order types: MARKET, LIMIT, SL, SL-M.  
Varieties: `regular`, `amo`, `co`, `iceberg`, …

Instrument ids for quotes: `NSE:INFY`, `NFO:NIFTY25APRFUT` (comma-separated).

## Signals ops (admin desk)

Kite is **required** for the signal engine (full quote + OI + MCX crude + India VIX + historical candles).

1. Allowlist `api.kite.trade`.
2. Publish `kite_toolkit.py` in Tool Builder.
3. Bind on **`signals-ops`** team only (read-only: `get_quote`, `get_ltp`, `get_ohlc`, **`get_historical_candles`**).
4. Credential/settings: `api_key`, `api_secret`, daily `access_token` (via `create_session` or `scripts/kite_get_access_token.py`).

Global indices (Dow, Nikkei, etc.) are fetched by the backend Yahoo slow tier — **no Yahoo/yfinance tool binding** on the team.

**Do not** bind Groww on Signals ops when using Kite-only setup.

**Example signal setup symbols (adjust expiry/strike to current series):**

| Field | Kite instrument |
|---|---|
| Underlying | `NSE:NIFTY 50` |
| FUT (OI) | `NFO:NIFTY26AUGFUT` |
| CE | `NFO:NIFTY26AUG24500CE` |
| PE | `NFO:NIFTY26AUG24500PE` |
| India VIX | `NSE:INDIA VIX` |
| Crude | `MCX:CRUDEOILM` |

`get_quote` returns `last_price`, `oi` (FUT), and OHLC — the backend signal engine prefers `get_quote` over `get_ltp` for OI.

**Validate in Tool Builder:**

```text
get_quote(instruments="NSE:NIFTY 50,NFO:NIFTY26AUG24500CE")
get_ltp(instruments="NFO:NIFTY26AUG24500CE,NFO:NIFTY26AUG24500PE")
```

Groww-style aliases also work if another adapter shares the same call shape:

```text
get_quote(exchange="NSE", segment="FNO", trading_symbols="NIFTY26AUG24500CE,NIFTY26AUG24500PE")
```
