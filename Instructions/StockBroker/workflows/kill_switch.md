# Workflow: Auto-Disarm / Reconnect

## Trigger

Broker token expiry, 401, risk-cap breach, or user asks why live is disarmed.

## Actors

- Live Trader (explains + verifies)
- In-product reconnect / global kill (out of band)

## Steps

1. Read `get_account_health` / `get_algo_status` on **assigned** tools.
2. Expect `armed=false` (or vendor equivalent) after expiry / 401.
3. Tell the user to reconnect **that** broker, then re-arm in-product if still approved + packs set.
4. Do **not** trip a global kill switch from this chat. Do **not** echo tokens.

## Pass

- No live accepts after expiry until re-auth.
- Customer sees a clear reconnect path for the assigned broker.

## Fail

- Live orders continue after expiry.
- Tokens echoed in chat.
- Agent claims to have flipped a global kill.
