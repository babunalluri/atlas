"""Helpers for desk SSE streams (Signal / Options Lab / Param Chart)."""

from __future__ import annotations

from typing import Any, Mapping

# SSE comment — keeps proxies/browsers from idle-closing; clients ignore it.
SSE_KEEPALIVE = b":\n\n"


def stream_revision(payload: Mapping[str, Any]) -> tuple[Any, ...]:
    """Stable identity of a desk frame for duplicate suppression.

    Deliberately ignores ``data_age_ms`` (changes every poll) so identical
    worker snapshots do not force React re-renders at STREAM_INTERVAL_MS.
    ``snapshot_stale`` is included so the UI still sees the Running→Stale flip.
    ``globals_computed_at_ms`` + instrument ensure matrix row refreshes (and
    globals-only refreshes) are not incorrectly deduped against each other.
    """
    underlying = payload.get("underlying") if isinstance(payload.get("underlying"), dict) else {}
    kite_live = payload.get("kite_live") if isinstance(payload.get("kite_live"), dict) else {}
    return (
        payload.get("computed_at_ms"),
        payload.get("globals_computed_at_ms"),
        payload.get("instrument") or underlying.get("symbol"),
        payload.get("fetched_at"),
        payload.get("engine_computing"),
        payload.get("engine_enabled"),
        payload.get("feed_source"),
        payload.get("snapshot_stale"),
        payload.get("quote_source"),
        payload.get("underlying_symbol"),
        payload.get("fut_symbol"),
        payload.get("wings"),
        payload.get("ok"),
        payload.get("passed"),
        payload.get("evaluable"),
        payload.get("error"),
        # Param Chart overlay: LTP can move many times inside one fetched_at second.
        kite_live.get("spot"),
        kite_live.get("ce"),
        kite_live.get("pe"),
        payload.get("quote_stale"),
    )
