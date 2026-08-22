"""Tests for Options Lab paper bots (Wave 2)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.domains import signal_engine_cache as cache
from app.domains.options_lab_bots import (
    append_run_log,
    bot_due_for_auto,
    create_bot,
    days_to_expiry_from_fut,
    delete_bot,
    dte_exit_due,
    entry_conditions_ok,
    event_avoid_reason,
    flip_legs_for_exit,
    in_schedule,
    list_bots,
    normalize_bot,
    reset_bots_armed_for_tests,
    update_bot,
)
from app.domains.options_lab_templates import (
    build_template_legs,
    enrich_legs_from_chain,
)


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    cache.reset_signal_cache_for_tests()
    reset_bots_armed_for_tests()
    yield
    cache.reset_signal_cache_for_tests()
    reset_bots_armed_for_tests()


def test_iron_condor_geometry() -> None:
    legs = build_template_legs("iron_condor", atm=24500, strike_step=50, width_steps=1)
    assert len(legs) == 4
    strikes = [leg["strike"] for leg in legs]
    assert strikes == [24450.0, 24400.0, 24550.0, 24600.0]


def test_enrich_legs_from_chain_attaches_symbol() -> None:
    skeleton = build_template_legs("long_straddle", atm=24500, strike_step=50)
    rows = [
        {
            "strike": 24500,
            "ce": {"symbol": "NFO:CE", "ltp": 120},
            "pe": {"symbol": "NFO:PE", "ltp": 110},
        }
    ]
    legs = enrich_legs_from_chain(skeleton, rows)
    assert legs[0]["symbol"] == "NFO:CE"
    assert legs[0]["premium"] == 120
    assert legs[1]["symbol"] == "NFO:PE"


def test_days_to_expiry_from_fut_monthly() -> None:
    # Last Thursday Aug 2026 = 27 Aug. Ref 22 Aug → 5 days.
    now = datetime(2026, 8, 22, 12, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    dte = days_to_expiry_from_fut("NFO:NIFTY26AUGFUT", now=now)
    assert dte == 5


def test_entry_gates_require_metric() -> None:
    bot = normalize_bot({"name": "g", "template": "iron_condor", "entry": {"min_ivp": 40}})
    ok, reason = entry_conditions_ok(bot, ivp=None, pcr=1.0, dte=7)
    assert ok is False
    assert "IVP" in reason
    ok2, _ = entry_conditions_ok(bot, ivp=55, pcr=1.0, dte=7)
    assert ok2 is True


def test_entry_rejects_expired_fut() -> None:
    bot = normalize_bot({"name": "g", "template": "iron_condor"})
    ok, reason = entry_conditions_ok(bot, ivp=50, pcr=1.0, dte=-1)
    assert ok is False
    assert "expired" in reason.lower()


def test_schedule_weekday_window() -> None:
    # Saturday 10:00 IST — default Mon–Fri schedule → closed
    sat = datetime(2026, 8, 22, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    assert sat.weekday() == 5
    assert in_schedule(None, now=sat) is False
    fri = datetime(2026, 8, 21, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    assert fri.weekday() == 4
    assert in_schedule(None, now=fri) is True


def test_bot_due_respects_kill_and_live() -> None:
    paper = normalize_bot(
        {"name": "p", "template": "iron_condor", "enabled": True, "mode": "paper"}
    )
    # Force schedule open
    fri = datetime(2026, 8, 21, 11, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    due, _ = bot_due_for_auto(paper, now=fri)
    assert due is True

    live = normalize_bot(
        {"name": "l", "template": "iron_condor", "enabled": True, "mode": "live"}
    )
    due_live, reason = bot_due_for_auto(live, now=fri)
    assert due_live is False
    assert "Live" in reason

    killed = normalize_bot(
        {"name": "k", "template": "iron_condor", "enabled": True, "kill": True}
    )
    assert killed["enabled"] is False
    due_k, _ = bot_due_for_auto(killed, now=fri)
    assert due_k is False


@pytest.mark.asyncio
async def test_create_list_update_delete_bot() -> None:
    created = await create_bot(
        "tenant-bots",
        {
            "name": "IC bot",
            "template": "iron_condor",
            "mode": "paper",
            "enabled": True,
            "stop_pct": 40,
            "profit_pct": 50,
        },
    )
    assert created["ok"] is True
    bot_id = created["bot"]["id"]
    listed = await list_bots("tenant-bots")
    assert listed["count"] == 1
    assert listed["armed_paper"] == 1

    updated = await update_bot(
        "tenant-bots",
        bot_id,
        {"enabled": False, "kill": True},
    )
    assert updated["ok"] is True
    assert updated["bot"]["kill"] is True
    assert updated["bot"]["enabled"] is False

    deleted = await delete_bot("tenant-bots", bot_id)
    assert deleted["ok"] is True
    listed2 = await list_bots("tenant-bots")
    assert listed2["count"] == 0


@pytest.mark.asyncio
async def test_try_claim_bot_run_is_exclusive() -> None:
    from app.domains.options_lab_bots import try_claim_bot_run

    assert await try_claim_bot_run("t-claim", "bot-1", cooldown_sec=60) is True
    assert await try_claim_bot_run("t-claim", "bot-1", cooldown_sec=60) is False
    assert await try_claim_bot_run("t-claim", "bot-2", cooldown_sec=60) is True


def test_append_run_log_disarms_on_auto_fail() -> None:
    bot = normalize_bot(
        {"name": "x", "template": "long_straddle", "enabled": True, "mode": "paper"}
    )
    next_bot = append_run_log(bot, ok=False, message="boom", auto=True)
    assert next_bot["enabled"] is False
    assert next_bot["log"][0]["message"] == "boom"


def test_append_run_log_exit_fail_keeps_armed() -> None:
    bot = normalize_bot(
        {"name": "x", "template": "long_straddle", "enabled": True, "mode": "paper"}
    )
    next_bot = append_run_log(
        bot,
        ok=False,
        message="exit boom",
        auto=True,
        disarm_on_fail=False,
        count_toward_daily=False,
    )
    assert next_bot["enabled"] is True
    assert next_bot["runs_today"] == 0


def test_append_run_log_exit_ok_does_not_burn_daily_cap() -> None:
    bot = normalize_bot(
        {"name": "x", "template": "long_straddle", "enabled": True, "mode": "paper"}
    )
    next_bot = append_run_log(
        bot,
        ok=True,
        message="flat",
        auto=True,
        disarm_on_fail=False,
        count_toward_daily=False,
    )
    assert next_bot["runs_today"] == 0


def test_event_avoid_on_nse_holiday() -> None:
    holiday = datetime(2026, 8, 15, 11, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    reason = event_avoid_reason(now=holiday)
    assert reason is not None
    assert "holiday" in reason.lower()
    plain = datetime(2026, 8, 21, 11, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    assert event_avoid_reason(now=plain) is None


def test_bot_due_skips_on_event_avoid() -> None:
    bot = normalize_bot(
        {
            "name": "ev",
            "template": "iron_condor",
            "enabled": True,
            "mode": "paper",
            "avoid_events": True,
        }
    )
    # Republic Day 2026 is Monday — inside default schedule, blocked by event-avoid.
    holiday = datetime(2026, 1, 26, 11, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    due, reason = bot_due_for_auto(bot, now=holiday)
    assert due is False
    assert "holiday" in reason.lower()


def test_bot_due_skips_when_open_position() -> None:
    bot = normalize_bot(
        {
            "name": "open",
            "template": "iron_condor",
            "enabled": True,
            "mode": "paper",
        },
        existing={
            "open_position": {
                "legs": [{"side": "buy", "type": "CE", "strike": 24500, "qty": 1}],
            },
        },
    )
    fri = datetime(2026, 8, 21, 11, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    due, reason = bot_due_for_auto(bot, now=fri)
    assert due is False
    assert "open" in reason.lower()


def test_dte_exit_due_and_flip_legs() -> None:
    bot = normalize_bot(
        {"name": "x", "template": "iron_condor", "max_dte_hold": 1}
    )
    due, _ = dte_exit_due(bot, dte=1)
    assert due is True
    not_due, _ = dte_exit_due(bot, dte=5)
    assert not_due is False
    flipped = flip_legs_for_exit(
        [
            {"side": "sell", "type": "CE", "strike": 24500, "qty": 1, "premium": 100},
            {"side": "buy", "type": "CE", "strike": 24600, "qty": 1, "premium": 40},
        ]
    )
    assert [leg["side"] for leg in flipped] == ["buy", "sell"]


def test_partial_placement_tracks_submitted_legs_only() -> None:
    from app.domains.options_lab_bots import (
        legs_from_placement,
        residual_open_legs_after_exit,
    )

    legs = [
        {"side": "buy", "type": "PE", "strike": 24800, "qty": 1},
        {"side": "sell", "type": "PE", "strike": 24600, "qty": 1},
        {"side": "sell", "type": "CE", "strike": 25400, "qty": 1},
        {"side": "buy", "type": "CE", "strike": 25200, "qty": 1},
    ]
    # Buys-first safety skipped sells → partial
    placed = {
        "ok": False,
        "partial": True,
        "orders": [
            {"leg_index": 0, "status": "submitted"},
            {"leg_index": 3, "status": "submitted"},
            {"leg_index": 1, "status": "skipped"},
            {"leg_index": 2, "status": "skipped"},
        ],
    }
    filled = legs_from_placement(legs, placed)
    assert len(filled) == 2
    assert [leg["strike"] for leg in filled] == [24800, 25200]

    # Exit closes only the two shorts that exist — residual stays tracked
    open_book = filled
    exit_placed = {
        "ok": False,
        "partial": True,
        "orders": [
            {"leg_index": 0, "status": "submitted"},
            {"leg_index": 1, "status": "failed"},
        ],
    }
    residual = residual_open_legs_after_exit(open_book, exit_placed)
    assert len(residual) == 1
    assert residual[0]["strike"] == 25200

    # Full mock exit clears
    assert residual_open_legs_after_exit(open_book, {"mock": True, "ok": True}) == []


@pytest.mark.asyncio
async def test_clear_open_position_via_update() -> None:
    created = await create_bot(
        "tenant-clear",
        {"name": "c", "template": "iron_condor", "mode": "paper"},
    )
    assert created["ok"]
    bot_id = created["bot"]["id"]
    from app.domains.options_lab_bots import replace_bot

    bot = dict(created["bot"])
    bot["open_position"] = {
        "legs": [{"side": "buy", "type": "CE", "strike": 24500, "qty": 1}],
    }
    await replace_bot("tenant-clear", bot)
    cleared = await update_bot("tenant-clear", bot_id, {"clear_open_position": True})
    assert cleared["ok"] is True
    assert cleared["bot"].get("open_position") is None


def test_event_avoid_stale_table_logs_but_does_not_block() -> None:
    far = datetime(2030, 1, 26, 11, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    assert event_avoid_reason(now=far) is None


def test_nse_holiday_table_has_weekday_density() -> None:
    from app.domains.options_lab_bots import NSE_HOLIDAYS_STATIC

    for year in (2026, 2027):
        weekdays = sum(
            1 for d in NSE_HOLIDAYS_STATIC if d.year == year and d.weekday() < 5
        )
        assert weekdays >= 8, f"{year} weekday holidays={weekdays}"


@pytest.mark.asyncio
async def test_exit_bot_skips_on_enrich_leg_count_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Guard: shorter enrich result must not reach place (residual index safety)."""
    from types import SimpleNamespace

    from app.domains.options_lab import OptionsLabConfig, OptionsLabService
    from app.domains.options_lab_bots import create_bot, get_bot, replace_bot

    tenant = "tenant-exit-mismatch"
    created = await create_bot(
        tenant,
        {
            "name": "mismatch",
            "template": "iron_condor",
            "mode": "paper",
            "enabled": True,
            "max_dte_hold": 30,
        },
    )
    assert created["ok"]
    bot = dict(created["bot"])
    open_legs = [
        {
            "side": "buy",
            "type": "PE",
            "strike": 24600,
            "qty": 1,
            "symbol": "NFO:PE24600",
        },
        {
            "side": "buy",
            "type": "CE",
            "strike": 25400,
            "qty": 1,
            "symbol": "NFO:CE25400",
        },
    ]
    bot["open_position"] = {
        "legs": open_legs,
        "fut_symbol": "NFO:NIFTY26AUGFUT",
        "underlying_symbol": "NSE:NIFTY 50",
    }
    await replace_bot(tenant, bot)

    context = SimpleNamespace(
        tenant_id="11111111-1111-1111-1111-111111111111",
        user_id="user-a",
    )
    session = SimpleNamespace(info={"tenant_id": context.tenant_id})
    # Force tenant key used by OptionsLabService helpers.
    monkeypatch.setattr(
        "app.domains.options_lab._tenant_key",
        lambda _ctx: tenant,
    )
    service = OptionsLabService(session=session, context=context)  # type: ignore[arg-type]

    async def _cfg() -> OptionsLabConfig:
        return OptionsLabConfig(
            underlying_symbol="NSE:NIFTY 50",
            fut_symbol="NFO:NIFTY26AUGFUT",
            strike_step=50,
            mock=True,
        )

    async def _chain(**_kwargs: object) -> dict:
        return {"ok": True, "mock": True, "rows": [], "fut_symbol": "NFO:NIFTY26AUGFUT"}

    async def _claim(*_a: object, **_k: object) -> bool:
        return True

    def _enrich(legs: list, _rows: list) -> list:
        # Drop one leg but keep a symbol so fallback flip does not restore length.
        assert len(legs) == 2
        return [{**legs[0], "symbol": "NFO:PE24600"}]

    async def _place(*_a: object, **_k: object) -> dict:
        raise AssertionError("place_strategy_orders must not run on count mismatch")

    monkeypatch.setattr(service, "_read_config", _cfg)
    monkeypatch.setattr(service, "chain_snapshot", _chain)
    monkeypatch.setattr(service, "place_strategy_orders", _place)
    monkeypatch.setattr(
        "app.domains.options_lab_bots.try_claim_bot_run",
        _claim,
    )
    monkeypatch.setattr(
        "app.domains.options_lab_templates.enrich_legs_from_chain",
        _enrich,
    )
    monkeypatch.setattr(
        "app.domains.options_lab_bots.days_to_expiry_from_fut",
        lambda *_a, **_k: 1,
    )

    out = await service.exit_bot_if_due(str(bot["id"]))
    assert out.get("skipped") is True
    assert "mismatch" in str(out.get("error") or "").lower()

    got = await get_bot(tenant, str(bot["id"]))
    assert got["ok"] is True
    pos = got["bot"].get("open_position")
    assert isinstance(pos, dict)
    assert len(pos.get("legs") or []) == 2
