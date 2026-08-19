"""Slow-tier public market data for Trade Desk global checks (Yahoo Finance).

Yahoo aggressively rate-limits scripted access (429 / bad crumb). Rules:
- Never call from the fast signal stream (~8 Hz) — slow tier only (default 1 h).
- One batched ``yf.download`` per refresh, not per-ticker ``.info`` (N+1 requests).
- Reuse a curl_cffi Chrome session (plain requests gets blocked more often).
- Serve stale cache on failure; back off 30 min after rate-limit errors.
- Chunk large ticker lists with a short pause between chunks.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Slow-tier defaults — align with signal_engine_constants TIER_TTL_MS["slow"].
YAHOO_CACHE_TTL_SECONDS = 3_600
YAHOO_RATE_LIMIT_COOLDOWN_SECONDS = 1_800
YAHOO_MIN_FETCH_INTERVAL_SECONDS = 3_600
YAHOO_CHUNK_SIZE = 8
YAHOO_CHUNK_PAUSE_SECONDS = 2.0

# Yahoo tickers for Global Markets Watch checklist rows.
GLOBAL_YAHOO_TICKERS: dict[str, str] = {
    "global_gift_nifty_chg": "^NSEI",
    "global_nikkei_chg": "^N225",
    "global_sti_chg": "^STI",
    "global_hang_seng_chg": "^HSI",
    "global_taiwan_chg": "^TWII",
    "global_kospi_chg": "^KS11",
    "global_set_thailand_chg": "^SET.BK",
    "global_jakarta_chg": "^JKSE",
    "global_shanghai_chg": "000001.SS",
    "global_ftse_chg": "^FTSE",
    "global_cac40_chg": "^FCHI",
    "global_dax_chg": "^GDAXI",
    "global_dow_fut_chg": "YM=F",
    "global_sp500_fut_chg": "ES=F",
    "global_nasdaq_fut_chg": "NQ=F",
    "global_dow_jones_chg": "^DJI",
    "global_sp500_chg": "^GSPC",
    "global_nasdaq_chg": "^IXIC",
    "global_asx200_chg": "^AXJO",
    "global_gold_chg": "GC=F",
    "global_silver_chg": "SI=F",
    "global_bitcoin_chg": "BTC-USD",
    "global_bond_proxy_chg": "^TNX",
}

TIMING_YAHOO_TICKERS: dict[str, str] = {
    "us_futures_chg": "ES=F",
    "eu_futures_chg": "^STOXX50E",
    "gold_chg": "GC=F",
    "silver_chg": "SI=F",
}

STOCK_KITE_SYMBOLS: dict[str, str] = {
    "stock_reliance_chg": "NSE:RELIANCE",
    "stock_hdfc_chg": "NSE:HDFCBANK",
    "stock_infosys_chg": "NSE:INFY",
    "stock_sbi_chg": "NSE:SBIN",
    "stock_icici_chg": "NSE:ICICIBANK",
    "stock_airtel_chg": "NSE:BHARTIARTL",
}

INDEX_KITE_SYMBOLS: dict[str, str] = {
    "index_nifty_chg": "NSE:NIFTY 50",
    "index_sensex_chg": "BSE:SENSEX",
    "index_banknifty_chg": "NSE:NIFTY BANK",
    "index_finnifty_chg": "NSE:NIFTY FIN SERVICE",
}

USD_INR_KITE_SYMBOL = "CDS:USDINR"

ALL_YAHOO_TICKERS: dict[str, str] = {
    **GLOBAL_YAHOO_TICKERS,
    **TIMING_YAHOO_TICKERS,
}

_fetch_lock = threading.Lock()


@dataclass
class _YahooCache:
    values: dict[str, float] = field(default_factory=dict)
    fetched_at: float = 0.0
    cooldown_until: float = 0.0
    last_error: str | None = None


_cache = _YahooCache()


def _is_rate_limit_error(exc: BaseException) -> bool:
    name = type(exc).__name__
    if "RateLimit" in name or "TooManyRequests" in name:
        return True
    msg = str(exc).lower()
    return "too many requests" in msg or "rate limit" in msg


def _yahoo_session() -> Any | None:
    try:
        from curl_cffi import requests as curl_requests

        return curl_requests.Session(impersonate="chrome")
    except ImportError:
        logger.warning("curl_cffi missing — yfinance may hit Yahoo rate limits more often")
        return None


def _change_from_closes(closes: Any) -> float | None:
    try:
        series = closes.dropna()
    except Exception:
        return None
    if series is None or len(series) < 2:
        return None
    try:
        last = float(series.iloc[-1])
        prev = float(series.iloc[-2])
    except (TypeError, ValueError, IndexError):
        return None
    if prev == 0:
        return None
    return round((last - prev) / prev * 100, 3)


def _parse_download_frame(data: Any, symbols: list[str]) -> dict[str, float | None]:
    out: dict[str, float | None] = {sym: None for sym in symbols}
    if data is None:
        return out
    try:
        empty = data.empty  # type: ignore[attr-defined]
    except Exception:
        empty = False
    if empty:
        return out

    columns = getattr(data, "columns", None)
    if columns is not None and hasattr(columns, "levels") and len(getattr(columns, "levels", ())) >= 2:
        for sym in symbols:
            if sym not in data.columns.get_level_values(0):  # type: ignore[union-attr]
                continue
            closes = data[sym]["Close"]  # type: ignore[index]
            out[sym] = _change_from_closes(closes)
        return out

    if "Close" in getattr(data, "columns", ()):
        sym = symbols[0] if len(symbols) == 1 else None
        if sym:
            out[sym] = _change_from_closes(data["Close"])
        return out

    return out


def _download_chunk(symbols: list[str], session: Any | None) -> dict[str, float | None]:
    import yfinance as yf

    if not symbols:
        return {}
    kwargs: dict[str, Any] = {
        "period": "5d",
        "interval": "1d",
        "progress": False,
        "threads": False,
        "group_by": "ticker",
    }
    if session is not None:
        kwargs["session"] = session
    data = yf.download(" ".join(symbols), **kwargs)
    return _parse_download_frame(data, symbols)


def _fetch_raw_changes(tickers: dict[str, str]) -> tuple[dict[str, float], str | None]:
    """Network fetch — may take 20–40 s for ~24 symbols. Call only on slow-tier miss."""
    if not tickers:
        return {}, None

    unique = list(dict.fromkeys(tickers.values()))
    session = _yahoo_session()
    by_symbol: dict[str, float | None] = {}

    for start in range(0, len(unique), YAHOO_CHUNK_SIZE):
        chunk = unique[start : start + YAHOO_CHUNK_SIZE]
        try:
            by_symbol.update(_download_chunk(chunk, session))
        except Exception as exc:
            if _is_rate_limit_error(exc):
                raise
            logger.warning("yahoo_chunk_failed", extra={"chunk": chunk, "error": str(exc)})
        if start + YAHOO_CHUNK_SIZE < len(unique):
            time.sleep(YAHOO_CHUNK_PAUSE_SECONDS)

    out: dict[str, float] = {}
    for feed_key, sym in tickers.items():
        val = by_symbol.get(sym)
        if val is not None:
            out[feed_key] = val
    return out, None


def fetch_yahoo_changes(
    tickers: dict[str, str] | None = None,
    *,
    force: bool = False,
    now: float | None = None,
) -> dict[str, float]:
    """Cached session % changes. Safe for slow-tier polling (~1 h), not fast stream."""
    tickers = tickers or ALL_YAHOO_TICKERS
    ts = now if now is not None else time.monotonic()

    with _fetch_lock:
        if not force and _cache.cooldown_until > ts:
            logger.debug("yahoo_cooldown_active until=%s", _cache.cooldown_until)
            return dict(_cache.values)

        age = ts - _cache.fetched_at
        if (
            not force
            and _cache.values
            and age < YAHOO_CACHE_TTL_SECONDS
            and age < YAHOO_MIN_FETCH_INTERVAL_SECONDS
        ):
            return dict(_cache.values)

        try:
            fresh, _ = _fetch_raw_changes(tickers)
            _cache.values = fresh
            _cache.fetched_at = ts
            _cache.cooldown_until = 0.0
            _cache.last_error = None
            return dict(fresh)
        except Exception as exc:
            _cache.last_error = str(exc)
            if _is_rate_limit_error(exc):
                _cache.cooldown_until = ts + YAHOO_RATE_LIMIT_COOLDOWN_SECONDS
                logger.warning(
                    "yahoo_rate_limited — serving stale cache for %ss",
                    YAHOO_RATE_LIMIT_COOLDOWN_SECONDS,
                )
            else:
                logger.warning("yahoo_fetch_failed: %s", exc)
            return dict(_cache.values)


def yahoo_cache_status() -> dict[str, Any]:
    """Expose cache age / cooldown for admin warnings."""
    ts = time.monotonic()
    age = ts - _cache.fetched_at if _cache.fetched_at else None
    return {
        "cached_keys": len(_cache.values),
        "fetched_age_seconds": round(age, 1) if age is not None else None,
        "cooldown_remaining_seconds": max(0.0, round(_cache.cooldown_until - ts, 1)),
        "last_error": _cache.last_error,
        "ttl_seconds": YAHOO_CACHE_TTL_SECONDS,
    }


def reset_yahoo_cache_for_tests() -> None:
    global _cache
    _cache = _YahooCache()


def mock_yahoo_changes(tickers: dict[str, str]) -> dict[str, float]:
    """Deterministic demo values for mock mode."""
    seeds = [-0.35, 0.12, -0.08, 0.22, -0.15, 0.05, 0.18, -0.42, 0.31, -0.05]
    out: dict[str, float] = {}
    for idx, key in enumerate(tickers):
        out[key] = seeds[idx % len(seeds)]
    return out
