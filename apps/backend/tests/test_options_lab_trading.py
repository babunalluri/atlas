"""Unit tests for Options Lab trading helpers (margins / lot size)."""

from __future__ import annotations

import json

import pytest

from app.domains.options_lab_trading import (
    _available_from_margins_payload,
    _basket_margin_totals,
    _margin_total_from_order_payload,
    _split_exchange_symbol,
    estimate_lot_size,
)


def test_estimate_lot_size_by_underlying() -> None:
    assert estimate_lot_size("NIFTY 50") == 75
    assert estimate_lot_size("BANKNIFTY") == 15
    assert estimate_lot_size(root="SENSEX") == 10


def test_margin_payload_parsers() -> None:
    assert _margin_total_from_order_payload([{"total": 10}, {"total": 2.5}]) == 12.5
    assert (
        _available_from_margins_payload(
            {"data": {"equity": {"available": {"live_balance": 99_000}}}}
        )
        == 99_000
    )
    init, final = _basket_margin_totals(
        {"ok": True, "data": {"initial": {"total": 96504.9}, "final": {"total": 34786.7}}}
    )
    assert init == 96504.9
    assert final == 34786.7


@pytest.mark.asyncio
async def test_strategy_margins_prefers_basket(monkeypatch) -> None:
    from app.domains.options_lab_trading import OptionsLabTradingService

    svc = OptionsLabTradingService.__new__(OptionsLabTradingService)

    async def _find(names, *, team_slugs):
        if "get_basket_margins" in names:

            async def _basket(**kwargs):
                assert len(kwargs["orders"]) == 2
                return {
                    "ok": True,
                    "data": {
                        "initial": {"total": 90000},
                        "final": {"total": 30000},
                    },
                }

            return _basket, "get_basket_margins", "signals-ops"
        if "get_order_margins" in names:
            raise AssertionError("per-leg margins should not run when basket succeeds")
        return None, None, None

    monkeypatch.setattr(svc, "_find_tool", _find)
    res = await OptionsLabTradingService.strategy_margins(
        svc,
        legs=[
            {"side": "buy", "qty": 1, "premium": 40, "symbol": "NFO:NIFTY_BUY"},
            {"side": "sell", "qty": 1, "premium": 100, "symbol": "NFO:NIFTY_SELL"},
        ],
        lot_size=75,
        basket=True,
    )
    assert res["basket"] is True
    assert res["margin_needed"] == 90000
    assert res["margin_final"] == 30000
    assert res["source"] == "get_basket_margins"


@pytest.mark.asyncio
async def test_place_basket_buy_wave_then_sell_wave(monkeypatch) -> None:
    import asyncio

    from app.domains.options_lab_trading import OptionsLabTradingService

    svc = OptionsLabTradingService.__new__(OptionsLabTradingService)
    calls: list[str] = []
    lock = asyncio.Lock()

    async def _find(names, *, team_slugs):
        if "place_paper_order" in names:

            async def _place(**kwargs):
                async with lock:
                    calls.append(kwargs["transaction_type"])
                await asyncio.sleep(0.02)
                return {"status": "success", "data": {"order_id": f"id-{len(calls)}"}}

            return _place, "place_paper_order", "paper-trading"
        return None, None, None

    monkeypatch.setattr(svc, "_find_tool", _find)
    res = await OptionsLabTradingService.place_strategy_orders(
        svc,
        legs=[
            {"side": "sell", "qty": 1, "premium": 100, "symbol": "NFO:NIFTY_SELL_A"},
            {"side": "buy", "qty": 1, "premium": 40, "symbol": "NFO:NIFTY_BUY_A"},
            {"side": "sell", "qty": 1, "premium": 100, "symbol": "NFO:NIFTY_SELL_B"},
            {"side": "buy", "qty": 1, "premium": 40, "symbol": "NFO:NIFTY_BUY_B"},
        ],
        lot_size=75,
        confirm=True,
        basket=True,
    )
    assert res["ok"] is True
    assert res["basket"] is True
    assert calls[:2] == ["BUY", "BUY"]
    assert calls[2:] == ["SELL", "SELL"]
    assert any("concurrent buy wave" in w.lower() for w in res["warnings"])


def test_order_submission_result_requires_order_id() -> None:
    from app.domains.options_lab_trading import _order_submission_result

    assert _order_submission_result(None)[0] is False
    assert _order_submission_result({})[0] is False
    assert _order_submission_result({"ok": False, "error": "nope"})[0] is False
    assert _order_submission_result({"status": "REJECTED"})[0] is False
    assert _order_submission_result({"status": "success"})[0] is False
    ok, oid, _ = _order_submission_result({"status": "success", "data": {"order_id": "x"}})
    assert ok is True and oid == "x"


@pytest.mark.asyncio
async def test_strategy_margins_incomplete_falls_back_to_heuristic(monkeypatch) -> None:
    from app.domains.options_lab_trading import OptionsLabTradingService

    svc = OptionsLabTradingService.__new__(OptionsLabTradingService)

    async def _find(names, *, team_slugs):
        if "get_order_margins" in names:

            async def _margins(**kwargs):
                return [{"total": 1000}]

            return _margins, "get_order_margins", "signals-ops"
        return None, None, None

    monkeypatch.setattr(svc, "_find_tool", _find)
    res = await OptionsLabTradingService.strategy_margins(
        svc,
        legs=[
            {"side": "buy", "qty": 1, "premium": 10, "symbol": "NFO:A"},
            {"side": "buy", "qty": 0, "premium": 10, "symbol": "NFO:B"},
        ],
        lot_size=75,
        heuristic={"marginNeeded": 55},
    )
    assert res["estimated"] is True
    assert res["source"] in {"heuristic", "mock_heuristic"}
    assert res["margin_needed"] == 55
    assert any("incomplete" in w.lower() for w in res["warnings"])


def test_split_exchange_symbol() -> None:
    exch, sym = _split_exchange_symbol("NFO:NIFTY26AUG24500CE")
    assert exch == "NFO"
    assert "24500" in sym


def test_build_leg_exit_gtt_long_and_short() -> None:
    from app.domains.options_lab_trading import build_leg_exit_gtt

    long_oco = build_leg_exit_gtt(
        side="buy",
        premium=100,
        quantity=75,
        exchange="NFO",
        tradingsymbol="NIFTY26AUG24500CE",
        product="NRML",
        stop_loss_pct=20,
        target_pct=40,
    )
    assert long_oco is not None
    assert long_oco["trigger_type"] == "two-leg"
    assert long_oco["orders"][0]["transaction_type"] == "SELL"
    assert long_oco["orders"][0]["product"] == "NRML"
    assert long_oco["trigger_values"][0] == 80.0
    assert long_oco["trigger_values"][1] == 140.0

    short_oco = build_leg_exit_gtt(
        side="sell",
        premium=100,
        quantity=75,
        exchange="NFO",
        tradingsymbol="NIFTY26AUG24500CE",
        product="NRML",
        stop_loss_pct=20,
        target_pct=30,
    )
    assert short_oco is not None
    assert short_oco["trigger_type"] == "two-leg"
    # Ascending [target-below, SL-above] to match Kite OCO convention.
    assert short_oco["trigger_values"][0] == 70.0
    assert short_oco["trigger_values"][1] == 120.0
    assert short_oco["orders"][0]["price"] == 70.0
    assert short_oco["orders"][1]["price"] == 120.0
    assert short_oco["orders"][0]["transaction_type"] == "BUY"
    assert short_oco["last_price"] == 100.0
    assert short_oco["trigger_values"][0] < short_oco["last_price"] < short_oco["trigger_values"][1]

    short_sl = build_leg_exit_gtt(
        side="sell",
        premium=50,
        quantity=75,
        exchange="NFO",
        tradingsymbol="NIFTY26AUG24500PE",
        product="NRML",
        stop_loss_pct=25,
        target_pct=None,
    )
    assert short_sl is not None
    assert short_sl["trigger_type"] == "single"
    assert short_sl["orders"][0]["transaction_type"] == "BUY"
    assert short_sl["trigger_values"][0] == 62.5

    assert (
        build_leg_exit_gtt(
            side="buy",
            premium=100,
            quantity=75,
            exchange="NFO",
            tradingsymbol="X",
            product="MIS",
            stop_loss_pct=20,
            target_pct=20,
        )
        is None
    )

    # Tiny % that would round onto entry should nudge one tick.
    nudged = build_leg_exit_gtt(
        side="buy",
        premium=100,
        quantity=75,
        exchange="NFO",
        tradingsymbol="X",
        product="NRML",
        stop_loss_pct=0.01,
        target_pct=None,
    )
    assert nudged is not None
    # 0.01% clamps to 0.5% floor → 99.50
    assert nudged["trigger_values"][0] == 99.5

    # Floor premium: SL cannot exist below min tick — report drop, keep target.
    floor = build_leg_exit_gtt(
        side="buy",
        premium=0.05,
        quantity=75,
        exchange="NFO",
        tradingsymbol="X",
        product="NRML",
        stop_loss_pct=20,
        target_pct=40,
    )
    assert floor is not None
    assert floor["trigger_type"] == "single"
    assert "stop_loss" in (floor.get("dropped") or [])
    assert floor.get("notes")


@pytest.mark.asyncio
async def test_place_orders_mis_skips_gtt(monkeypatch) -> None:
    from app.domains.options_lab_trading import OptionsLabTradingService

    svc = OptionsLabTradingService.__new__(OptionsLabTradingService)

    async def _find(names, *, team_slugs):
        if "place_paper_order" in names:
            return None, None, None
        if "place_order" in names:

            async def _place(**kwargs):
                return {"status": "success", "data": {"order_id": "ord-1"}}

            return _place, "place_order", "live-trading"
        if "place_gtt" in names:
            raise AssertionError("place_gtt must not run for MIS")
        return None, None, None

    monkeypatch.setattr(svc, "_find_tool", _find)
    res = await OptionsLabTradingService.place_strategy_orders(
        svc,
        legs=[{"side": "buy", "qty": 1, "premium": 100, "symbol": "NFO:NIFTY26AUG24500CE"}],
        lot_size=75,
        confirm=True,
        live=True,
        product="MIS",
        stop_loss_pct=20,
    )
    assert res["ok"] is True
    assert res["gtts"] == []
    assert any("NRML only" in w for w in res["warnings"])


@pytest.mark.asyncio
async def test_place_orders_live_creates_gtt(monkeypatch) -> None:
    from app.domains.options_lab_trading import OptionsLabTradingService

    svc = OptionsLabTradingService.__new__(OptionsLabTradingService)
    gtt_calls: list[dict] = []

    async def _find(names, *, team_slugs):
        if "place_paper_order" in names:
            return None, None, None
        if "place_order" in names:

            async def _place(**kwargs):
                return {"status": "success", "data": {"order_id": "ord-1"}}

            return _place, "place_order", "live-trading"
        if "place_gtt" in names:

            async def _gtt(**kwargs):
                gtt_calls.append(kwargs)
                return {"status": "success", "data": {"trigger_id": 99}}

            return _gtt, "place_gtt", "live-trading"
        return None, None, None

    monkeypatch.setattr(svc, "_find_tool", _find)
    res = await OptionsLabTradingService.place_strategy_orders(
        svc,
        legs=[{"side": "buy", "qty": 1, "premium": 100, "symbol": "NFO:NIFTY26AUG24500CE"}],
        lot_size=75,
        confirm=True,
        live=True,
        order_type="MARKET",
        stop_loss_pct=20,
        target_pct=30,
    )
    assert res["ok"] is True
    assert len(gtt_calls) == 1
    assert gtt_calls[0]["trigger_type"] == "two-leg"
    assert res["gtts"][0]["trigger_id"] == 99


@pytest.mark.asyncio
async def test_place_orders_limit_skips_auto_gtt(monkeypatch) -> None:
    from app.domains.options_lab_trading import OptionsLabTradingService

    svc = OptionsLabTradingService.__new__(OptionsLabTradingService)

    async def _find(names, *, team_slugs):
        if "place_paper_order" in names:
            return None, None, None
        if "place_order" in names:

            async def _place(**kwargs):
                return {"status": "success", "data": {"order_id": "ord-1"}}

            return _place, "place_order", "live-trading"
        if "place_gtt" in names:
            raise AssertionError("place_gtt must not run for LIMIT entries")
        return None, None, None

    monkeypatch.setattr(svc, "_find_tool", _find)
    res = await OptionsLabTradingService.place_strategy_orders(
        svc,
        legs=[{"side": "buy", "qty": 1, "premium": 100, "symbol": "NFO:NIFTY26AUG24500CE"}],
        lot_size=75,
        confirm=True,
        live=True,
        order_type="LIMIT",
        stop_loss_pct=20,
        target_pct=30,
    )
    assert res["ok"] is True
    assert res["gtts"] == []
    assert any("MARKET" in w for w in res["warnings"])


@pytest.mark.asyncio
async def test_place_orders_paper_skips_gtt(monkeypatch) -> None:
    from app.domains.options_lab_trading import OptionsLabTradingService

    svc = OptionsLabTradingService.__new__(OptionsLabTradingService)

    async def _find(names, *, team_slugs):
        if "place_paper_order" in names:

            async def _place(**kwargs):
                return {"status": "success", "data": {"order_id": "p1"}}

            return _place, "place_paper_order", "paper-trading"
        if "place_gtt" in names:
            raise AssertionError("place_gtt should not be looked up on paper path")
        return None, None, None

    monkeypatch.setattr(svc, "_find_tool", _find)
    res = await OptionsLabTradingService.place_strategy_orders(
        svc,
        legs=[{"side": "buy", "qty": 1, "premium": 100, "symbol": "NFO:NIFTY26AUG24500CE"}],
        lot_size=75,
        confirm=True,
        stop_loss_pct=20,
    )
    assert res["ok"] is True
    assert res["gtts"] == []
    assert any("GTT skipped" in w for w in res["warnings"])


@pytest.mark.asyncio
async def test_list_and_delete_gtt(monkeypatch) -> None:
    from app.domains.options_lab_trading import OptionsLabTradingService

    svc = OptionsLabTradingService.__new__(OptionsLabTradingService)
    deleted: list[int] = []

    async def _find(names, *, team_slugs):
        if "list_gtts" in names:

            async def _list(**kwargs):
                return {
                    "data": [
                        {
                            "id": 42,
                            "status": "active",
                            "type": "two-leg",
                            "condition": json.dumps(
                                {
                                    "tradingsymbol": "NIFTY26AUG24500CE",
                                    "trigger_values": [80, 140],
                                }
                            ),
                            "orders": [{"tradingsymbol": "NIFTY26AUG24500CE"}],
                        }
                    ]
                }

            return _list, "list_gtts", "live-trading"
        if "delete_gtt" in names:

            async def _delete(**kwargs):
                deleted.append(int(kwargs["trigger_id"]))
                return {"status": "success"}

            return _delete, "delete_gtt", "live-trading"
        return None, None, None

    monkeypatch.setattr(svc, "_find_tool", _find)
    listed = await OptionsLabTradingService.list_gtts(svc, mock=False)
    assert listed["ok"] is True
    assert listed["gtts"][0]["trigger_id"] == 42
    assert listed["gtts"][0]["tradingsymbol"] == "NIFTY26AUG24500CE"
    assert "raw" not in listed["gtts"][0]

    mock_listed = await OptionsLabTradingService.list_gtts(svc, mock=True)
    assert mock_listed["ok"] is True
    assert mock_listed["gtts"] == []

    deleted_res = await OptionsLabTradingService.delete_gtt(svc, 42, mock=False)
    assert deleted_res["ok"] is True
    assert deleted == [42]


@pytest.mark.asyncio
async def test_list_gtts_unrecognized_payload_fails_closed(monkeypatch) -> None:
    from app.domains.options_lab_trading import OptionsLabTradingService

    svc = OptionsLabTradingService.__new__(OptionsLabTradingService)

    async def _find(names, *, team_slugs):
        if "list_gtts" in names:

            async def _list(**kwargs):
                return {"status": "success", "message": "weird"}

            return _list, "list_gtts", "live-trading"
        return None, None, None

    monkeypatch.setattr(svc, "_find_tool", _find)
    listed = await OptionsLabTradingService.list_gtts(svc, mock=False)
    assert listed["ok"] is False
    assert listed["gtts"] == []
    assert "unrecognized" in listed["error"]


@pytest.mark.asyncio
async def test_place_orders_buy_first_partial_and_reject(monkeypatch) -> None:
    from app.domains.options_lab_trading import OptionsLabTradingService

    svc = OptionsLabTradingService.__new__(OptionsLabTradingService)
    calls: list[str] = []

    async def _find(names, *, team_slugs):
        if "place_paper_order" in names:
            async def _place(**kwargs):
                calls.append(kwargs["transaction_type"])
                sym = kwargs["tradingsymbol"]
                if "FAIL" in sym:
                    return {"status": "REJECTED", "message": "margin"}
                return {"status": "success", "data": {"order_id": f"id-{len(calls)}"}}

            return _place, "place_paper_order", "paper-trading"
        return None, None, None

    monkeypatch.setattr(svc, "_find_tool", _find)
    legs = [
        {"side": "sell", "qty": 1, "premium": 100, "symbol": "NFO:NIFTY_SELL_PE"},
        {"side": "buy", "qty": 1, "premium": 40, "symbol": "NFO:NIFTY_BUY_PE"},
        {"side": "sell", "qty": 1, "premium": 100, "symbol": "NFO:NIFTY_FAIL_CE"},
        {"side": "buy", "qty": 1, "premium": 40, "symbol": "NFO:NIFTY_BUY_CE"},
    ]
    res = await OptionsLabTradingService.place_strategy_orders(
        svc,
        legs=legs,
        lot_size=75,
        confirm=True,
        live=False,
    )
    assert calls[:2] == ["BUY", "BUY"]
    assert res["partial"] is True
    assert res["ok"] is False
    assert res["submitted_count"] == 3
    assert res["failed_count"] == 1

    # Buy failure must skip subsequent sells (no naked short after missed hedge).
    calls.clear()

    async def _find_buy_fail(names, *, team_slugs):
        if "place_paper_order" in names:

            async def _place(**kwargs):
                calls.append(kwargs["transaction_type"] + ":" + kwargs["tradingsymbol"])
                if kwargs["transaction_type"] == "BUY":
                    return {"status": "REJECTED"}
                return {"status": "success", "data": {"order_id": "should-not"}}

            return _place, "place_paper_order", "paper-trading"
        return None, None, None

    monkeypatch.setattr(svc, "_find_tool", _find_buy_fail)
    skipped = await OptionsLabTradingService.place_strategy_orders(
        svc,
        legs=[
            {"side": "sell", "qty": 1, "premium": 100, "symbol": "NFO:NIFTY_SELL"},
            {"side": "buy", "qty": 1, "premium": 40, "symbol": "NFO:NIFTY_BUY"},
        ],
        lot_size=75,
        confirm=True,
    )
    assert calls == ["BUY:NIFTY_BUY"]
    assert any(r.get("status") == "skipped" for r in skipped["orders"])

    bad_qty = await OptionsLabTradingService.place_strategy_orders(
        svc,
        legs=[{"side": "buy", "qty": 0, "premium": 10, "symbol": "NFO:NIFTY26AUG24500CE"}],
        lot_size=75,
        confirm=True,
    )
    assert bad_qty["ok"] is False
    assert any("qty" in (e or "").lower() for e in (bad_qty.get("errors") or []))

    bad_px = await OptionsLabTradingService.place_strategy_orders(
        svc,
        legs=[{"side": "buy", "qty": 1, "premium": 0, "symbol": "NFO:NIFTY26AUG24500CE"}],
        lot_size=75,
        confirm=True,
        order_type="LIMIT",
    )
    assert bad_px["ok"] is False
    assert any("premium" in (e or "").lower() for e in (bad_px.get("errors") or []))


@pytest.mark.asyncio
async def test_place_orders_requires_symbols_and_live_flag(monkeypatch) -> None:
    from app.domains.options_lab_trading import OptionsLabTradingService

    svc = OptionsLabTradingService.__new__(OptionsLabTradingService)

    missing = await OptionsLabTradingService.place_strategy_orders(
        svc,
        legs=[{"side": "buy", "qty": 1, "premium": 10}],
        lot_size=75,
        confirm=True,
    )
    assert missing["ok"] is False
    assert "symbol" in missing["error"].lower()

    async def _find(names, *, team_slugs):
        if "place_paper_order" in names:
            return None, None, None
        if "place_order" in names:

            async def _place(**kwargs):
                return {"order_id": "1"}

            return _place, "place_order", "live-trading"
        return None, None, None

    monkeypatch.setattr(svc, "_find_tool", _find)
    blocked = await OptionsLabTradingService.place_strategy_orders(
        svc,
        legs=[{"side": "buy", "qty": 1, "premium": 10, "symbol": "NFO:NIFTY26AUG24500CE"}],
        lot_size=75,
        confirm=True,
        live=False,
    )
    assert blocked["ok"] is False
    assert "live=true" in blocked["error"]
