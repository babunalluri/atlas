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
# Timing (gold/silver/futures): session % vs prior close — 10 min (within 5–15; avoids Yahoo 429s).
YAHOO_TIMING_CACHE_TTL_SECONDS = 600
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

# Major alts for checklist #83 (max abs % move across basket).
CRYPTO_YAHOO_TICKERS: dict[str, str] = {
    "global_eth_chg": "ETH-USD",
    "global_sol_chg": "SOL-USD",
    "global_xrp_chg": "XRP-USD",
    "global_bnb_chg": "BNB-USD",
    "global_ada_chg": "ADA-USD",
    "global_doge_chg": "DOGE-USD",
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


def crypto_max_abs_change(changes: dict[str, float]) -> float | None:
    """Largest absolute session % move across the crypto basket."""
    keys = set(CRYPTO_YAHOO_TICKERS) | {"global_bitcoin_chg"}
    vals = [abs(v) for key, v in changes.items() if key in keys and v is not None]
    if not vals:
        return None
    return round(max(vals), 3)

_fetch_lock = threading.Lock()


@dataclass
class _YahooCache:
    values: dict[str, float] = field(default_factory=dict)
    fetched_at: float = 0.0
    cooldown_until: float = 0.0
    last_error: str | None = None


_caches: dict[str, _YahooCache] = {}


def _ticker_cache_key(tickers: dict[str, str]) -> str:
    return "|".join(f"{key}:{tickers[key]}" for key in sorted(tickers))


def _cache_for(tickers: dict[str, str]) -> _YahooCache:
    return _caches.setdefault(_ticker_cache_key(tickers), _YahooCache())


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


def _pct_change(last: float, prev: float) -> float | None:
    if prev == 0:
        return None
    return round((last - prev) / prev * 100, 3)


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
    return _pct_change(last, prev)


def _last_close(closes: Any) -> float | None:
    try:
        series = closes.dropna()
    except Exception:
        return None
    if series is None or len(series) < 1:
        return None
    try:
        return float(series.iloc[-1])
    except (TypeError, ValueError, IndexError):
        return None


def _prev_session_close(closes: Any) -> float | None:
    """Prior completed daily close (skip incomplete last bar when 2+ days present)."""
    try:
        series = closes.dropna()
    except Exception:
        return None
    if series is None or len(series) < 1:
        return None
    try:
        if len(series) >= 2:
            return float(series.iloc[-2])
        return float(series.iloc[-1])
    except (TypeError, ValueError, IndexError):
        return None


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


def _closes_by_symbol(data: Any, symbols: list[str]) -> dict[str, Any]:
    """Extract per-symbol Close series from a yfinance download frame."""
    out: dict[str, Any] = {}
    if data is None:
        return out
    try:
        if data.empty:  # type: ignore[attr-defined]
            return out
    except Exception:
        pass
    columns = getattr(data, "columns", None)
    if columns is not None and hasattr(columns, "levels") and len(getattr(columns, "levels", ())) >= 2:
        for sym in symbols:
            if sym not in data.columns.get_level_values(0):  # type: ignore[union-attr]
                continue
            out[sym] = data[sym]["Close"]  # type: ignore[index]
        return out
    if "Close" in getattr(data, "columns", ()) and len(symbols) == 1:
        out[symbols[0]] = data["Close"]
    return out


def _fetch_raw_session_changes(tickers: dict[str, str]) -> tuple[dict[str, float], str | None]:
    """Session % = last trade vs prior daily close (moves while the contract is trading)."""
    import yfinance as yf

    if not tickers:
        return {}, None

    unique = list(dict.fromkeys(tickers.values()))
    session = _yahoo_session()
    kwargs: dict[str, Any] = {
        "progress": False,
        "threads": False,
        "group_by": "ticker",
    }
    if session is not None:
        kwargs["session"] = session

    daily = yf.download(" ".join(unique), period="5d", interval="1d", **kwargs)
    # Short intraday window for last print; falls back to daily last if empty.
    try:
        intra = yf.download(" ".join(unique), period="1d", interval="5m", **kwargs)
    except Exception as exc:
        if _is_rate_limit_error(exc):
            raise
        logger.warning("yahoo_timing_intraday_failed: %s", exc)
        intra = None

    daily_closes = _closes_by_symbol(daily, unique)
    intra_closes = _closes_by_symbol(intra, unique) if intra is not None else {}

    by_symbol: dict[str, float | None] = {}
    for sym in unique:
        prev = _prev_session_close(daily_closes.get(sym))
        last = _last_close(intra_closes.get(sym))
        if last is None:
            last = _last_close(daily_closes.get(sym))
        if last is None or prev is None:
            by_symbol[sym] = None
            continue
        by_symbol[sym] = _pct_change(last, prev)

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
    bucket_key = _ticker_cache_key(tickers)

    with _fetch_lock:
        cache = _cache_for(tickers)
        if not force and cache.cooldown_until > ts:
            logger.debug("yahoo_cooldown_active bucket=%s until=%s", bucket_key, cache.cooldown_until)
            return dict(cache.values)

        age = ts - cache.fetched_at
        if (
            not force
            and cache.values
            and age < YAHOO_CACHE_TTL_SECONDS
            and age < YAHOO_MIN_FETCH_INTERVAL_SECONDS
        ):
            return dict(cache.values)

        try:
            fresh, _ = _fetch_raw_changes(tickers)
            cache.values = fresh
            cache.fetched_at = ts
            cache.cooldown_until = 0.0
            cache.last_error = None
            return dict(fresh)
        except Exception as exc:
            cache.last_error = str(exc)
            if _is_rate_limit_error(exc):
                cache.cooldown_until = ts + YAHOO_RATE_LIMIT_COOLDOWN_SECONDS
                logger.warning(
                    "yahoo_rate_limited bucket=%s — serving stale cache for %ss",
                    bucket_key,
                    YAHOO_RATE_LIMIT_COOLDOWN_SECONDS,
                )
            else:
                logger.warning("yahoo_fetch_failed bucket=%s: %s", bucket_key, exc)
            return dict(cache.values)


def fetch_yahoo_session_changes(
    tickers: dict[str, str] | None = None,
    *,
    force: bool = False,
    now: float | None = None,
) -> dict[str, float]:
    """Intraday session % (last vs prior close). Medium-tier (~10 min) for gold/silver/futures."""
    tickers = tickers or TIMING_YAHOO_TICKERS
    ts = now if now is not None else time.monotonic()
    # Separate cache bucket from slow day-over-day so TTLs do not collide.
    bucket_tickers = {f"session:{k}": v for k, v in tickers.items()}
    bucket_key = _ticker_cache_key(bucket_tickers)

    with _fetch_lock:
        cache = _cache_for(bucket_tickers)
        if not force and cache.cooldown_until > ts:
            return dict(cache.values)

        age = ts - cache.fetched_at
        if not force and cache.values and age < YAHOO_TIMING_CACHE_TTL_SECONDS:
            return dict(cache.values)

        try:
            fresh, _ = _fetch_raw_session_changes(tickers)
            cache.values = fresh
            cache.fetched_at = ts
            cache.cooldown_until = 0.0
            cache.last_error = None
            return dict(fresh)
        except Exception as exc:
            cache.last_error = str(exc)
            if _is_rate_limit_error(exc):
                cache.cooldown_until = ts + YAHOO_RATE_LIMIT_COOLDOWN_SECONDS
                logger.warning(
                    "yahoo_timing_rate_limited bucket=%s — serving stale for %ss",
                    bucket_key,
                    YAHOO_RATE_LIMIT_COOLDOWN_SECONDS,
                )
            else:
                logger.warning("yahoo_timing_fetch_failed bucket=%s: %s", bucket_key, exc)
            return dict(cache.values)


def yahoo_cache_status() -> dict[str, Any]:
    """Expose cache age / cooldown for admin warnings."""
    ts = time.monotonic()
    entries: list[dict[str, Any]] = []
    for bucket_key, cache in _caches.items():
        age = ts - cache.fetched_at if cache.fetched_at else None
        entries.append(
            {
                "bucket": bucket_key,
                "cached_keys": len(cache.values),
                "fetched_age_seconds": round(age, 1) if age is not None else None,
                "cooldown_remaining_seconds": max(0.0, round(cache.cooldown_until - ts, 1)),
                "last_error": cache.last_error,
            }
        )
    return {
        "buckets": entries,
        "bucket_count": len(_caches),
        "ttl_seconds": YAHOO_CACHE_TTL_SECONDS,
        "timing_ttl_seconds": YAHOO_TIMING_CACHE_TTL_SECONDS,
    }


def reset_yahoo_cache_for_tests() -> None:
    global _caches
    _caches = {}


def mock_yahoo_changes(tickers: dict[str, str]) -> dict[str, float]:
    """Deterministic demo values for mock mode."""
    seeds = [-0.35, 0.12, -0.08, 0.22, -0.15, 0.05, 0.18, -0.42, 0.31, -0.05]
    out: dict[str, float] = {}
    for idx, key in enumerate(tickers):
        out[key] = seeds[idx % len(seeds)]
    return out
