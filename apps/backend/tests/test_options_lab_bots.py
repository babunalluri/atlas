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
    entry_conditions_ok,
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
