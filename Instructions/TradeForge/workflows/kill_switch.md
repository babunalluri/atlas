# Workflow: Kill Switch / Auto-Disarm

## Trigger

Admin orders a global or per-user kill; or system signals token expiry / risk-cap breach / broker 401.

## Actors

- Compliance Officer / Super Admin only for intentional kill
- Platform / OpenBull for auto-disarm events (agent explains + verifies)

## Steps — intentional kill

1. Confirm actor is Compliance/Admin (Operators cannot kill).
2. Confirm scope twice in chat: `global` or `user_id=...`.
3. Optional: `cancel_open` flag for open orders.
4. `kill_switch(scope=..., user_id=..., cancel_open=...)` (HITL).
5. `get_algo_status` — expect `armed=false` quickly (PRD ≤5s target for global).
6. Customer-facing copy: disarmed state; no new live orders.

## Steps — auto-disarm (Groww token)

1. Detect expiry or Groww 401 via `get_account_health` / algo status.
2. Expect `armed=false`; block new live orders until Groww re-auth.
3. Concierge: push/in-app style guidance — reconnect Groww, then re-arm if still approved + packs set.

## Pass

- No live accepts after kill / expiry.
- Audit row for intentional kill.
- Customer sees clear disarmed + reconnect path for Groww.

## Fail

- Operator trips kill without Compliance.
- Live orders continue after expiry.
- Tokens echoed in chat.
