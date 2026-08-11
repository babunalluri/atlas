# Workflow: Signal → Paper Order (UC-1)

## Trigger

Customer wants to trade a signal in paper mode (Sandbox/Pro).

## Actors

- Customer Concierge

## Steps

1. **Auth / plan** — user is logged in; plan allows paper (not Free-only learn).
2. **Pick signal** — `list_signals` / `get_signal(signal_id)`.
3. **Paper hub** — `get_paper_hub` (virtual capital default ₹1 Cr unless overridden).
4. **Build ticket** — market/limit, qty, product analog (MIS/CNC/NRML); validate buying power.
5. **Idempotency** — set `idempotency_key` (stable per user intent; for algo-like fills use `signal_id+user_id` pattern when applicable).
6. **Place** — user confirms → `place_paper_order` (HITL).
7. **Confirm** — `list_positions` + hub P&L; explain reject reasons if any.

## Pass

- Single fill on retry with same idempotency key.
- Positions / Home P&L update when quotes available; if quotes down, place may succeed with flat MTM (degraded mode — say so).

## Fail

- Double fill on retry.
- Paper blocked solely because cash market is closed.
- Concierge invents fill without tool result.
