# Groww toolkit — Atlas setup

Source: `tools/groww_toolkit.py`  
API docs: https://groww.in/trade-api/docs/curl  
Host to allowlist: `api.groww.in`

## Load into Atlas

1. Add `api.groww.in` to `REST_TOOL_ALLOWED_HOSTS` / `BACKEND_ALLOWED_OUTBOUND_HOSTS`.
2. Tool Builder → Editable Python → paste `groww_toolkit.py`.
3. Settings example:

```json
{
  "base_url": "https://api.groww.in",
  "api_version": "1.0",
  "access_token": "<from Groww Trading APIs → Access Token>"
}
```

Or key+secret (then call `create_access_token` and store returned token as `access_token`):

```json
{
  "base_url": "https://api.groww.in",
  "api_key": "<groww api key>",
  "api_secret": "<groww api secret>"
}
```

Prefer binding secrets via a tenant credential (JSON) merged into settings — never commit tokens.

4. Validate → Publish. Mark mutating capabilities: `place_order`, `modify_order`, `cancel_order`.
5. Attach to Customer Concierge (read: holdings/positions/margin/orders/ltp) and Compliance (account health). Live place/cancel only if product policy allows agent-assisted trading with HITL.

## Capabilities

| Capability | Mutating | Purpose |
|---|---|---|
| `create_access_token` | no* | api_key+secret → daily token |
| `create_access_token_totp` | no* | api_key+totp → daily token |
| `get_account_health` | no | token configured? + margin |
| `get_holdings` | no | demat holdings |
| `get_positions` | no | positions (CASH/FNO) |
| `get_position_for_symbol` | no | one symbol |
| `get_user_margin` | no | funds / margin |
| `get_required_margin` | no | pre-trade margin calc |
| `list_orders` | no | today's orders |
| `get_order_detail` / `get_order_status` / `get_order_trades` | no | order lookup |
| `get_order_status_by_reference` | no | idempotency lookup |
| `place_order` | **yes** | create order |
| `modify_order` | **yes** | modify open order |
| `cancel_order` | **yes** | cancel order |
| `get_quote` / `get_ltp` / `get_ohlc` | no | live market data |

\*Auth helpers are sensitive; treat as restricted even if not order-mutating.

## Agent usage notes

- Customer broker is **Groww only** for Stock Broker live.
- On 401 / auth failure → treat as auto-disarm condition; ask user to refresh Access Token (daily).
- Always pass `order_reference_id` on `place_order` when retrying (maps to Groww idempotency / GA007 duplicate).
- Segments: `CASH`, `FNO`. Products: e.g. `CNC`, `MIS`, `NRML` per Groww rules.
