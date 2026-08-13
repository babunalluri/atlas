# Workflow: Live Status + Arm (UC-2)

## Trigger

Customer asks about live trading, holdings, orders, reconnect, or arm readiness.

## Actors

- Live Trader (Live trading window)
- In-product approval / OAuth (out of band)

## Steps

1. **Discover tools** — use the broker toolkit **assigned** on Live trading. Do not assume a vendor.
2. **Demat health** — `get_account_health` / `get_profile` or vendor equivalent.
3. **Portfolio** — holdings, positions, margin, orders via assigned aliases.
4. **Approval / arm** — `get_algo_status` / `list_live_requests` when bound. Explain pending vs approved. This agent cannot approve.
5. **Packs / mode** — `list_strategy_packs` when bound. Arm only if policy + HITL allow (`arm_algo`).
6. **Live order** — assigned `place_order` / `cancel_order` (HITL) with stable idempotency / `order_reference_id`.
7. **401 / expiry** — auto-disarm copy; reconnect **that** broker.

## Pass

- Arm refused while pending.
- Token expiry → disarmed + reconnect path.
- Holdings/orders come from the assigned adapter, not memory.

## Fail

- Invented fills or “you’re live” without tools.
- Approving live or tripping global kill from this window.
- Paper orders placed here.
