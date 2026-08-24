"""Shared signal-engine timing constants (single source of truth)."""

from __future__ import annotations

from typing import Literal

Tier = Literal["slow", "medium", "fast", "broker"]

# UI stream cadence (~8 Hz). Broker fetches are throttled separately.
STREAM_INTERVAL_MS = 125
# Book-first Tier A ticks (ticker hub alive) — ~5 Hz worker floor.
# SSE still uses STREAM_INTERVAL_MS; cold REST/IV remains slower.
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

WATCH_TTL_SECONDS = 2
# Redis keeps last-good longer than the badge "fresh" window so SSE can serve
# stale frames (snapshot_stale=true) instead of missing and cold-recomputing.
# 45s: sandbox-backed ticks on small VMs often take 15–40s; while a refresh is
# in flight we also force fresh (see compute_lock_held).
SNAPSHOT_FRESH_MS = 45_000
SNAPSHOT_TTL_MS = 120_000
# Short lock TTL so a killed worker self-heals quickly; holders must heartbeat.
LOCK_TTL_MS = 10_000
LOCK_HEARTBEAT_SECONDS = 3.0

# How long SSE waits for an in-flight compute before starting its own.
STREAM_COMPUTE_WAIT_MS = 3_000
