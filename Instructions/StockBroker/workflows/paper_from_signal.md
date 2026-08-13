# Workflow: Signal → Paper Order (UC-1)

## Trigger

Customer wants to trade a signal in paper mode.

## Actors

- Paper Trader (Paper trading window)

## Steps

1. **Auth / plan** — plan allows paper (not Free-only learn).
2. **Pick signal** — `list_signals` / `get_signal(signal_id)`.
3. **Paper hub** — `get_paper_hub`.
4. **Build ticket** — market/limit, qty, product analog; validate buying power.
5. **Idempotency** — stable `idempotency_key` per user intent.
6. **Place** — user confirms → `place_paper_order` (HITL).
7. **Confirm** — `list_positions` + hub P&L; explain rejects.

## Pass

- Single fill on retry with the same idempotency key.
- Degraded quotes → say flat MTM; do not fake P&L.

## Fail

- Double fill on retry.
- Paper blocked solely because cash market is closed.
- Paper Trader invents a fill without a tool result.
- Live `place_order` called from this window.
