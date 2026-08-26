"""Shared signal-engine timing constants (single source of truth)."""

from __future__ import annotations

from typing import Literal

Tier = Literal["slow", "medium", "fast", "broker"]

# UI stream cadence (~8 Hz). Broker fetches are throttled separately.
STREAM_INTERVAL_MS = 125
# Book-first Tier A ticks (ticker hub alive) — ~5 Hz worker floor.
# SSE may poll faster at STREAM_INTERVAL_MS; duplicate frames are suppressed
# via stream_revision() so 8 Hz does not force identical React updates.
# Cold REST/IV remains slower.
SIGNAL_ACTIVE_TICK_MS = 200
BROKER_QUOTE_TTL_MS = 500
# When the live ticker book is empty/dead, REST gap-fill is capped to this
# cadence so a 200ms worker loop cannot stampede the sandbox.
TIER_A_REST_GAP_FILL_MS = 5_000

TIER_TTL_MS: dict[Tier, int] = {
    "slow": 3_600_000,
    "medium": 60_000,
    "fast": STREAM_INTERVAL_MS,
    "broker": BROKER_QUOTE_TTL_MS,
}

# Ticker sleeps longer when no admin desk has an open SSE connection.
TICKER_IDLE_POLL_SECONDS = 2.0

# Must exceed a slow state_for_stream / sandbox tick so the watch key cannot
# expire mid-compute (touch used to run only AFTER the frame was built).
WATCH_TTL_SECONDS = 45
# Redis keeps last-good longer than the badge "fresh" window so SSE can serve
# stale frames (snapshot_stale=true) instead of missing and cold-recomputing.
# 45s: sandbox-backed ticks on small VMs often take 15–40s; while a refresh is
# in flight we also force fresh (see compute_lock_held).
SNAPSHOT_FRESH_MS = 45_000
# 10 min last-good: brief SSE gaps / tab blips must not wipe the board.
SNAPSHOT_TTL_MS = 600_000
# Short lock TTL so a killed worker self-heals quickly; holders must heartbeat.
LOCK_TTL_MS = 10_000
LOCK_HEARTBEAT_SECONDS = 3.0

# How long SSE waits for an in-flight compute before starting its own.
STREAM_COMPUTE_WAIT_MS = 3_000
# Hard cap for a single state() under sandbox pressure (small VMs).
STATE_COMPUTE_TIMEOUT_MS = 45_000
# Loop-level guard: one slow tick() must not block the worker forever.
# Slightly above STATE_COMPUTE_TIMEOUT so a single-tenant refresh can finish.
SIGNAL_TICK_DEADLINE_SECONDS = (STATE_COMPUTE_TIMEOUT_MS / 1000.0) + 15.0
# Provisional frame TTL while the first live tick is still warming.
ENGINE_STARTING_SNAPSHOT_MS = 90_000
# BUY must see at least this fraction of gates_entry rows with data (or fail-closed
# votes). Prevents a budget-truncated tick from manufacturing entry_ready on a
# tiny all-pass subset (e.g. 22/22 while 30 Yahoo/Tier-B gates abstained).
ENTRY_GATE_COVERAGE_RATIO = 0.85
