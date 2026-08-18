"""Shared signal-engine timing constants (single source of truth)."""

from __future__ import annotations

from typing import Literal

Tier = Literal["slow", "medium", "fast", "broker"]

# UI stream cadence (~8 Hz). Broker fetches are throttled separately.
STREAM_INTERVAL_MS = 125
BROKER_QUOTE_TTL_MS = 500

TIER_TTL_MS: dict[Tier, int] = {
    "slow": 3_600_000,
    "medium": 60_000,
    "fast": STREAM_INTERVAL_MS,
    "broker": BROKER_QUOTE_TTL_MS,
}

# Ticker sleeps longer when no admin desk has an open SSE connection.
TICKER_IDLE_POLL_SECONDS = 2.0

WATCH_TTL_SECONDS = 2
SNAPSHOT_TTL_MS = max(STREAM_INTERVAL_MS * 2, 250)
LOCK_TTL_MS = max(BROKER_QUOTE_TTL_MS, STREAM_INTERVAL_MS * 2)
