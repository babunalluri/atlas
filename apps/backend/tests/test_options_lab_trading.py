"""Unit tests for Options Lab trading helpers (margins / lot size)."""

from __future__ import annotations

import pytest

from app.domains.options_lab_trading import (
    _available_from_margins_payload,
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
