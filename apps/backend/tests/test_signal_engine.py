"""Signal engine evaluation tests."""

from __future__ import annotations

from app.domains.signal_engine import (
    DEFAULT_METRICS,
    SignalEngineConfig,
    _build_entry_preview,
    _compute_adx,
    _compute_rsi,
    _derive_option_symbol,
    _estimate_pcr,
    _evaluate_rule,
    _find_quote_row,
    _fut_basis_pct,
    _merge_option_iv,
    _mock_feed,
    _normalize_quote_payload,
    _parse_historical_candles,
    _quote_change_pcts,
    _resolve_option_symbols,
    _round_strike,
    evaluate_signal_state,
)


def test_round_strike_nifty() -> None:
    assert _round_strike(24312.5, 50) == 24300
    assert _round_strike(24326.0, 50) == 24350


def test_mock_feed_evaluate_not_ready() -> None:
    config = SignalEngineConfig(mock=True)
    feed = _mock_feed(config)
    result = evaluate_signal_state(config, feed)
    assert result["evaluable"] > 0
    assert result["entry_ready"] is False
    assert result["entry"] is not None
    assert result["entry"]["status"] == "blocked"


def test_dow_jones_abs_lte_passes() -> None:
    assert _evaluate_rule("abs_lte", -0.5, 0.5, feed={}, ce=None, pe=None) is True
    assert _evaluate_rule("abs_lte", -0.6, 0.5, feed={}, ce=None, pe=None) is False


def test_ce_pe_balance() -> None:
    assert _evaluate_rule("ce_pe_balance", None, 0, feed={}, ce=100.0, pe=100.2) is True
    assert _evaluate_rule("ce_pe_balance", None, 0, feed={}, ce=125.0, pe=55.0) is False


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


def test_default_metrics_count() -> None:
    assert len(DEFAULT_METRICS) == 22
    ids = {row["id"] for row in DEFAULT_METRICS}
    assert "dow_jones" in ids
    assert "atm" in ids
    assert "pcr" in ids
    assert "india_vix" in ids
    assert "max_pain" in ids
    assert "spot_chg" in ids
    assert "spot_vs_open" in ids
    assert "fut_basis" in ids
    assert "rsi" in ids
    assert "vix_chg" in ids
    assert "ce_oi" in ids
    assert "pe_oi" in ids
    assert "fii_net" in ids


def test_pcr_between_rule() -> None:
    assert _evaluate_rule("between", 1.25, 1.0, feed={}, ce=None, pe=None, spec={"target_high": 1.3}) is True
    assert _evaluate_rule("between", 1.35, 1.0, feed={}, ce=None, pe=None, spec={"target_high": 1.3}) is False


def test_spot_below_max_pain() -> None:
    feed = {"nifty_ltp": 24312.5, "max_pain": 24400.0}
    assert _evaluate_rule("spot_below_max_pain", None, 0, feed=feed, ce=None, pe=None) is True
    feed2 = {"nifty_ltp": 24500.0, "max_pain": 24400.0}
    assert _evaluate_rule("spot_below_max_pain", None, 0, feed=feed2, ce=None, pe=None) is False


def test_india_vix_lt_18() -> None:
    assert _evaluate_rule("lt", 14.2, 18, feed={}, ce=None, pe=None) is True
    assert _evaluate_rule("lt", 19.0, 18, feed={}, ce=None, pe=None) is False


def test_sensibull_mock_metrics_partial_pass() -> None:
    config = SignalEngineConfig(mock=True)
    feed = _mock_feed(config)
    result = evaluate_signal_state(config, feed)
    by_id = {row["id"]: row["passed"] for row in result["metrics"]}
    assert by_id.get("pcr") is True
    assert by_id.get("india_vix") is True
    assert by_id.get("max_pain") is True
    assert by_id.get("adx") is False
    assert result["entry_ready"] is False


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
    for mid in ("atm", "oi", "ce_oi", "pe_oi"):
        row = next(r for r in result["metrics"] if r["id"] == mid)
        assert row["passed"] is None


def test_fii_net_skipped_when_unset() -> None:
    config = SignalEngineConfig(mock=True)
    feed = _mock_feed(config)
    feed.pop("fii_net", None)
    result = evaluate_signal_state(config, feed)
    fii = next(row for row in result["metrics"] if row["id"] == "fii_net")
    assert fii["passed"] is None
