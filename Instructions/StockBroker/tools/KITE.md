# Zerodha Kite Connect toolkit — Atlas setup

Source: `tools/kite_toolkit.py`  
API docs: https://kite.trade/docs/connect/v3/  
Host to allowlist: `api.kite.trade`

Stock Broker default customer broker remains **Groww**; use this toolkit when Zerodha/Kite is enabled for a user or for Ops multi-broker support.

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
| `get_quote` / `get_ltp` / `get_ohlc` | no | Market data |

\*Sensitive auth helper.

## Segments / exchanges

Kite supports whatever is on the user profile, commonly: **NSE, BSE, NFO, BFO, CDS, MCX**.  
Products: CNC, NRML, MIS, … Order types: MARKET, LIMIT, SL, SL-M.  
Varieties: `regular`, `amo`, `co`, `iceberg`, …

Instrument ids for quotes: `NSE:INFY`, `NFO:NIFTY25APRFUT` (comma-separated).
