"""Signal engine evaluation tests."""

from __future__ import annotations

import asyncio
import json
import math
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domains.signal_engine import (
    DEFAULT_METRICS,
    SignalEngineConfig,
    _align_ce_pe_strikes,
    _build_alt_fut_symbol,
    _build_entry_preview,
    _compute_adx,
    _compute_rsi,
    _derive_option_symbol,
    _drop_mismatched_option_symbols,
    _estimate_pcr,
    _evaluate_rule,
    _find_keyed_quote_row,
    _find_quote_row,
    _fut_basis_pct,
    _merge_option_iv,
    _merge_secondary_ce_pe_quotes,
    _merge_yahoo_slow_tier,
    _mock_feed,
    _normalize_quote_payload,
    _option_strike,
    _parse_historical_candles,
    _quote_change_pcts,
    _resolve_option_symbols,
    _round_strike,
    evaluate_signal_state,
)
from app.domains.trade_desk_checklist import CHECKLIST_CATEGORIES, CHECKLIST_ITEM_COUNT


def test_round_strike_nifty() -> None:
    assert _round_strike(24312.5, 50) == 24300
    assert _round_strike(24326.0, 50) == 24350


def test_align_ce_pe_strikes_clears_mismatch() -> None:
    assert _option_strike("NFO:NIFTY26AUG24150CE") == 24150
    assert _option_strike("NFO:NIFTY26AUG24350PE") == 24350
    out = _align_ce_pe_strikes(
        {
            "ce_symbol": "NFO:NIFTY26AUG24150CE",
            "pe_symbol": "NFO:NIFTY26AUG24350PE",
        }
    )
    assert "ce_symbol" not in out
    assert "pe_symbol" not in out
    matched = _align_ce_pe_strikes(
        {
            "ce_symbol": "NFO:NIFTY26AUG24350CE",
            "pe_symbol": "NFO:NIFTY26AUG24350PE",
        }
    )
    assert matched["ce_symbol"].endswith("24350CE")
    assert matched["pe_symbol"].endswith("24350PE")


def test_drop_mismatched_also_aligns_strikes() -> None:
    out = _drop_mismatched_option_symbols(
        {
            "nifty_fut_symbol": "NFO:NIFTY26AUGFUT",
            "ce_symbol": "NFO:NIFTY26AUG24150CE",
            "pe_symbol": "NFO:NIFTY26AUG24350PE",
        }
    )
    assert "ce_symbol" not in out
    assert "pe_symbol" not in out


def test_signal_settings_patch_skips_nested_desk_keys() -> None:
    from app.domains.signal_engine import _signal_settings_patch

    previous = {
        "engine_enabled": False,
        "underlying_symbol": "NSE:NIFTY BANK",
        "options_lab": {"mock": True},
        "param_chart": {"strike": 57000},
    }
    next_settings = {
        "engine_enabled": True,
        "underlying_symbol": "NSE:NIFTY 50",
        "options_lab": {"mock": False},  # must not appear in patch
        "param_chart": {"strike": 24000},
        "ce_symbol": "NFO:NIFTY26AUG24000CE",
    }
    patch = _signal_settings_patch(previous, next_settings)
    assert patch["engine_enabled"] is True
    assert patch["underlying_symbol"] == "NSE:NIFTY 50"
    assert patch["ce_symbol"] == "NFO:NIFTY26AUG24000CE"
    assert "options_lab" not in patch
    assert "param_chart" not in patch


def test_config_fields_changed_ignores_echoed_same_values() -> None:
    """Autosave often re-sends underlying — must not look like a switch."""
    from app.domains.signal_engine import _config_fields_changed

    current = {
        "underlying_symbol": "NSE:NIFTY 50",
        "nifty_fut_symbol": "NFO:NIFTY26AUGFUT",
        "strike_step": 50,
        "pcr": 1.1,
    }
    assert (
        _config_fields_changed(
            current,
            {
                "underlying_symbol": "NSE:NIFTY 50",
                "nifty_fut_symbol": "NFO:NIFTY26AUGFUT",
                "pcr": 1.25,
            },
            "underlying_symbol",
            "fut_symbol",
            "nifty_fut_symbol",
            "strike_step",
        )
        is False
    )
    assert (
        _config_fields_changed(
            current,
            {"underlying_symbol": "BSE:SENSEX"},
            "underlying_symbol",
            "fut_symbol",
            "nifty_fut_symbol",
        )
        is True
    )
    assert _config_fields_changed(current, {"strike_step": 100}, "strike_step") is True


def test_mock_feed_evaluate_not_ready() -> None:
    config = SignalEngineConfig(mock=True)
    feed = _mock_feed(config)
    result = evaluate_signal_state(config, feed)
    assert result["evaluable"] > 0
    assert result["entry_ready"] is False
    assert result["entry"] is not None
    assert result["entry"]["status"] == "blocked"
    assert result["gates_total"] == 52
    assert result["min_coverage"] >= 44  # 85% of 52


def test_entry_ready_requires_gate_coverage() -> None:
    """Sparse all-pass subset must not manufacture BUY (P0 budget-truncation)."""
    from app.domains.signal_engine_constants import ENTRY_GATE_COVERAGE_RATIO

    # Only before_time has data and passes — old logic would be ready on 1/1.
    feed = {"source": "live", "ist_hour": 9.0, "atm": 24200}
    result = evaluate_signal_state(SignalEngineConfig(), feed)
    assert result["passed"] >= 1
    assert result["evaluable"] < result["min_coverage"]
    assert result["entry_ready"] is False
    assert result["entry"]["status"] == "waiting"
    assert "Incomplete checklist" in result["entry"]["status_note"]
    assert result["min_coverage"] == math.ceil(52 * ENTRY_GATE_COVERAGE_RATIO)


def test_tiers_truncated_fail_closed_blocks_buy() -> None:
    """Missing gate data on a truncated tick votes False, not abstain."""
    feed = {
        "source": "live",
        "tiers_truncated": True,
        "ist_hour": 9.0,
        "atm": 24200,
        "nifty_ltp": 24200,
    }
    result = evaluate_signal_state(SignalEngineConfig(), feed)
    assert result["tiers_truncated"] is True
    # Fail-closed pulls missing gates into the denominator as failures.
    assert result["evaluable"] == result["gates_total"] == 52
    assert result["passed"] < result["evaluable"]
    assert result["entry_ready"] is False
    assert result["entry"]["status"] == "blocked"
    assert "fail closed" in result["entry"]["status_note"]
    # Coerced "no data" rows stay blank in the UI (not a red X).
    coerced_blank = [
        r
        for r in result["metrics"]
        if r.get("gates_entry") and r["passed"] is None and r.get("rule") != "info"
    ]
    assert len(coerced_blank) >= 20
    # Rules that actually had data still show a real bool.
    evaluated = [
        r for r in result["metrics"] if r.get("gates_entry") and isinstance(r["passed"], bool)
    ]
    assert evaluated


def test_dow_jones_abs_lte_passes() -> None:
    assert _evaluate_rule("abs_lte", -0.5, 0.5, feed={}, ce=None, pe=None) is True
    assert _evaluate_rule("abs_lte", -0.6, 0.5, feed={}, ce=None, pe=None) is False


def test_ce_pe_balance() -> None:
    assert _evaluate_rule("ce_pe_balance", None, 0, feed={}, ce=100.0, pe=100.2) is True
    assert _evaluate_rule("ce_pe_balance", None, 0, feed={}, ce=125.0, pe=55.0) is False


def test_evaluate_secondary_ce_pe_metrics() -> None:
    feed = _mock_feed(SignalEngineConfig())
    state = evaluate_signal_state(SignalEngineConfig(), feed)
    by_id = {row["id"]: row for row in state["metrics"]}
    assert by_id["chk_005"]["passed"] is True
    assert by_id["chk_018"]["passed"] is True
    assert by_id["chk_083"]["value"] == 1.85


def test_underlying_not_hardcoded() -> None:
    config = SignalEngineConfig()
    assert config.underlying_symbol == ""
    assert config.spot_symbol == ""


def test_from_settings_underlying_alias() -> None:
    config = SignalEngineConfig.from_settings(
        {"nifty_symbol": "NSE:BANKNIFTY", "underlying_label": "BANKNIFTY"}
    )
    assert config.underlying_symbol == "NSE:BANKNIFTY"
    assert config.underlying_label == "BANKNIFTY"


def test_default_mock_is_live() -> None:
    config = SignalEngineConfig()
    assert config.mock is False
    assert config.engine_enabled is False


def test_engine_enabled_from_settings() -> None:
    off = SignalEngineConfig.from_settings({"engine_enabled": False})
    assert off.engine_enabled is False
    on = SignalEngineConfig.from_settings({"engine_enabled": True})
    assert on.engine_enabled is True
    assert off.to_admin_dict()["engine_enabled"] is False


def test_trade_desk_checklist_metrics() -> None:
    assert len(DEFAULT_METRICS) >= CHECKLIST_ITEM_COUNT
    assert CHECKLIST_ITEM_COUNT == 115
    assert len(CHECKLIST_CATEGORIES) == 6
    ids = {row["id"] for row in DEFAULT_METRICS}
    assert "atm" in ids
    assert "chk_008" in ids
    assert "no_trade_after_10" in ids
    by_no = {row["check_no"]: row for row in DEFAULT_METRICS if row.get("check_no")}
    assert by_no[8]["feed_key"] == "pcr"
    assert by_no[19]["rule"] == "before_time"


def test_pcr_between_rule() -> None:
    assert _evaluate_rule("between", 1.25, 1.0, feed={}, ce=None, pe=None, spec={"target_high": 1.3}) is True
    assert _evaluate_rule("between", 1.35, 1.0, feed={}, ce=None, pe=None, spec={"target_high": 1.3}) is False


def test_spot_below_max_pain() -> None:
    feed = {"nifty_ltp": 24312.5, "max_pain": 24400.0}
    assert _evaluate_rule("spot_below_max_pain", None, 0, feed=feed, ce=None, pe=None) is True
    feed2 = {"nifty_ltp": 24500.0, "max_pain": 24400.0}
    assert _evaluate_rule("spot_below_max_pain", None, 0, feed=feed2, ce=None, pe=None) is False


def test_before_time_rule() -> None:
    feed = {"ist_hour": 9.5}
    assert _evaluate_rule("before_time", 9.5, 10, feed=feed, ce=None, pe=None) is True
    feed_late = {"ist_hour": 10.5}
    assert _evaluate_rule("before_time", 10.5, 10, feed=feed_late, ce=None, pe=None) is False


def test_india_vix_lt_18() -> None:
    assert _evaluate_rule("lt", 14.2, 18, feed={}, ce=None, pe=None) is True
    assert _evaluate_rule("lt", 19.0, 18, feed={}, ce=None, pe=None) is False


def test_sensibull_mock_metrics_partial_pass() -> None:
    config = SignalEngineConfig(mock=True)
    feed = _mock_feed(config)
    result = evaluate_signal_state(config, feed)
    by_id = {row["id"]: row["passed"] for row in result["metrics"]}
    assert by_id.get("chk_008") is True
    assert by_id.get("india_vix_level") is True
    assert by_id.get("max_pain_check") is True
    assert by_id.get("chk_003") is False
    assert result["entry_ready"] is False


def test_gates_entry_only_counts_gated_rules() -> None:
    config = SignalEngineConfig(mock=True)
    feed = _mock_feed(config)
    result = evaluate_signal_state(config, feed)
    gated = [r for r in result["metrics"] if r.get("gates_entry")]
    assert result["evaluable"] == len([r for r in gated if r["passed"] is not None])
    assert result["evaluable"] <= len(gated)


def test_find_quote_row_resolves_kite_index_aliases() -> None:
    payload = {
        "NSE:NIFTY BANK": {"last_price": 52100.5, "open_interest": 0},
        "NSE:NIFTY 50": {"last_price": 24200.0},
    }
    bank = _find_quote_row(payload, "NSE:BANKNIFTY")
    nifty = _find_quote_row(payload, "NSE:NIFTY")
    assert bank is not None and bank["last_price"] == 52100.5
    assert nifty is not None and nifty["last_price"] == 24200.0


def test_normalize_groww_ltp_payload() -> None:
    payload = _normalize_quote_payload(
        {
            "ok": True,
            "data": {
                "NSE_NIFTY26AUG24500CE": 125.5,
                "NSE_NIFTY26AUG24500PE": 55.2,
            },
        }
    )
    ce = _find_quote_row(payload, "NFO:NIFTY26AUG24500CE")
    pe = _find_quote_row(payload, "NFO:NIFTY26AUG24500PE")
    assert ce is not None and ce["ltp"] == 125.5
    assert pe is not None and pe["ltp"] == 55.2


def test_normalize_groww_quote_payload() -> None:
    payload = _normalize_quote_payload(
        {
            "ok": True,
            "data": {
                "NIFTY26AUG24500CE": {"last_price": 125.5, "implied_volatility": 11.2},
                "NIFTY26AUG24500PE": {"last_price": 55.2},
            },
        }
    )
    ce = _find_quote_row(payload, "NFO:NIFTY26AUG24500CE")
    pe = _find_quote_row(payload, "NFO:NIFTY26AUG24500PE")
    assert ce is not None and ce["last_price"] == 125.5
    assert pe is not None and pe["last_price"] == 55.2


def test_find_fut_open_interest_from_groww_quote() -> None:
    payload = _normalize_quote_payload(
        {
            "ok": True,
            "data": {
                "last_price": 24500.0,
                "open_interest": 1234567.0,
                "trading_symbol": "NIFTY26AUGFUT",
            },
        }
    )
    fut = _find_quote_row(payload, "NFO:NIFTY26AUGFUT")
    assert fut is not None
    assert fut.get("open_interest") == 1234567.0


def test_find_quote_row_does_not_cross_match_nifty_to_options() -> None:
    quotes = {
        "NIFTY": {"last_price": 24150.0, "trading_symbol": "NIFTY"},
        "NIFTY26AUGFUT": {"last_price": 24166.35, "open_interest": 4_500_000.0},
        "NIFTY26AUG24500CE": {"last_price": 125.5, "implied_volatility": 11.2},
        "NIFTY26AUG24500PE": {"last_price": 55.2},
    }
    ce = _find_quote_row(quotes, "NFO:NIFTY26AUG24500CE")
    pe = _find_quote_row(quotes, "NFO:NIFTY26AUG24500PE")
    fut = _find_quote_row(quotes, "NFO:NIFTY26AUGFUT")
    assert ce is not None and ce["last_price"] == 125.5
    assert pe is not None and pe["last_price"] == 55.2
    assert fut is not None and fut["open_interest"] == 4_500_000.0


def test_find_quote_row_ignores_flat_when_keyed_quotes_exist() -> None:
    quotes = {
        "_flat": {"last_price": 24166.35},
        "NIFTY26AUG24500CE": {"last_price": 125.5},
    }
    ce = _find_quote_row(quotes, "NFO:NIFTY26AUG24500CE")
    assert ce is not None and ce["last_price"] == 125.5


def test_derive_option_symbol_from_fut() -> None:
    assert _derive_option_symbol("NFO:NIFTY26AUGFUT", 24500, "CE") == "NFO:NIFTY26AUG24500CE"
    assert _derive_option_symbol("NFO:NIFTY26AUGFUT", 24500, "PE") == "NFO:NIFTY26AUG24500PE"
    assert _derive_option_symbol("NFO:NIFTY26AUG24500CE", 24500, "CE") is None


def test_derive_option_symbol_rejects_cross_index_strike() -> None:
    """Stale SENSEX ATM must not mint a NIFTY option that never quotes."""
    assert _derive_option_symbol("NFO:NIFTY26AUGFUT", 77500, "CE") is None
    assert _derive_option_symbol("BFO:SENSEX26AUGFUT", 24500, "CE") is None
    assert (
        _derive_option_symbol("BFO:SENSEX26AUGFUT", 77500, "CE")
        == "BFO:SENSEX26AUG77500CE"
    )


def test_resolve_option_symbols_skips_implausible_atm() -> None:
    config = SignalEngineConfig(
        nifty_fut_symbol="NFO:NIFTY26AUGFUT",
        auto_atm_symbols=True,
    )
    ce, pe = _resolve_option_symbols(config, 77500)
    assert ce == "" and pe == ""


def test_build_alt_fut_symbol() -> None:
    assert (
        _build_alt_fut_symbol("NFO:NIFTY26MAR26FUT", "BANKNIFTY", "NFO")
        == "NFO:BANKNIFTY26MAR26FUT"
    )
    assert (
        _build_alt_fut_symbol("NFO:NIFTY26MAR26FUT", "SENSEX", "BFO")
        == "BFO:SENSEX26MAR26FUT"
    )


def test_resolve_option_symbols_prefers_auto_atm() -> None:
    config = SignalEngineConfig(
        nifty_fut_symbol="NFO:NIFTY26AUGFUT",
        ce_symbol="NFO:NIFTY26AUG24000CE",
        pe_symbol="NFO:NIFTY26AUG24000PE",
        auto_atm_symbols=True,
    )
    ce, pe = _resolve_option_symbols(config, 24500)
    assert ce == "NFO:NIFTY26AUG24500CE"
    assert pe == "NFO:NIFTY26AUG24500PE"


def test_sanitize_drops_fut_pasted_as_ce_pe() -> None:
    from app.domains.signal_engine import _sanitize_option_symbol

    assert _sanitize_option_symbol("NFO:NIFTY26AUGFUT") == ""
    assert _sanitize_option_symbol("NFO:NIFTY26AUG24150CE") == "NFO:NIFTY26AUG24150CE"
    # Index names that happen to end in "CE" must not look like options.
    assert _sanitize_option_symbol("NSE:NIFTY FIN SERVICE") == ""
    assert _sanitize_option_symbol("NSE:NIFTY MID SELECT") == ""
    config = SignalEngineConfig.from_settings(
        {
            "nifty_fut_symbol": "NFO:NIFTY26AUGFUT",
            "ce_symbol": "NFO:NIFTY26AUGFUT",
            "pe_symbol": "NFO:NIFTY26AUGFUT",
            "auto_atm_symbols": True,
        }
    )
    assert config.ce_symbol == ""
    assert config.pe_symbol == ""
    ce, pe = _resolve_option_symbols(config, 24150)
    assert ce == "NFO:NIFTY26AUG24150CE"
    assert pe == "NFO:NIFTY26AUG24150PE"


def test_from_settings_drops_nifty_options_on_sensex_fut() -> None:
    """Preset switch leftover: NIFTY CE/PE must not stick when FUT is SENSEX."""
    config = SignalEngineConfig.from_settings(
        {
            "underlying_symbol": "BSE:SENSEX",
            "nifty_fut_symbol": "BFO:SENSEX26AUGFUT",
            "ce_symbol": "NFO:NIFTY26AUG24000CE",
            "pe_symbol": "NFO:NIFTY26AUG24000PE",
            "strike_step": 100,
            "auto_atm_symbols": True,
        }
    )
    assert config.ce_symbol == ""
    assert config.pe_symbol == ""
    ce, pe = _resolve_option_symbols(config, 77700)
    assert ce == "BFO:SENSEX26AUG77700CE"
    assert pe == "BFO:SENSEX26AUG77700PE"


def test_option_matches_fut_rejects_niftynxt50_on_nifty() -> None:
    """Longest-root compare — NIFTYNXT50 must not prefix-match as NIFTY."""
    from app.domains.signal_engine import _option_matches_fut

    assert (
        _option_matches_fut("NFO:NIFTYNXT5026AUG25000CE", "NFO:NIFTY26AUGFUT")
        is False
    )
    assert (
        _option_matches_fut("NFO:NIFTY26AUG24500CE", "NFO:NIFTY26AUGFUT") is True
    )
    assert (
        _option_matches_fut("NFO:NIFTYNXT5026AUG25000CE", "NFO:NIFTYNXT5026AUGFUT")
        is True
    )


def test_from_settings_keeps_unknown_root_pairs() -> None:
    """Hand-typed underlyings (e.g. BANKEX) must not be wiped on load."""
    config = SignalEngineConfig.from_settings(
        {
            "fut_symbol": "BFO:BANKEX26AUGFUT",
            "nifty_fut_symbol": "BFO:BANKEX26AUGFUT",
            "ce_symbol": "BFO:BANKEX26AUG60000CE",
            "pe_symbol": "BFO:BANKEX26AUG60000PE",
        }
    )
    assert config.ce_symbol == "BFO:BANKEX26AUG60000CE"
    assert config.pe_symbol == "BFO:BANKEX26AUG60000PE"

    # Truly unknown root also fails open.
    config2 = SignalEngineConfig.from_settings(
        {
            "fut_symbol": "NFO:FOOBAR26AUGFUT",
            "ce_symbol": "NFO:FOOBAR26AUG100CE",
            "pe_symbol": "NFO:FOOBAR26AUG100PE",
        }
    )
    assert config2.ce_symbol == "NFO:FOOBAR26AUG100CE"
    assert config2.pe_symbol == "NFO:FOOBAR26AUG100PE"


@pytest.mark.asyncio
async def test_maybe_persist_auto_atm_fills_empty_ce_pe(monkeypatch) -> None:
    """Auto-ATM should write derived CE/PE into tool settings when empty."""
    import uuid
    from unittest.mock import AsyncMock, MagicMock

    from app.db.models import Role
    from app.domains.signal_engine import SignalEngineService
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
    service = SignalEngineService(session, context)
    cfg = SignalEngineConfig(
        underlying_symbol="BSE:SENSEX",
        nifty_fut_symbol="BFO:SENSEX26AUGFUT",
        ce_symbol="",
        pe_symbol="",
        auto_atm_symbols=True,
        strike_step=100,
    )
    written: dict = {}
    tool = MagicMock()
    tool.id = uuid.uuid4()
    tool.published_version_id = None

    async def _load_config():
        return cfg

    async def _signal_tool():
        return tool

    async def _settings(_tool):
        return {
            "underlying_symbol": "BSE:SENSEX",
            "nifty_fut_symbol": "BFO:SENSEX26AUGFUT",
            "auto_atm_symbols": True,
        }

    async def _write(_tool, settings):
        written.update(settings)

    monkeypatch.setattr(service, "_load_config", _load_config)
    monkeypatch.setattr(service, "_signal_engine_tool", _signal_tool)
    monkeypatch.setattr(service, "_tool_settings", _settings)
    monkeypatch.setattr(service, "_write_tool_settings", _write)
    service.tools.get = AsyncMock(return_value=tool)
    service.tool_versions.get = AsyncMock(return_value=None)
    service.tool_versions.latest_draft = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "app.domains.signal_engine_cache.get_metric",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "app.domains.signal_engine_cache.set_metric",
        AsyncMock(),
    )

    ok = await service.maybe_persist_auto_atm_symbols(
        {
            "ce_symbol": "BFO:SENSEX26AUG77700CE",
            "pe_symbol": "BFO:SENSEX26AUG77700PE",
            "atm": 77700,
        }
    )
    assert ok is True
    assert written["ce_symbol"] == "BFO:SENSEX26AUG77700CE"
    assert written["pe_symbol"] == "BFO:SENSEX26AUG77700PE"


@pytest.mark.asyncio
async def test_state_live_quote_missing_flag() -> None:
    """Desk badge depends on live_quote_missing — lock True on miss, False on mock."""
    import uuid

    from app.db.models import Role
    from app.domains.signal_engine import SignalEngineService
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
    service = SignalEngineService(session, context)
    service._last_quote_error = None

    live_cfg = SignalEngineConfig(
        mock=False,
        engine_enabled=True,
        underlying_symbol="NSE:NIFTY 50",
    )
    mock_cfg = SignalEngineConfig(mock=True, engine_enabled=True)

    async def load_live():
        return live_cfg, True, True

    async def load_mock():
        return mock_cfg, True, True

    async def feed_no_ltp(_config):
        return {"source": "live"}

    async def feed_with_ltp(_config):
        return {"source": "mock", "nifty_ltp": 24300.0}

    service._load_setup = AsyncMock(side_effect=load_live)
    service._build_feed = AsyncMock(side_effect=feed_no_ltp)
    missing = await service.state()
    assert missing["live_quote_missing"] is True
    assert missing["engine_active"] is True

    service._load_setup = AsyncMock(side_effect=load_mock)
    service._build_feed = AsyncMock(side_effect=feed_with_ltp)
    mocked = await service.state()
    assert mocked["live_quote_missing"] is False
    assert mocked["mock"] is True

    service._load_setup = AsyncMock(side_effect=load_live)
    service._build_feed = AsyncMock(side_effect=feed_with_ltp)
    present = await service.state()
    assert present["live_quote_missing"] is False
    assert present["engine_active"] is True


def test_estimate_pcr_from_atm_oi() -> None:
    pcr = _estimate_pcr(
        {"open_interest": 1000.0},
        {"open_interest": 1250.0},
    )
    assert pcr == 1.25


def test_merge_option_iv_averages_ce_pe() -> None:
    iv = _merge_option_iv({"implied_volatility": 20.0}, {"implied_volatility": 24.0})
    assert iv == 22.0


def test_parse_historical_candles_for_adx() -> None:
    highs, lows, closes = _parse_historical_candles(
        {
            "ok": True,
            "data": {
                "candles": [
                    [1, 100, 105, 99, 103, 10],
                    [2, 103, 108, 102, 106, 12],
                ]
            },
        }
    )
    assert highs == [105.0, 108.0]
    assert lows == [99.0, 102.0]
    assert closes == [103.0, 106.0]


def test_compute_adx_from_candles() -> None:
    highs = [44, 44.5, 43.5, 44.8, 45, 45.2, 44.9, 45.5, 45.1, 45.8, 46, 45.7, 46.2, 46.5, 46.1]
    lows = [43, 43.2, 42.8, 43.5, 43.8, 44, 43.9, 44.5, 44.2, 44.8, 45, 44.6, 45.1, 45.4, 45.0]
    closes = [43.5, 44, 43.2, 44.5, 44.2, 44.8, 44.4, 45.2, 44.9, 45.5, 45.8, 45.3, 46.0, 46.2, 45.6]
    adx = _compute_adx(highs, lows, closes)
    assert adx is not None
    assert adx >= 0


def test_entry_preview_uses_live_ce_pe() -> None:
    config = SignalEngineConfig(entry_ce_premium=100, entry_pe_premium=100)
    entry = _build_entry_preview(
        config,
        {"atm": 24500, "ce": 102.5, "pe": 98.0},
        entry_ready=True,
        passed=10,
        evaluable=10,
    )
    assert "CE=102.5" in entry["label"]
    assert "PE=98" in entry["label"]


def test_spot_chg_between_rule() -> None:
    assert _evaluate_rule(
        "between", 0.25, -0.5, feed={}, ce=None, pe=None, spec={"target_high": 1.5}
    ) is True
    assert _evaluate_rule(
        "between", -0.8, -0.5, feed={}, ce=None, pe=None, spec={"target_high": 1.5}
    ) is False


def test_fut_basis_pct() -> None:
    assert _fut_basis_pct(24300.0, 24329.16) == 0.12
    assert _fut_basis_pct(None, 24329.16) is None


def test_quote_change_pcts() -> None:
    vs_prev, vs_open = _quote_change_pcts(
        {"last_price": 24312.5, "ohlc": {"close": 24250.0, "open": 24280.0}}
    )
    assert vs_prev is not None and round(vs_prev, 2) == 0.26
    assert vs_open is not None and vs_open > 0


def test_quote_change_pcts_from_net_change() -> None:
    vs_prev, _ = _quote_change_pcts({"last_price": 100.0, "net_change": -2.0})
    assert vs_prev == round((-2.0) / 102.0 * 100, 3)
    vs_direct, _ = _quote_change_pcts({"change_percent": 1.25})
    assert vs_direct == 1.25


def test_compute_rsi_mid_band() -> None:
    closes = [44.0 + (i % 3) * 0.5 for i in range(30)]
    rsi = _compute_rsi(closes)
    assert rsi is not None
    assert 35 <= rsi <= 65


def test_info_metrics_never_pass_or_block() -> None:
    assert _evaluate_rule("info", None, 0, feed={}, ce=None, pe=None) is None
    assert _evaluate_rule("info", 24300.0, 0, feed={}, ce=None, pe=None) is None
    config = SignalEngineConfig(mock=True)
    feed = _mock_feed(config)
    result = evaluate_signal_state(config, feed)
    for mid in ("atm", "chk_001"):
        row = next(r for r in result["metrics"] if r["id"] == mid)
        assert row["passed"] is None


def test_fii_net_skipped_when_unset() -> None:
    config = SignalEngineConfig(mock=True)
    feed = _mock_feed(config)
    feed.pop("fii_net", None)
    result = evaluate_signal_state(config, feed)
    fii = next(row for row in result["metrics"] if row["id"] == "fii_net")
    assert fii["passed"] is None


@pytest.mark.asyncio
async def test_merge_secondary_ce_pe_quotes_does_not_corrupt_shared_quotes() -> None:
    """Regression: in-place quotes.update() disabled _flat fallback for FUT OI."""
    fut = "NFO:NIFTY26AUGFUT"
    fast_quotes: dict[str, object] = {
        "_flat": {"last_price": 24310.0, "oi": 12345},
    }
    assert _find_quote_row(fast_quotes, fut) is not None
    assert _find_keyed_quote_row(fast_quotes, "BSE:SENSEX") is None

    config = SignalEngineConfig(
        nifty_fut_symbol=fut,
        metrics=[
            {
                "id": "chk_005",
                "rule": "ce_pe_balance",
                "underlying_symbol": "BSE:SENSEX",
                "option_root": "SENSEX",
                "option_exchange": "BFO",
                "strike_step": 100,
                "ce_feed_key": "sensex_ce",
                "pe_feed_key": "sensex_pe",
            }
        ],
    )

    service = MagicMock()
    fetch_calls: list[list[str]] = []

    async def mock_fetch(symbols: list[str], **_kwargs) -> dict[str, object]:
        fetch_calls.append(list(symbols))
        if "BSE:SENSEX" in symbols:
            return {"BSE:SENSEX": {"last_price": 81000.0}}
        return {
            "BFO:SENSEX26AUG81000CE": {"last_price": 118.0},
            "BFO:SENSEX26AUG81000PE": {"last_price": 119.0},
            "_flat": {"last_price": 118.0},
        }

    service._fetch_quote = AsyncMock(side_effect=mock_fetch)

    feed: dict[str, object] = {}
    await _merge_secondary_ce_pe_quotes(service, config, feed, fast_quotes)

    fut_row = _find_quote_row(fast_quotes, fut)
    assert fut_row is not None
    assert fut_row.get("oi") == 12345
    assert feed["sensex_ce"] == 118.0
    assert feed["sensex_pe"] == 119.0
    assert "_flat" in fast_quotes
    assert "BFO:SENSEX26AUG81000CE" not in fast_quotes
    assert fetch_calls[0] == ["BSE:SENSEX"]


@pytest.mark.asyncio
async def test_straddle_session_open_resets_per_ist_day(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.domains import signal_engine_cache as cache
    from app.domains.signal_engine import _apply_straddle_decay

    cache.reset_signal_cache_for_tests()
    session_store: dict[str, dict[str, object]] = {}

    async def fake_get(tenant_id: str, field: str) -> object | None:
        return session_store.get(tenant_id, {}).get(field)

    async def fake_set(tenant_id: str, field: str, value: object) -> None:
        session_store.setdefault(tenant_id, {})[field] = value

    monkeypatch.setattr(cache, "get_session_value", fake_get)
    monkeypatch.setattr(cache, "set_session_value", fake_set)

    tenant = "tenant-smoke"
    ist_date = {"value": "2026-08-19"}
    monkeypatch.setattr("app.domains.signal_engine._ist_session_date", lambda: ist_date["value"])

    day1 = {"straddle": 200.0}
    await _apply_straddle_decay(tenant, day1)
    assert day1["_straddle_session_open"] == 200.0

    day1_later = {"straddle": 150.0}
    await _apply_straddle_decay(tenant, day1_later)
    assert day1_later["straddle_decay_pct"] == 25.0

    ist_date["value"] = "2026-08-20"
    day2_open = {"straddle": 180.0}
    await _apply_straddle_decay(tenant, day2_open)
    assert day2_open["_straddle_session_open"] == 180.0
    assert "straddle_decay_pct" not in day2_open


@pytest.mark.asyncio
async def test_option_chain_cache_hits_without_writer_grip(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.domains import signal_engine_cache as cache
    from app.domains.signal_engine import (
        SignalEngineConfig,
        _merge_option_chain_tier,
    )
    from app.domains.signal_matrix import row_metric_id

    cache.reset_signal_cache_for_tests()
    tenant = "tenant-chain"
    feed: dict[str, object] = {}
    config = SignalEngineConfig(
        underlying_symbol="NSE:NIFTY 50",
        nifty_fut_symbol="NFO:NIFTY26AUGFUT",
        strike_step=50,
    )
    await cache.set_metric(
        tenant,
        row_metric_id("option_chain", config.underlying_symbol),
        "medium",
        {"pcr": 1.15, "max_pain": 24300.0},
    )

    fetch_calls = {"n": 0}

    class FakeService:
        async def _fetch_quote(self, *args, **kwargs):
            fetch_calls["n"] += 1
            return {}

    await _merge_option_chain_tier(
        FakeService(),
        tenant,
        feed,
        config,
        atm_strike=24300,
        mock=False,
    )

    assert feed["pcr"] == 1.15
    assert feed["max_pain"] == 24300.0
    assert fetch_calls["n"] == 0


def test_publish_entry_dedup_signature_is_entry_only() -> None:
    entry = {"label": "BUY", "passed": 5, "evaluable": 5}
    entry_sig = json.dumps({"entry": entry}, sort_keys=True)
    with_body_sig = json.dumps(
        {"entry": entry, "title": "New trading signal", "body": "4/5 rules passing"},
        sort_keys=True,
    )
    assert entry_sig != with_body_sig
    assert json.dumps({"entry": entry}, sort_keys=True) == entry_sig


@pytest.mark.asyncio
async def test_publish_entry_rejects_when_not_ready(monkeypatch) -> None:
    """entry is always a dict — must gate on entry_ready / status, not truthiness."""
    import uuid

    from app.db.models import Role
    from app.domains.signal_engine import SignalEngineService
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
    service = SignalEngineService(session, context)

    async def fake_state():
        return {
            "entry_ready": False,
            "entry": {
                "side": "BUY",
                "atm": 24200,
                "status": "blocked",
                "label": "BUY= 24200, CE=100, PE=100, EXIT +5%",
                "status_note": "No buy — 2 rules failing (20/22 pass).",
            },
            "passed": 20,
            "evaluable": 22,
        }

    monkeypatch.setattr(service, "state", fake_state)
    out = await service.publish_entry()
    assert out["ok"] is False
    assert out["error"] == "Entry conditions not met"


@pytest.mark.asyncio
async def test_publish_entry_allows_ready(monkeypatch) -> None:
    import uuid

    from app.db.models import Role
    from app.domains.signal_engine import SignalEngineService
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
    service = SignalEngineService(session, context)
    entry = {
        "side": "BUY",
        "atm": 24200,
        "status": "ready",
        "label": "BUY= 24200, CE=100, PE=100, EXIT +5%",
        "status_note": "All entry rules pass",
    }

    async def fake_state():
        return {
            "entry_ready": True,
            "entry": entry,
            "passed": 22,
            "evaluable": 22,
        }

    monkeypatch.setattr(service, "state", fake_state)
    monkeypatch.setattr(
        "app.domains.signal_engine_cache.get_session_value",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "app.domains.signal_engine_cache.set_session_value",
        AsyncMock(),
    )

    class _Memberships:
        async def list_users(self):
            return []

    class _Notifications:
        async def create_batch(self, **_kwargs):
            return uuid.uuid4(), []

    monkeypatch.setattr(
        "app.db.repositories.MembershipRepository",
        lambda *_a, **_k: _Memberships(),
    )
    monkeypatch.setattr(
        "app.db.repositories.UserNotificationRepository",
        lambda *_a, **_k: _Notifications(),
    )
    # Service constructs these from self.session — bind on instance after init
    service  # noqa: B018 — repositories are imported inside publish_entry

    # Patch the imports used inside publish_entry
    import app.domains.signal_engine as se_mod

    monkeypatch.setattr(
        se_mod,
        "UserNotificationRepository",
        lambda *_a, **_k: _Notifications(),
        raising=False,
    )

    # publish_entry imports MembershipRepository and UserNotificationRepository
    # from app.db.repositories inside the method.
    monkeypatch.setattr(
        "app.db.repositories.MembershipRepository",
        lambda *a, **k: _Memberships(),
    )
    monkeypatch.setattr(
        "app.db.repositories.UserNotificationRepository",
        lambda *a, **k: _Notifications(),
    )

    out = await service.publish_entry()
    assert out["ok"] is True
    assert out["entry"]["status"] == "ready"


@pytest.mark.asyncio
async def test_merge_yahoo_uses_merged_payload_for_crypto_max(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_cache_get(_tenant: str, _metric: str):
        return None

    async def fake_cache_set(_tenant: str, _metric: str, _tier: str, _value):
        return None

    def fake_fetch(tickers: dict[str, str]) -> dict[str, float]:
        if "global_bitcoin_chg" in tickers:
            return {"global_bitcoin_chg": -9.5}
        if "global_eth_chg" in tickers:
            return {"global_eth_chg": 1.0}
        return {}

    monkeypatch.setattr("app.domains.signal_engine._cache_get", fake_cache_get)
    monkeypatch.setattr("app.domains.signal_engine._cache_set", fake_cache_set)
    monkeypatch.setattr("app.domains.signal_engine.fetch_yahoo_changes", fake_fetch)

    feed: dict[str, float] = {}
    await _merge_yahoo_slow_tier("tenant-x", feed, mock=False)
    assert feed["global_bitcoin_chg"] == -9.5
    assert feed["global_crypto_max_abs_chg"] == 9.5


@pytest.mark.asyncio
async def test_merge_yahoo_fetches_all_plus_crypto_only(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[frozenset[str]] = []

    async def fake_cache_get(_tenant: str, _metric: str):
        return None

    async def fake_cache_set(_tenant: str, _metric: str, _tier: str, _value):
        return None

    def fake_fetch(tickers: dict[str, str]) -> dict[str, float]:
        seen.append(frozenset(tickers.keys()))
        return {}

    monkeypatch.setattr("app.domains.signal_engine._cache_get", fake_cache_get)
    monkeypatch.setattr("app.domains.signal_engine._cache_set", fake_cache_set)
    monkeypatch.setattr("app.domains.signal_engine.fetch_yahoo_changes", fake_fetch)

    await _merge_yahoo_slow_tier("tenant-x", {}, mock=False)
    assert len(seen) == 2


def test_signal_active_tick_ms_is_book_first_cadence() -> None:
    from app.domains.signal_engine_constants import (
        SIGNAL_ACTIVE_TICK_MS,
        TIER_A_REST_GAP_FILL_MS,
    )

    assert SIGNAL_ACTIVE_TICK_MS == 200
    assert TIER_A_REST_GAP_FILL_MS == 5_000


@pytest.mark.asyncio
async def test_tier_a_quotes_does_not_fetch_when_book_has_ltp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.db.models import Role
    from app.domains.signal_engine import SignalEngineService
    from app.tenancy.context import TenantContext
    import uuid

    tenant = uuid.uuid4()
    symbols = ["NSE:NIFTY 50", "NFO:NIFTYFUT"]

    async def fake_book(_tenant, syms, *, require_all=True, require_alive=True):
        return {
            "NSE:NIFTY 50": {"last_price": 24500.0, "instrument_token": 1},
            "NFO:NIFTYFUT": {"last_price": 24510.0, "oi": 100, "instrument_token": 2},
        }

    async def fake_get_metric(_tenant, key):
        if key == "quote:ticker:alive":
            return {"ts": 1}
        return None

    fetch = AsyncMock(return_value={})
    monkeypatch.setattr(
        "app.domains.kite_ticker_hub.assemble_quotes_from_book",
        fake_book,
    )
    monkeypatch.setattr(
        "app.domains.signal_engine_cache.get_metric",
        fake_get_metric,
    )
    session = MagicMock()
    session.info = {"tenant_id": tenant}
    ctx = TenantContext(
        tenant_id=tenant,
        user_id="test",
        role=Role.tenant_admin,
        auth_org_id="org",
        principal_type="user",
    )
    service = SignalEngineService(session, ctx)
    service._fetch_quote = fetch
    out = await service._tier_a_quotes(symbols)
    assert out["NSE:NIFTY 50"]["last_price"] == 24500.0
    fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_tier_a_rest_gap_fill_rate_limited_when_ticker_dead(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dead ticker must not sandbox-fan-out on every 200ms worker tick."""
    from app.db.models import Role
    from app.domains.signal_engine import SignalEngineService
    from app.tenancy.context import TenantContext
    import uuid

    tenant = uuid.uuid4()
    symbols = ["NSE:NIFTY 50"]
    metrics: dict[str, object] = {}

    async def fake_book(_tenant, syms, *, require_all=True, require_alive=True):
        return {}

    async def fake_get_metric(_tenant, key):
        return metrics.get(key)

    async def fake_set_metric(_tenant, key, _tier, value, *, ttl_ms=None):
        metrics[key] = value

    fetch = AsyncMock(
        return_value={"NSE:NIFTY 50": {"last_price": 100.0}}
    )
    monkeypatch.setattr(
        "app.domains.kite_ticker_hub.assemble_quotes_from_book",
        fake_book,
    )
    monkeypatch.setattr(
        "app.domains.signal_engine_cache.get_metric",
        fake_get_metric,
    )
    monkeypatch.setattr(
        "app.domains.signal_engine_cache.set_metric",
        fake_set_metric,
    )
    session = MagicMock()
    session.info = {"tenant_id": tenant}
    ctx = TenantContext(
        tenant_id=tenant,
        user_id="test",
        role=Role.tenant_admin,
        auth_org_id="org",
        principal_type="user",
    )
    service = SignalEngineService(session, ctx)
    service._fetch_quote = fetch

    first = await service._tier_a_quotes(symbols)
    assert first["NSE:NIFTY 50"]["last_price"] == 100.0
    assert fetch.await_count == 1
    assert "tier_a_rest_gap" in metrics

    # Second tick while gate is set — no sandbox call.
    second = await service._tier_a_quotes(symbols)
    assert fetch.await_count == 1
    assert "NSE:NIFTY 50" not in second  # empty book, gated, no soft rows

    # Newly derived ATM CE/PE must still REST-fill (not blocked by under gate).
    fetch.return_value = {"NFO:NIFTY26AUG24350CE": {"last_price": 12.5}}
    third = await service._tier_a_quotes(["NFO:NIFTY26AUG24350CE"])
    assert fetch.await_count == 2
    assert third["NFO:NIFTY26AUG24350CE"]["last_price"] == 12.5


@pytest.mark.asyncio
async def test_tier_a_uses_soft_book_before_rest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.db.models import Role
    from app.domains.signal_engine import SignalEngineService
    from app.tenancy.context import TenantContext
    import uuid

    tenant = uuid.uuid4()
    symbols = ["NSE:NIFTY 50"]

    async def fake_book(_tenant, syms, *, require_all=True, require_alive=True):
        if require_alive:
            return {}
        return {"NSE:NIFTY 50": {"last_price": 24444.0}}

    async def fake_get_metric(_tenant, key):
        return None  # ticker dead, no gap gate yet

    fetch = AsyncMock(return_value={})
    monkeypatch.setattr(
        "app.domains.kite_ticker_hub.assemble_quotes_from_book",
        fake_book,
    )
    monkeypatch.setattr(
        "app.domains.signal_engine_cache.get_metric",
        fake_get_metric,
    )
    session = MagicMock()
    session.info = {"tenant_id": tenant}
    ctx = TenantContext(
        tenant_id=tenant,
        user_id="test",
        role=Role.tenant_admin,
        auth_org_id="org",
        principal_type="user",
    )
    service = SignalEngineService(session, ctx)
    service._fetch_quote = fetch
    out = await service._tier_a_quotes(symbols)
    assert out["NSE:NIFTY 50"]["last_price"] == 24444.0
    fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_build_feed_reads_tier_b_cache_without_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.db.models import Role
    from app.domains.signal_engine import SignalEngineConfig, SignalEngineService
    from app.tenancy.context import TenantContext
    import uuid

    tenant_id = uuid.uuid4()
    caches = {
        "dow_jones": -0.4,
        "crude_oil": {"crude_ltp": 71.2, "crude_prev_close": 70.0},
        "india_vix": 13.5,
        "aux_quotes": {"usd_inr": 83.1},
    }

    async def fake_cache_get(_tenant: str, metric: str):
        return caches.get(metric)

    async def fake_cache_set(*_a, **_k):
        return None

    async def fake_nse(*_a, **_k):
        return None

    async def fake_yahoo(*_a, **_k):
        return None

    async def fake_chain(*_a, **_k):
        return None

    async def fake_levels(*_a, **_k):
        return None

    async def fake_straddle(*_a, **_k):
        return None

    async def fake_secondary(*_a, **_k):
        return None

    async def fake_tier_a(self, symbols):
        return {
            "NSE:NIFTY 50": {
                "last_price": 24500.0,
                "ohlc": {"open": 24400.0},
            }
        }

    fetch = AsyncMock(return_value={})
    monkeypatch.setattr("app.domains.signal_engine._cache_get", fake_cache_get)
    monkeypatch.setattr("app.domains.signal_engine._cache_set", fake_cache_set)
    monkeypatch.setattr("app.domains.signal_engine._merge_nse_slow_tier", fake_nse)
    monkeypatch.setattr("app.domains.signal_engine._merge_yahoo_slow_tier", fake_yahoo)
    monkeypatch.setattr("app.domains.signal_engine._merge_yahoo_timing_tier", fake_yahoo)
    monkeypatch.setattr("app.domains.signal_engine._merge_option_chain_tier", fake_chain)
    monkeypatch.setattr("app.domains.signal_engine._merge_levels_tier", fake_levels)
    monkeypatch.setattr("app.domains.signal_engine._apply_straddle_decay", fake_straddle)
    monkeypatch.setattr(
        "app.domains.signal_engine._merge_secondary_ce_pe_quotes", fake_secondary
    )
    monkeypatch.setattr(SignalEngineService, "_tier_a_quotes", fake_tier_a)

    session = MagicMock()
    session.info = {"tenant_id": tenant_id}
    ctx = TenantContext(
        tenant_id=tenant_id,
        user_id="test",
        role=Role.tenant_admin,
        auth_org_id="org",
        principal_type="user",
    )
    service = SignalEngineService(session, ctx)
    service._fetch_quote = fetch
    config = SignalEngineConfig(
        mock=False,
        underlying_symbol="NSE:NIFTY 50",
        crude_symbol="MCX:CRUDEOILM",
        india_vix_symbol="NSE:INDIA VIX",
    )
    feed = await service._build_feed(config)
    assert feed["nifty_ltp"] == 24500.0
    assert feed["crude_ltp"] == 71.2
    assert feed["india_vix"] == 13.5
    assert feed["usd_inr"] == 83.1
    assert feed["dow_change_pct"] == -0.4
    # No REST for Tier B on the critical path.
    fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_compute_state_payload_keeps_last_good_on_timeout(monkeypatch) -> None:
    """Timeout recovery must return the prior live board, not an empty starting frame."""
    import asyncio
    import uuid

    from app.db.models import Role
    from app.domains.signal_engine import SignalEngineService, _compute_state_payload
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
    service = SignalEngineService(session, context)

    async def _hang() -> dict:
        await asyncio.sleep(3600)
        return {}

    monkeypatch.setattr(service, "state", _hang)
    # Force a tiny timeout so the test stays fast.
    monkeypatch.setattr(
        "app.domains.signal_engine.STATE_COMPUTE_TIMEOUT_MS",
        50,
    )

    last_good = {
        "feed_source": "live",
        "passed": 12,
        "evaluable": 20,
        "metrics": [{"id": "atm", "value": 24300}],
        "live_warnings": [],
        "computed_at_ms": 1_700_000_000_000,
    }
    cfg = SignalEngineConfig(engine_enabled=True, underlying_symbol="NSE:NIFTY 50")
    out = await _compute_state_payload(service, config=cfg, last_good=last_good)
    assert out["feed_source"] == "live"
    assert out["passed"] == 12
    assert out["engine_computing"] is True
    assert out["computed_at_ms"] == 1_700_000_000_000
    assert any("timed out under load" in str(w) for w in (out.get("live_warnings") or []))


def test_should_preserve_computed_at_ms_for_keep_last_good() -> None:
    from app.domains.signal_engine_worker import should_preserve_computed_at_ms

    assert should_preserve_computed_at_ms(
        {
            "engine_computing": True,
            "feed_source": "live",
            "computed_at_ms": 1_700_000_000_000,
        }
    )
    assert not should_preserve_computed_at_ms(
        {
            "engine_computing": False,
            "feed_source": "live",
            "computed_at_ms": 1_700_000_000_000,
        }
    )
    assert not should_preserve_computed_at_ms(
        {
            "engine_computing": True,
            "feed_source": "starting",
            "computed_at_ms": 1_700_000_000_000,
        }
    )


@pytest.mark.asyncio
async def test_merge_nse_slow_tier_updates_full_payload(monkeypatch) -> None:
    """Live NSE path must merge calendar/DII fields, not only FII + A/D."""
    from app.domains.signal_engine import SignalEngineConfig, _merge_nse_slow_tier

    payload = {
        "fii_net": 100.0,
        "dii_net": -50.0,
        "advance_decline_ratio": 1.2,
        "market_holiday_any": 0.0,
        "macro_events_next_7d": 2.0,
        "macro_event_risk_score": 1.0,
        "nse_corp_events_today": 1.0,
        "fed_meeting_proximity_days": 3.0,
        "fed_meeting_today": 0.0,
        "nse_holiday_today": 0.0,
        "us_holiday_today": 0.0,
        "uk_holiday_today": 0.0,
    }
    cached_hits: list[dict] = []

    async def fake_cache_get(_tenant: str, metric: str):
        if metric == "nse_slow":
            return None
        return None

    async def fake_cache_set(_tenant: str, metric: str, _tier: str, value):
        if metric == "nse_slow":
            cached_hits.append(dict(value))

    monkeypatch.setattr("app.domains.signal_engine._cache_get", fake_cache_get)
    monkeypatch.setattr("app.domains.signal_engine._cache_set", fake_cache_set)
    monkeypatch.setattr(
        "app.domains.signal_engine.fetch_nse_slow_fields",
        lambda: payload,
    )

    feed: dict = {}
    await _merge_nse_slow_tier(
        "tenant",
        feed,
        SignalEngineConfig(engine_enabled=True),
        mock=False,
    )
    for key, val in payload.items():
        assert feed.get(key) == val
    assert cached_hits and cached_hits[0]["dii_net"] == -50.0

    # Cached branch also merges fully.
    async def fake_cache_hit(_tenant: str, metric: str):
        return payload if metric == "nse_slow" else None

    monkeypatch.setattr("app.domains.signal_engine._cache_get", fake_cache_hit)
    feed2: dict = {}
    await _merge_nse_slow_tier(
        "tenant",
        feed2,
        SignalEngineConfig(engine_enabled=True),
        mock=False,
    )
    assert feed2.get("macro_events_next_7d") == 2.0
    assert feed2.get("nse_corp_events_today") == 1.0


@pytest.mark.asyncio
async def test_refresh_tier_b_fetches_run_concurrently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Crude / VIX / aux must not stack sandbox waits serially."""
    import time
    import uuid

    from app.db.models import Role
    from app.domains.signal_engine import SignalEngineConfig, SignalEngineService
    from app.tenancy.context import TenantContext

    tenant_id = uuid.uuid4()
    in_flight = 0
    max_in_flight = 0

    async def fake_cache_get(_tenant: str, _metric: str):
        return None

    async def fake_cache_set(*_a, **_k):
        return None

    async def fake_fetch(symbols, prefer="get_quote", timeout_s=None, **_kwargs):  # noqa: ARG001
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        await asyncio.sleep(0.05)
        in_flight -= 1
        # Minimal rows so each branch caches something.
        out = {}
        for sym in symbols:
            out[sym] = {"last_price": 100.0, "close": 99.0, "ohlc": {"open": 98.0}}
        return out

    async def fake_heavy(*_a, **_k):
        return None

    monkeypatch.setattr("app.domains.signal_engine._cache_get", fake_cache_get)
    monkeypatch.setattr("app.domains.signal_engine._cache_set", fake_cache_set)
    # Chain/levels/trend/Yahoo run in the same gather — keep them no-ops so this
    # test still asserts crude/VIX/aux concurrency without wall-clock hist/Yahoo.
    monkeypatch.setattr("app.domains.signal_engine._merge_option_chain_tier", fake_heavy)
    monkeypatch.setattr("app.domains.signal_engine._merge_levels_tier", fake_heavy)
    monkeypatch.setattr("app.domains.signal_engine._merge_yahoo_slow_tier", fake_heavy)
    monkeypatch.setattr("app.domains.signal_engine._merge_yahoo_timing_tier", fake_heavy)

    session = MagicMock()
    session.info = {"tenant_id": tenant_id}
    ctx = TenantContext(
        tenant_id=tenant_id,
        auth_org_id="org",
        user_id="u1",
        role=Role.tenant_admin,
    )
    service = SignalEngineService(session, ctx)
    service._fetch_quote = fake_fetch  # type: ignore[method-assign]

    async def fake_tier_a(self, symbols):  # noqa: ANN001,ARG001
        return {}

    monkeypatch.setattr(SignalEngineService, "_tier_a_quotes", fake_tier_a)

    cfg = SignalEngineConfig(
        mock=False,
        crude_symbol="MCX:CRUDEOILM",
        india_vix_symbol="NSE:INDIA VIX",
    )
    t0 = time.monotonic()
    await service.refresh_tier_b_context(cfg)
    elapsed = time.monotonic() - t0
    assert max_in_flight >= 2
    assert elapsed < 0.12


@pytest.mark.asyncio
async def test_build_feed_does_not_inline_chain_or_yahoo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cause 2: Tier-A tick must not await chain/levels/Yahoo refreshes."""
    import uuid

    from app.db.models import Role
    from app.domains.signal_engine import SignalEngineConfig, SignalEngineService
    from app.tenancy.context import TenantContext

    calls = {"chain": 0, "levels": 0, "yahoo": 0}

    async def fake_cache_get(_tenant: str, metric: str):
        # Instrument-scoped Tier-B keys (F1).
        if metric in {"option_chain", "option_chain:NSE:NIFTY_50"}:
            return {"pcr": 1.1, "max_pain": 24500.0}
        if metric in {"levels", "levels:NSE:NIFTY_50"}:
            return {"pdh": 24600.0}
        if metric in {"trend", "trend:NSE:NIFTY_50"}:
            return {"adx": 22.0, "rsi": 55.0}
        if metric == "yahoo_global":
            return {"global_nikkei_chg": 0.4}
        return None

    async def fake_cache_set(*_a, **_k):
        return None

    async def boom_chain(*_a, **_k):
        calls["chain"] += 1
        raise AssertionError("chain must not run on Tier-A tick")

    async def boom_levels(*_a, **_k):
        calls["levels"] += 1
        raise AssertionError("levels must not run on Tier-A tick")

    async def boom_yahoo(*_a, **_k):
        calls["yahoo"] += 1
        raise AssertionError("yahoo must not run on Tier-A tick")

    async def fake_nse(*_a, **_k):
        return None

    async def fake_straddle(*_a, **_k):
        return None

    async def fake_secondary(*_a, **_k):
        return None

    async def fake_tier_a(self, symbols):  # noqa: ANN001,ARG001
        return {
            "NSE:NIFTY 50": {
                "last_price": 24500.0,
                "ohlc": {"open": 24400.0},
                "instrument_token": 256265,
            }
        }

    monkeypatch.setattr("app.domains.signal_engine._cache_get", fake_cache_get)
    monkeypatch.setattr("app.domains.signal_engine._cache_set", fake_cache_set)
    monkeypatch.setattr("app.domains.signal_engine._merge_nse_slow_tier", fake_nse)
    monkeypatch.setattr("app.domains.signal_engine._merge_option_chain_tier", boom_chain)
    monkeypatch.setattr("app.domains.signal_engine._merge_levels_tier", boom_levels)
    monkeypatch.setattr("app.domains.signal_engine._merge_yahoo_slow_tier", boom_yahoo)
    monkeypatch.setattr("app.domains.signal_engine._merge_yahoo_timing_tier", boom_yahoo)
    monkeypatch.setattr("app.domains.signal_engine._apply_straddle_decay", fake_straddle)
    monkeypatch.setattr(
        "app.domains.signal_engine._merge_secondary_ce_pe_quotes", fake_secondary
    )
    monkeypatch.setattr(SignalEngineService, "_tier_a_quotes", fake_tier_a)

    session = MagicMock()
    session.info = {"tenant_id": uuid.uuid4()}
    ctx = TenantContext(
        tenant_id=session.info["tenant_id"],
        user_id="test",
        role=Role.tenant_admin,
        auth_org_id="org",
        principal_type="user",
    )
    service = SignalEngineService(session, ctx)
    service._fetch_quote = AsyncMock(return_value={})
    config = SignalEngineConfig(
        mock=False,
        underlying_symbol="NSE:NIFTY 50",
        nifty_fut_symbol="NFO:NIFTY26SEPFUT",
    )
    feed = await service._build_feed(config)
    assert feed["nifty_ltp"] == 24500.0
    assert feed["pcr"] == 1.1
    assert feed["adx"] == 22.0
    assert feed["global_nikkei_chg"] == 0.4
    assert calls == {"chain": 0, "levels": 0, "yahoo": 0}
    # FUT OI / secondary CE-PE may still REST; chain/levels/Yahoo must not.


def test_phase3_metric_ttls_are_shorter_than_generic_medium() -> None:
    from app.domains.signal_engine_constants import (
        OPTION_CHAIN_TTL_MS,
        TIER_B_REFRESH_GATE_MS,
        TIER_TTL_MS,
        TREND_TTL_MS,
    )

    assert OPTION_CHAIN_TTL_MS == 15_000
    assert TREND_TTL_MS == 30_000
    assert TIER_B_REFRESH_GATE_MS < OPTION_CHAIN_TTL_MS
    assert OPTION_CHAIN_TTL_MS < TIER_TTL_MS["medium"]
    assert TREND_TTL_MS < TIER_TTL_MS["medium"]
