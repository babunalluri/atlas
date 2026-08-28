"""Tests for Param Chart shared allowlist + Kite candle helpers."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from app.domains import param_chart_cache as pc_cache
from app.domains.param_chart import (
    ParamChartConfig,
    _apply_premiums,
    _candle_ohlc_by_day,
    _empty_day,
    _trading_days_in_month,
    suggest_option_symbols,
)
from app.domains.param_chart_constants import (
    PARAM_CHART_SHARED_CHECK_NOS,
    project_metrics_from_signal_rows,
    shared_categories,
    shared_metric_defs,
)


@pytest.fixture(autouse=True)
def _clear_pc_cache() -> None:
    from app.domains import param_chart_metrics_store as mstore
    from app.domains import param_chart_token_store as tstore

    pc_cache.reset_param_chart_cache_for_tests()
    mstore.reset_metrics_store_for_tests()
    tstore.reset_token_store_for_tests()
    yield
    pc_cache.reset_param_chart_cache_for_tests()
    mstore.reset_metrics_store_for_tests()
    tstore.reset_token_store_for_tests()


def test_shared_allowlist_is_customer_photo_set() -> None:
    expected = {
        1,
        7,
        8,
        10,
        15,
        16,
        17,
        18,
        19,
        23,
        26,
        27,
        33,
        36,
        41,
        42,
        44,
        45,
        48,
        49,
        50,
        51,
        52,
        57,
        59,
        61,
        64,
        68,
        69,
        70,
    }
    assert PARAM_CHART_SHARED_CHECK_NOS == frozenset(expected)
    defs = shared_metric_defs()
    assert len(defs) == 30
    assert {m["check_no"] for m in defs} == expected
    cats = shared_categories()
    assert "Data & Charts Watch" in cats
    assert "Trade Discipline Check" not in cats


def test_project_metrics_filters_to_shared_list() -> None:
    defs = shared_metric_defs()
    first = defs[0]
    rows = [
        {
            "id": first["id"],
            "check_no": first["check_no"],
            "value": 12,
            "passed": True,
            "category": first["category"],
        },
        {"id": "some_other", "check_no": 99, "value": 1, "passed": False},
    ]
    out = project_metrics_from_signal_rows(rows)
    assert first["id"] in out
    assert "some_other" not in out


def test_trading_days_skeleton_has_empty_premiums() -> None:
    days = [
        _empty_day(d, day_index=i)
        for i, d in enumerate(_trading_days_in_month(2026, 8), start=1)
    ]
    assert len(days) >= 18
    assert days[0]["day_index"] == 1
    assert days[0]["open"] is None
    assert days[0]["ce"] is None
    assert "mock" not in ParamChartConfig.from_dict(None).to_admin_dict()


def test_entry_premium_zero_is_preserved() -> None:
    cfg = ParamChartConfig.from_dict(
        {"entry_ce_premium": 0, "entry_pe_premium": 0}
    )
    assert cfg.entry_ce_premium == 0.0
    assert cfg.entry_pe_premium == 0.0
    assert cfg.entry_total() == 0.0


def test_trading_days_skip_weekend_and_static_holiday() -> None:
    from app.domains.options_lab_bots import NSE_HOLIDAYS_STATIC

    days = _trading_days_in_month(2026, 1, holidays=NSE_HOLIDAYS_STATIC)
    assert all(d.weekday() < 5 for d in days)
    if date(2026, 1, 26) in NSE_HOLIDAYS_STATIC:
        assert date(2026, 1, 26) not in days


def test_kite_candle_parse_preserves_dates() -> None:
    hist = {
        "ok": True,
        "data": {
            "candles": [
                ["2026-08-03T00:00:00+0530", 57000.0, 57200.0, 56800.0, 57100.0, 100],
                ["2026-08-04T00:00:00+0530", 57100.0, 57300.0, 56900.0, 57250.0, 110],
            ]
        },
    }
    by_day = _candle_ohlc_by_day(hist)
    assert by_day["2026-08-03"]["close"] == 57100.0
    assert by_day["2026-08-04"]["high"] == 57300.0
    assert by_day["2026-08-03"]["volume"] == 100.0
    assert by_day["2026-08-04"]["volume"] == 110.0


def test_attach_day_deltas() -> None:
    from app.domains.param_chart import _attach_day_deltas

    rows = _attach_day_deltas(
        [
            {"date": "2026-08-03", "close": 100.0},
            {"date": "2026-08-04", "close": 105.0},
            {"date": "2026-08-05", "close": 102.0},
        ]
    )
    assert rows[0]["chg"] is None
    assert rows[1]["chg"] == 5.0
    assert rows[2]["chg"] == -3.0


def test_metrics_by_day_strip_keeps_bars_lean() -> None:
    from app.domains.param_chart import _slim_stream_frame
    from app.domains.param_chart_metrics_store import (
        normalize_metrics_by_day,
        strip_embedded_metrics,
    )

    days = [
        {
            "date": "2026-08-25T09:15",
            "close": 1,
            "metrics": {"chk_008": {"id": "chk_008", "value": 1.3}},
        },
        {
            "date": "2026-08-25T09:16",
            "close": 2,
            "metrics": {"chk_008": {"id": "chk_008", "value": 1.3}},
        },
    ]
    lean = strip_embedded_metrics(days)
    assert lean[0]["metrics"] == {}
    assert lean[0]["close"] == 1
    mbd = normalize_metrics_by_day(
        {"2026-08-25": {"chk_008": {"id": "chk_008", "value": 1.3}}}
    )
    assert "chk_008" in mbd["2026-08-25"]
    slim = _slim_stream_frame(
        {
            "ok": True,
            "year": 2026,
            "month": 8,
            "interval": "1m",
            "today": "2026-08-25",
            "days": lean
            + [{"date": "2026-08-24T15:29", "close": 0, "metrics": {}}],
            "live_metrics": {"chk_008": {"id": "chk_008", "value": 1.4}},
            "metrics_by_day": mbd,
        }
    )
    assert slim["stream_patch"] is True
    assert all(str(d["date"]).startswith("2026-08-25") for d in slim["days"])
    assert len(slim["days"]) == 2
    assert slim["metrics_by_day"]["2026-08-25"]["chk_008"]["value"] == 1.4

    building = _slim_stream_frame(
        {
            "ok": True,
            "building": True,
            "year": 2026,
            "month": 8,
            "interval": "1m",
            "today": "2026-08-25",
            "days": lean,
            "live_metrics": {"chk_008": {"id": "chk_008", "value": 1.4}},
        }
    )
    assert building["days"] == []
    assert building["building"] is True
    assert building["live_metrics"]["chk_008"]["value"] == 1.4


def test_interval_storage_keys_preserve_minute_vs_month() -> None:
    from app.domains.param_chart_cache import _pack_key
    from app.domains.param_chart_candle_store import _month_key

    assert _month_key(256265, 2026, 8, "1m").endswith("/1m/2026-08.json")
    assert _month_key(256265, 2026, 8, "1M").endswith("/1M/2026.json")
    assert _pack_key("tenant", 2026, 8, "1m").endswith(":1m:2026-08")
    assert _pack_key("tenant", 2026, 8, "1M").endswith(":1M:2026")


def test_minute_hist_chunk_helpers() -> None:
    from datetime import date

    from app.domains.param_chart import (
        _iter_date_chunks,
        _merge_kite_hist_chunks,
        _today_bar_indices,
    )

    chunks = _iter_date_chunks(date(2026, 8, 1), date(2026, 8, 5), chunk_days=2)
    assert chunks == [
        (date(2026, 8, 1), date(2026, 8, 2)),
        (date(2026, 8, 3), date(2026, 8, 4)),
        (date(2026, 8, 5), date(2026, 8, 5)),
    ]
    merged = _merge_kite_hist_chunks(
        [
            {
                "ok": True,
                "data": {
                    "candles": [
                        ["2026-08-01T09:15:00", 1, 2, 0.5, 1.5, 10],
                        ["2026-08-01T09:16:00", 1.5, 2, 1, 1.8, 11],
                    ]
                },
            },
            {
                "ok": True,
                "data": {
                    "candles": [
                        ["2026-08-01T09:16:00", 1.5, 2, 1, 1.8, 11],  # dup
                        ["2026-08-01T09:17:00", 1.8, 2.1, 1.7, 2.0, 12],
                    ]
                },
            },
        ]
    )
    assert merged is not None
    assert len(merged["data"]["candles"]) == 3

    days = [
        {"date": "2026-08-24T15:29"},
        {"date": "2026-08-25T09:15"},
        {"date": "2026-08-25T15:29"},
        {"date": "2026-08-25"},
    ]
    assert _today_bar_indices(days, "2026-08-25") == [1, 2, 3]
    assert _today_bar_indices(days, "2026-08-24") == [0]


def test_normalize_interval_and_monthly_aggregate() -> None:
    from app.domains.param_chart import (
        _aggregate_monthly,
        _aggregate_weekly,
        _normalize_interval,
        _option_expiry_ym,
        _option_matches_chart_month,
    )

    assert _normalize_interval("1m") == "1m"
    assert _normalize_interval("minute") == "1m"
    assert _normalize_interval("5m") == "5m"
    assert _normalize_interval("15minute") == "15m"
    assert _normalize_interval("1M") == "1M"  # monthly stays distinct from 1m
    assert _normalize_interval("1h") == "1H"
    assert _normalize_interval("daily") == "1D"
    assert _normalize_interval("week") == "1W"
    assert _normalize_interval("month") == "1M"
    assert _option_expiry_ym("NFO:NIFTY26JUL24000CE") == (2026, 7)
    assert _option_matches_chart_month("NFO:NIFTY26JUL24000CE", 2026, 7) is True
    assert _option_matches_chart_month("NFO:NIFTY26JUL24000CE", 2026, 8) is False
    monthly = _aggregate_monthly(
        {
            "2026-01-02": {"open": 100, "high": 110, "low": 99, "close": 105, "volume": 10},
            "2026-01-31": {"open": 105, "high": 120, "low": 104, "close": 118, "volume": 20},
            "2026-02-01": {"open": 118, "high": 119, "low": 110, "close": 112},
        }
    )
    assert monthly["2026-01"]["open"] == 100
    assert monthly["2026-01"]["close"] == 118
    assert monthly["2026-01"]["high"] == 120
    assert monthly["2026-01"]["volume"] == 30
    assert monthly["2026-02"]["close"] == 112

    # 2026-01-02 Fri + 2026-01-05 Mon → two ISO weeks (Mon 2025-12-29 and Mon 2026-01-05)
    weekly = _aggregate_weekly(
        {
            "2026-01-02": {"open": 100, "high": 110, "low": 99, "close": 105, "volume": 10},
            "2026-01-05": {"open": 106, "high": 112, "low": 105, "close": 111, "volume": 5},
            "2026-01-06": {"open": 111, "high": 115, "low": 110, "close": 114, "volume": 7},
        }
    )
    assert weekly["2025-12-29"]["open"] == 100
    assert weekly["2025-12-29"]["close"] == 105
    assert weekly["2026-01-05"]["open"] == 106
    assert weekly["2026-01-05"]["close"] == 114
    assert weekly["2026-01-05"]["high"] == 115
    assert weekly["2026-01-05"]["volume"] == 12


def test_apply_premiums_vs_entry() -> None:
    row = _apply_premiums(
        {"date": "2026-08-03"},
        ce=800.0,
        pe=700.0,
        entry_total=1800.0,
    )
    assert row["total"] == 1500.0
    assert row["pct_vs_entry"] == pytest.approx(16.666, rel=1e-3)


def test_suggest_option_symbols_banknifty() -> None:
    when = datetime(2026, 8, 15, tzinfo=ZoneInfo("Asia/Kolkata"))
    ce, pe = suggest_option_symbols("NSE:NIFTY BANK", 57000, when=when)
    assert ce == "NFO:BANKNIFTY26AUG57000CE"
    assert pe == "NFO:BANKNIFTY26AUG57000PE"


def test_param_chart_config_defaults() -> None:
    cfg = ParamChartConfig.from_dict({"mock": True})  # legacy key ignored
    assert cfg.underlying_symbol == "NSE:NIFTY BANK"
    assert cfg.entry_total() == 1800.0
    d = cfg.to_admin_dict()
    assert d["strike_step"] == 100
    assert "mock" not in d


@pytest.mark.asyncio
async def test_param_chart_clears_strike_on_underlying_change(monkeypatch) -> None:
    """BANKNIFTY strike 57000 must not rederive NIFTY…57000CE after switch."""
    import uuid
    from unittest.mock import AsyncMock, MagicMock

    from app.db.models import Role
    from app.domains.param_chart import ParamChartService
    from app.domains.param_chart_constants import PARAM_CHART_SETTINGS_KEY
    from app.tenancy.context import TenantContext

    tenant_id = uuid.uuid4()
    session = MagicMock()
    session.info = {"tenant_id": tenant_id}
    context = TenantContext(
        tenant_id=tenant_id,
        user_id="tester",
        role=Role.platform_admin,
        auth_org_id="org-test",
    )
    svc = ParamChartService(session, context)
    written: dict = {}

    async def _settings(_tool):
        return {
            PARAM_CHART_SETTINGS_KEY: {
                "underlying_symbol": "NSE:NIFTY BANK",
                "underlying_label": "BANKNIFTY",
                "fut_symbol": "NFO:BANKNIFTY26AUGFUT",
                "strike_step": 100,
                "strike": 57000,
                "ce_symbol": "NFO:BANKNIFTY26AUG57000CE",
                "pe_symbol": "NFO:BANKNIFTY26AUG57000PE",
                "year": 2026,
                "month": 8,
                "interval": "1D",
            },
            "engine_enabled": True,
            "options_lab": {"mock": True},
        }

    async def _patch(_tool, patch):
        written.clear()
        written.update(patch)
        return patch

    tool = MagicMock()
    monkeypatch.setattr(svc.engine, "_signal_engine_tool", AsyncMock(return_value=tool))
    monkeypatch.setattr(svc.engine, "_tool_settings", _settings)
    monkeypatch.setattr(svc.engine, "_patch_tool_settings", _patch)
    monkeypatch.setattr(svc.engine, "_load_setup", AsyncMock(return_value=(None, True, True)))
    monkeypatch.setattr(
        svc,
        "get_admin_config",
        AsyncMock(return_value={"ok": True, "config": {}}),
    )

    result = await svc.update_admin_config({"underlying_symbol": "NSE:NIFTY 50"})
    assert result["ok"] is True
    chart = written[PARAM_CHART_SETTINGS_KEY]
    assert chart["underlying_symbol"] == "NSE:NIFTY 50"
    assert chart["strike"] == 24000
    assert "57000" not in chart["ce_symbol"]
    assert chart["ce_symbol"].endswith("24000CE")
    assert chart["pe_symbol"].endswith("24000PE")
    # Must not rewrite sibling desk keys.
    assert "engine_enabled" not in written
    assert "options_lab" not in written


@pytest.mark.asyncio
async def test_candle_dump_local_roundtrip(tmp_path, monkeypatch) -> None:
    from app.domains import param_chart_candle_store as store

    monkeypatch.setenv("DOCUMENT_BUCKET", "")
    monkeypatch.setattr(store, "_local_root", lambda: tmp_path / "candles")
    # Force no S3 path.
    monkeypatch.setattr(store, "_s3_client_and_bucket", lambda: None)

    hist = {
        "ok": True,
        "data": {
            "candles": [
                ["2026-08-03T00:00:00+0530", 1, 2, 0.5, 1.5, 10],
            ]
        },
    }
    uri = await store.put_month_candles(260105, year=2026, month=8, hist=hist)
    assert uri
    got = await store.get_month_candles(260105, year=2026, month=8)
    assert got is not None
    assert got["candle_count"] == 1
    assert store.should_refresh_month_dump(got, year=2025, month=1) is False


@pytest.mark.asyncio
async def test_month_pack_cache_roundtrip() -> None:
    await pc_cache.set_month_pack(
        "t1",
        year=2026,
        month=8,
        payload={"ok": True, "days": [{"date": "2026-08-01"}]},
    )
    got = await pc_cache.get_month_pack("t1", year=2026, month=8)
    assert got is not None
    assert got["days"][0]["date"] == "2026-08-01"
    await pc_cache.touch_watcher("t1")
    assert await pc_cache.watcher_alive("t1") is True


@pytest.mark.asyncio
async def test_metrics_cold_store_survives_skeleton_rebuild(tmp_path, monkeypatch) -> None:
    """EOD metrics must merge back after Redis pack rebuild / interval switch."""
    from app.domains import param_chart_metrics_store as mstore
    from app.domains.param_chart_metrics_store import merge_metrics_into_days

    monkeypatch.setattr(mstore, "_local_root", lambda: tmp_path / "metrics")
    monkeypatch.setattr(mstore, "_s3_client_and_bucket", lambda: None)

    uri = await mstore.upsert_day_metrics(
        "tenant-a",
        year=2026,
        month=8,
        day="2026-08-25",
        metrics={"m1": {"check_no": 1, "value": 12.5}},
    )
    assert uri is not None
    stored = await mstore.get_month_metrics("tenant-a", year=2026, month=8)
    assert stored is not None
    assert stored["2026-08-25"]["m1"]["value"] == 12.5

    # Fresh skeleton (as after TTL / interval switch) has empty metrics.
    skeleton = [
        _empty_day(date(2026, 8, 25), day_index=1),
        _empty_day(date(2026, 8, 26), day_index=2),
    ]
    assert skeleton[0]["metrics"] == {}
    merged = merge_metrics_into_days(skeleton, stored)
    assert merged[0]["metrics"]["m1"]["value"] == 12.5
    assert merged[1]["metrics"] == {}


@pytest.mark.asyncio
async def test_metrics_read_cache_avoids_repeated_io(tmp_path, monkeypatch) -> None:
    """SSE month_state must not hit cold storage on every 500ms tick."""
    from app.domains import param_chart_metrics_store as mstore

    monkeypatch.setattr(mstore, "_local_root", lambda: tmp_path / "metrics")
    monkeypatch.setattr(mstore, "_s3_client_and_bucket", lambda: None)

    loads = {"n": 0}
    real_load = mstore._load_month_metrics

    async def counting_load(*args, **kwargs):
        loads["n"] += 1
        return await real_load(*args, **kwargs)

    monkeypatch.setattr(mstore, "_load_month_metrics", counting_load)

    await mstore.upsert_day_metrics(
        "tenant-b",
        year=2026,
        month=8,
        day="2026-08-25",
        metrics={"m1": {"value": 1}},
    )
    # upsert refreshes memo; subsequent gets must not reload.
    loads["n"] = 0
    for _ in range(5):
        got = await mstore.get_month_metrics("tenant-b", year=2026, month=8)
        assert got is not None
        assert got["2026-08-25"]["m1"]["value"] == 1
    assert loads["n"] == 0

    # Expired memo forces one cold read.
    ck = mstore._cache_key("tenant-b", 2026, 8)
    mstore._read_cache[ck] = (0.0, None)  # expired
    await mstore.get_month_metrics("tenant-b", year=2026, month=8)
    assert loads["n"] == 1
    await mstore.get_month_metrics("tenant-b", year=2026, month=8)
    assert loads["n"] == 1


@pytest.mark.asyncio
async def test_heal_option_symbols_is_in_memory_only() -> None:
    """SSE month_state must not DB-write on every heal."""
    import uuid
    from unittest.mock import AsyncMock, MagicMock

    from app.db.models import Role
    from app.domains.param_chart import ParamChartService
    from app.tenancy.context import TenantContext

    tenant_id = uuid.uuid4()
    session = MagicMock()
    session.info = {"tenant_id": tenant_id}
    context = TenantContext(
        tenant_id=tenant_id,
        user_id="tester",
        role=Role.platform_admin,
        auth_org_id="org-test",
    )
    svc = ParamChartService(session, context)
    svc.update_admin_config = AsyncMock(
        side_effect=AssertionError("heal must not persist")
    )
    cfg = ParamChartConfig(
        underlying_symbol="NSE:NIFTY 50",
        strike=24500,
        ce_symbol="NFO:NIFTY26JUL24500CE",
        pe_symbol="NFO:NIFTY26JUL24500PE",
        year=2026,
        month=8,
    )
    healed = await svc._heal_option_symbols_for_month(cfg, year=2026, month=8)
    svc.update_admin_config.assert_not_called()
    assert "AUG" in healed.ce_symbol.upper()
    assert "AUG" in healed.pe_symbol.upper()
    assert healed.ce_symbol != cfg.ce_symbol


@pytest.mark.asyncio
async def test_symbol_token_store_survives_for_expired_options(
    tmp_path, monkeypatch
) -> None:
    """CE/PE tokens captured while live must resolve after quote/instruments miss."""
    from app.domains import param_chart_token_store as tstore

    monkeypatch.setattr(
        tstore,
        "_local_path",
        lambda exchange, ts: tmp_path / exchange / f"{ts}.json",
    )
    monkeypatch.setattr(tstore, "_s3_client_and_bucket", lambda: None)

    sym = "NFO:NIFTY26JUL24500CE"
    assert await tstore.get_instrument_token(sym) is None
    uri = await tstore.put_instrument_token(sym, 12345678)
    assert uri is not None
    # Clear memo so we hit disk (simulates new process after expiry).
    tstore.reset_token_store_for_tests()
    monkeypatch.setattr(
        tstore,
        "_local_path",
        lambda exchange, ts: tmp_path / exchange / f"{ts}.json",
    )
    monkeypatch.setattr(tstore, "_s3_client_and_bucket", lambda: None)
    assert await tstore.get_instrument_token(sym) == 12345678


@pytest.mark.asyncio
async def test_metrics_persist_due_is_peek_not_acquire(monkeypatch) -> None:
    """Worker must peek before opening work; SET NX stays inside persist."""
    calls: list[tuple] = []

    class _Redis:
        async def get(self, key):
            calls.append(("get", key))
            return b"1"  # gate already held

        async def set(self, *args, **kwargs):
            calls.append(("set", args, kwargs))
            return True

    async def fake_get_redis():
        return _Redis()

    monkeypatch.setattr(pc_cache, "get_redis", fake_get_redis)
    assert await pc_cache.metrics_persist_due("t1", day="2026-08-26") is False
    assert await pc_cache.eod_finalize_due("t1", day="2026-08-26") is False
    assert all(c[0] == "get" for c in calls)
    assert not any(c[0] == "set" for c in calls)


@pytest.mark.asyncio
async def test_param_chart_read_config_uses_setup_memo(monkeypatch) -> None:
    import uuid
    from unittest.mock import AsyncMock, MagicMock

    from app.db.models import Role
    from app.domains import signal_engine_cache as signal_cache
    from app.domains.param_chart import ParamChartService
    from app.domains.param_chart_constants import PARAM_CHART_SETTINGS_KEY
    from app.tenancy.context import TenantContext

    signal_cache.reset_signal_cache_for_tests()
    tenant = uuid.uuid4()
    await signal_cache.set_metric(
        str(tenant),
        "setup",
        "medium",
        {
            "settings": {
                PARAM_CHART_SETTINGS_KEY: {
                    "year": 2026,
                    "month": 8,
                    "interval": "1D",
                    "underlying_symbol": "NSE:NIFTY 50",
                }
            },
            "has_broker": True,
            "team_ready": True,
        },
    )
    session = MagicMock()
    session.info = {"tenant_id": tenant}
    ctx = TenantContext(
        tenant_id=tenant,
        user_id="u",
        role=Role.tenant_admin,
        auth_org_id="org",
    )
    svc = ParamChartService(session, ctx)
    svc.engine._signal_engine_tool = AsyncMock(side_effect=AssertionError("no DB"))
    svc.engine._load_setup = AsyncMock(side_effect=AssertionError("setup warm"))
    cfg = await svc._read_config()
    assert cfg.year == 2026
    assert cfg.month == 8
    assert cfg.underlying_symbol == "NSE:NIFTY 50"
    signal_cache.reset_signal_cache_for_tests()


def test_merge_desk_instrument_prefers_shared_board() -> None:
    from app.domains.param_chart_constants import (
        DESK_INSTRUMENT_SETTINGS_KEY,
        PARAM_CHART_SETTINGS_KEY,
        merge_desk_instrument_into_chart,
    )

    merged = merge_desk_instrument_into_chart(
        {
            PARAM_CHART_SETTINGS_KEY: {
                "underlying_symbol": "NSE:NIFTY BANK",
                "year": 2026,
                "month": 8,
                "interval": "1D",
                "entry_ce_premium": 100.0,
            },
            DESK_INSTRUMENT_SETTINGS_KEY: {
                "underlying_symbol": "NSE:NIFTY 50",
                "ce_symbol": "NFO:NIFTY26AUG24000CE",
                "pe_symbol": "NFO:NIFTY26AUG24000PE",
            },
        }
    )
    assert merged["underlying_symbol"] == "NSE:NIFTY 50"
    assert merged["ce_symbol"] == "NFO:NIFTY26AUG24000CE"
    assert merged["year"] == 2026
    assert merged["entry_ce_premium"] == 100.0


@pytest.mark.asyncio
async def test_refresh_overlay_from_cache_is_book_only() -> None:
    import uuid

    from app.domains import signal_engine_cache as signal_cache
    from app.domains.kite_ticker_hub import write_ticker_rows
    from app.domains.param_chart import _ist_today, refresh_overlay_from_cache
    from app.domains.param_chart_constants import PARAM_CHART_SETTINGS_KEY

    signal_cache.reset_signal_cache_for_tests()
    tenant = str(uuid.uuid4())
    today_d = _ist_today()
    today = today_d.isoformat()
    await signal_cache.set_metric(
        tenant,
        "setup",
        "medium",
        {
            "settings": {
                PARAM_CHART_SETTINGS_KEY: {
                    "year": today_d.year,
                    "month": today_d.month,
                    "interval": "1D",
                    "underlying_symbol": "NSE:NIFTY 50",
                    "ce_symbol": "NFO:NIFTY26AUG24000CE",
                    "pe_symbol": "NFO:NIFTY26AUG24000PE",
                }
            }
        },
    )
    await write_ticker_rows(
        tenant,
        {
            "NSE:NIFTY 50": {
                "last_price": 24150.0,
                "ohlc": {"open": 24100.0, "high": 24200.0, "low": 24050.0, "close": 24120.0},
            },
            "NFO:NIFTY26AUG24000CE": {"last_price": 110.5},
            "NFO:NIFTY26AUG24000PE": {"last_price": 95.25},
        },
    )
    await pc_cache.set_month_pack(
        tenant,
        year=today_d.year,
        month=today_d.month,
        interval="1D",
        payload={
            "year": today_d.year,
            "month": today_d.month,
            "interval": "1D",
            "days": [{"date": today, "open": 1, "high": 1, "low": 1, "close": 1}],
        },
    )
    frame = await refresh_overlay_from_cache(tenant)
    assert frame is not None
    assert frame["stream_patch"] is True
    assert frame["kite_live"]["source"] == "book"
    assert frame["kite_live"]["spot"] == 24150.0
    assert frame["kite_live"]["ce"] == 110.5
    assert frame["kite_live"]["pe"] == 95.25
    assert frame["days"]
    assert frame["days"][-1]["close"] == 24150.0
    cached = await pc_cache.get_overlay(tenant)
    assert cached == frame
    signal_cache.reset_signal_cache_for_tests()


@pytest.mark.asyncio
async def test_refresh_overlay_pack_miss_is_not_building() -> None:
    """Pack TTL miss must not freeze SSE on building=True / empty days."""
    import uuid

    from app.domains import signal_engine_cache as signal_cache
    from app.domains.kite_ticker_hub import write_ticker_rows
    from app.domains.param_chart import refresh_overlay_from_cache
    from app.domains.param_chart_constants import PARAM_CHART_SETTINGS_KEY

    signal_cache.reset_signal_cache_for_tests()
    tenant = str(uuid.uuid4())
    await signal_cache.set_metric(
        tenant,
        "setup",
        "medium",
        {
            "settings": {
                PARAM_CHART_SETTINGS_KEY: {
                    "interval": "15m",
                    "underlying_symbol": "NSE:NIFTY 50",
                    "year": 2026,
                    "month": 8,
                }
            }
        },
    )
    await write_ticker_rows(
        tenant,
        {"NSE:NIFTY 50": {"last_price": 24150.0}},
    )
    frame = await refresh_overlay_from_cache(tenant)
    assert frame is not None
    assert frame["building"] is False
    assert frame["days"] == []
    assert frame["kite_live"]["spot"] == 24150.0
    signal_cache.reset_signal_cache_for_tests()


@pytest.mark.asyncio
async def test_apply_today_overlay_paints_latest_today_bar() -> None:
    from app.domains.param_chart import ParamChartConfig, apply_today_overlay

    cfg = ParamChartConfig(
        underlying_symbol="NSE:NIFTY 50",
        entry_ce_premium=100.0,
        entry_pe_premium=100.0,
    )
    pack = {
        "year": 2099,
        "month": 1,
        "days": [
            {"date": "2099-01-01", "close": 1},
        ],
    }
    live = {
        "live_metrics": {"chk_008": {"id": "chk_008", "value": 1.4}},
        "kite_live": {"source": "book", "spot": 10, "ce": 2, "pe": 3, "error": None},
        "spot_close": 10.0,
        "ce": 2.0,
        "pe": 3.0,
        "total": 5.0,
        "pct": 97.5,
    }
    out = apply_today_overlay(cfg, pack, live)
    assert out["live_metrics"]["chk_008"]["value"] == 1.4
    assert out["kite_live"]["source"] == "book"


@pytest.mark.asyncio
async def test_param_chart_source_merges_without_dropping_signal(monkeypatch) -> None:
    import asyncio
    import contextlib

    from app.domains.kite_ticker_hub import (
        SOURCE_PARAM_CHART,
        SOURCE_SIGNAL,
        get_kite_ticker_hub,
        reset_kite_ticker_hub_for_tests,
    )

    class _Settings:
        kite_ticker_enabled = True

    monkeypatch.setattr("app.core.settings.get_settings", lambda: _Settings())
    reset_kite_ticker_hub_for_tests()
    hub = get_kite_ticker_hub()
    hub._enabled = True
    await hub.sync_tenant(
        "t-pc",
        api_key="k",
        access_token="tok",
        token_to_symbol={1: "NSE:NIFTY 50", 2: "NFO:NIFTYFUT"},
        source=SOURCE_SIGNAL,
    )
    feed = hub._tenants["t-pc"]
    if feed.task is not None:
        feed.task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await feed.task
        feed.task = asyncio.create_task(asyncio.sleep(3600))
    await hub.sync_tenant(
        "t-pc",
        api_key="k",
        access_token="tok",
        token_to_symbol={5: "NFO:NIFTY26AUG24000CE", 6: "NFO:NIFTY26AUG24000PE"},
        source=SOURCE_PARAM_CHART,
    )
    assert feed.desired_tokens == {1, 2, 5, 6}
    await hub.sync_tenant(
        "t-pc",
        api_key="k",
        access_token="tok",
        token_to_symbol={},
        source=SOURCE_PARAM_CHART,
    )
    assert 1 in feed.desired_tokens
    assert 2 in feed.desired_tokens
    assert 5 not in feed.desired_tokens
    feed.task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await feed.task
    reset_kite_ticker_hub_for_tests()
