# Workflow: Live Approval + Arm (UC-2 / UC-2b)

## Trigger

Customer requested live trading, or Ops asks to process the live queue / verify arm readiness.

## Actors

- Compliance Officer (approve/deny, kill)
- Customer Concierge (status explain, pack list)
- Customer app / OpenBull (actual OAuth + arm UI — out of band)

## Steps

1. **Demat linked** — customer broker is **Groww**. Token in vault; Concierge may `get_account_health` (TTL, margin).
2. **Disclosure** — risk disclosure accepted and timestamped.
3. **Request** — status `pending` in queue — `list_live_requests`.
4. **Compliance decision** — `approve_live_request` / `deny_live_request` with notes (HITL). TTL 365 days from approve.
5. **Packs** — customer selects pack(s) e.g. `EX-SMA-X` + lots + day-loss cap (`list_strategy_packs`).
6. **Mode** — Paper | One-click | Live-Auto.
7. **Arm** — only if approved + valid token + risk caps + ≥1 pack → `arm_algo` when policy allows agent arm; else instruct in-app arm.
8. **Signal match** — published signal for deployed pack → risk check → OpenBull (product plane).

## Pass

- Arm refused while pending.
- Token expiry / 401 → auto-disarm + reconnect UX.
- Day-loss breach → disarm + `RISK_CAP_BREACH`.

## Fail

- Arm without approval, token, or packs.
- Live orders after expiry without re-auth.
- Compliance approve without audit notes/tool call.
