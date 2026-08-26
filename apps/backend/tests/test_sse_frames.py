"""SSE frame revision / keepalive helpers."""

from __future__ import annotations

from app.domains.sse_frames import SSE_KEEPALIVE, stream_revision


def test_stream_revision_ignores_data_age() -> None:
    a = {
        "computed_at_ms": 1000,
        "passed": 3,
        "data_age_ms": 10,
        "snapshot_stale": False,
    }
    b = {
        "computed_at_ms": 1000,
        "passed": 3,
        "data_age_ms": 999,
        "snapshot_stale": False,
    }
    assert stream_revision(a) == stream_revision(b)


def test_stream_revision_changes_on_stale_flip() -> None:
    base = {"computed_at_ms": 1000, "passed": 3, "snapshot_stale": False}
    stale = {**base, "snapshot_stale": True}
    assert stream_revision(base) != stream_revision(stale)


def test_stream_revision_changes_on_new_compute() -> None:
    a = {"computed_at_ms": 1000, "passed": 3}
    b = {"computed_at_ms": 1001, "passed": 3}
    assert stream_revision(a) != stream_revision(b)


def test_stream_revision_lab_fetched_at() -> None:
    a = {"fetched_at": 50, "underlying_symbol": "NSE:NIFTY 50", "wings": 5}
    b = {"fetched_at": 50, "underlying_symbol": "NSE:NIFTY 50", "wings": 5}
    c = {"fetched_at": 51, "underlying_symbol": "NSE:NIFTY 50", "wings": 5}
    assert stream_revision(a) == stream_revision(b)
    assert stream_revision(a) != stream_revision(c)


def test_sse_keepalive_is_comment() -> None:
    assert SSE_KEEPALIVE.startswith(b":")
    assert SSE_KEEPALIVE.endswith(b"\n\n")
