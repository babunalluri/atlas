"""First-party async Kite REST — bypasses the sandbox pool for worker quotes.

Platform code calls ``api.kite.trade`` with tenant credentials resolved from the
Signals ops binding. Tenant tools / chat still use the sandbox toolkit path.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from urllib.parse import urlencode

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)

KITE_QUOTE_URL = "https://api.kite.trade/quote"
KITE_LTP_URL = "https://api.kite.trade/quote/ltp"
KITE_OHLC_URL = "https://api.kite.trade/quote/ohlc"
KITE_HISTORICAL_URL = "https://api.kite.trade/instruments/historical"

# Kite quote REST is roughly 1 req/s; historical is ~3 req/s. Keep separate
# buckets so candle fan-out does not serialize behind live quote fetches.
_QUOTE_INTERVAL_S = 1.05
_HISTORY_INTERVAL_S = 0.34
_BUCKET_INTERVAL_S = {
    "quote": _QUOTE_INTERVAL_S,
    "history": _HISTORY_INTERVAL_S,
}
# Back-compat alias used by older tests / call sites.
_MIN_INTERVAL_S = _QUOTE_INTERVAL_S
_429_BACKOFF_S = 2.0
# Cap how long a caller waits for a rate slot (avoids unbounded queues holding
# DB sessions during a 429 storm).
_RATE_WAIT_DEADLINE_S = 8.0

_HISTORY_INTERVALS = frozenset(
    {
        "minute",
        "3minute",
        "5minute",
        "10minute",
        "15minute",
        "30minute",
        "60minute",
        "day",
    }
)

# In-flight coalescing: overlapping Signal + Options Lab symbol sets share one
# HTTP round-trip instead of stampeding the broker / sandbox pool.
_inflight: dict[str, asyncio.Future[Any]] = {}
_inflight_lock = asyncio.Lock()
_rate_lock = asyncio.Lock()
# Per-(bucket, api_key) next-allowed monotonic timestamps (multi-tenant safe).
_next_allowed_at: dict[str, float] = {}
_shared_client: httpx.AsyncClient | None = None
_shared_client_lock = asyncio.Lock()


def reset_kite_rest_for_tests() -> None:
    global _shared_client
    _inflight.clear()
    _next_allowed_at.clear()
    # Drop the shared client handle; GC / process end closes sockets.
    _shared_client = None


def _rate_key(api_key: str, *, bucket: str = "quote") -> str:
    return f"{bucket}:{(api_key or '_')[:24]}"


def _coalesce_key(
    api_key: str,
    *,
    mode: str,
    symbols: list[str],
) -> str:
    norm = sorted({s.strip() for s in symbols if s and s.strip()})
    return f"{api_key[:8]}:{mode}:{','.join(norm)}"


def _history_coalesce_key(
    api_key: str,
    *,
    instrument_token: int,
    interval: str,
    from_date: str,
    to_date: str,
    continuous: int,
    oi: int,
) -> str:
    return (
        f"{api_key[:8]}:hist:{instrument_token}:{interval}:"
        f"{from_date}:{to_date}:{int(continuous)}:{int(oi)}"
    )


def _auth_headers(api_key: str, access_token: str) -> dict[str, str]:
    return {
        "X-Kite-Version": "3",
        "Authorization": f"token {api_key}:{access_token}",
    }


def _normalize_kite_quote_payload(data: Any) -> dict[str, Any]:
    """Map Kite REST ``data`` object into the desk quote map shape."""
    if not isinstance(data, dict):
        return {}
    out: dict[str, Any] = {}
    for key, row in data.items():
        if not isinstance(row, dict):
            continue
        # Preserve exchange:symbol keys from Kite.
        out[str(key)] = row
    return out


def _normalize_kite_history_payload(data: Any) -> dict[str, Any]:
    """Map Kite historical ``data`` into toolkit-shaped ``{ok, data.candles}``."""
    candles: list[Any] = []
    if isinstance(data, dict):
        raw = data.get("candles")
        if isinstance(raw, list):
            candles = raw
    return {"ok": True, "data": {"candles": candles}}


async def _get_shared_client(timeout_s: float) -> httpx.AsyncClient:
    """Reuse one AsyncClient so TLS handshakes are not paid per quote call."""
    global _shared_client
    async with _shared_client_lock:
        if _shared_client is None or _shared_client.is_closed:
            _shared_client = httpx.AsyncClient(
                timeout=httpx.Timeout(timeout_s),
                limits=httpx.Limits(max_connections=8, max_keepalive_connections=4),
            )
        return _shared_client


async def _await_rate_slot(
    api_key: str,
    *,
    bucket: str = "quote",
    penalize_s: float = 0.0,
    max_wait_s: float = _RATE_WAIT_DEADLINE_S,
) -> bool:
    """Reserve a per-api_key slot for ``bucket``; sleep outside the lock.

    Returns False when the wait would exceed ``max_wait_s`` (caller should
    fail closed instead of stacking behind a 429 storm).
    """
    key = _rate_key(api_key, bucket=bucket)
    interval = float(_BUCKET_INTERVAL_S.get(bucket, _QUOTE_INTERVAL_S))
    async with _rate_lock:
        now = time.monotonic()
        next_at = float(_next_allowed_at.get(key, 0.0))
        wait_s = max(0.0, next_at - now) + max(0.0, penalize_s)
        if wait_s > max(0.0, max_wait_s):
            return False
        # Reserve immediately so concurrent waiters queue without holding the lock
        # during sleep.
        _next_allowed_at[key] = now + wait_s + interval
    if wait_s > 0:
        await asyncio.sleep(wait_s)
    return True


async def _http_get_quotes(
    *,
    api_key: str,
    access_token: str,
    symbols: list[str],
    mode: str,
    timeout_s: float,
) -> dict[str, Any]:
    url = {
        "quote": KITE_QUOTE_URL,
        "ltp": KITE_LTP_URL,
        "ohlc": KITE_OHLC_URL,
    }.get(mode, KITE_QUOTE_URL)
    # Kite expects repeated ``i=`` query params.
    query = urlencode([("i", s) for s in symbols])
    full_url = f"{url}?{query}"
    headers = _auth_headers(api_key, access_token)
    slot_deadline = min(_RATE_WAIT_DEADLINE_S, max(1.0, timeout_s))

    if not await _await_rate_slot(api_key, bucket="quote", max_wait_s=slot_deadline):
        raise RuntimeError("kite REST rate slot wait exceeded")
    client = await _get_shared_client(timeout_s)
    response = await client.get(full_url, headers=headers, timeout=timeout_s)

    if response.status_code == 429:
        logger.warning(
            "kite_rest_rate_limited",
            mode=mode,
            bucket="quote",
            symbols=symbols[:8],
            retry_after_s=_429_BACKOFF_S,
        )
        if not await _await_rate_slot(
            api_key,
            bucket="quote",
            penalize_s=_429_BACKOFF_S,
            max_wait_s=slot_deadline + _429_BACKOFF_S,
        ):
            raise RuntimeError("kite REST rate slot wait exceeded after 429")
        response = await client.get(full_url, headers=headers, timeout=timeout_s)

    if response.status_code >= 400:
        body = response.text[:240]
        raise RuntimeError(f"kite REST {response.status_code}: {body}")
    payload = response.json()
    if not isinstance(payload, dict):
        return {}
    if payload.get("status") == "error":
        raise RuntimeError(str(payload.get("message") or "kite REST error")[:240])
    data = payload.get("data")
    return _normalize_kite_quote_payload(data)


async def _http_get_history(
    *,
    api_key: str,
    access_token: str,
    instrument_token: int,
    interval: str,
    from_date: str,
    to_date: str,
    continuous: int,
    oi: int,
    timeout_s: float,
) -> dict[str, Any]:
    path = f"{KITE_HISTORICAL_URL}/{int(instrument_token)}/{interval}"
    query = urlencode(
        {
            "from": from_date,
            "to": to_date,
            "continuous": int(continuous),
            "oi": int(oi),
        }
    )
    full_url = f"{path}?{query}"
    headers = _auth_headers(api_key, access_token)
    slot_deadline = min(_RATE_WAIT_DEADLINE_S, max(1.0, timeout_s))

    if not await _await_rate_slot(
        api_key, bucket="history", max_wait_s=slot_deadline
    ):
        raise RuntimeError("kite REST history rate slot wait exceeded")
    client = await _get_shared_client(timeout_s)
    response = await client.get(full_url, headers=headers, timeout=timeout_s)

    if response.status_code == 429:
        logger.warning(
            "kite_rest_rate_limited",
            mode="historical",
            bucket="history",
            instrument_token=instrument_token,
            interval=interval,
            retry_after_s=_429_BACKOFF_S,
        )
        if not await _await_rate_slot(
            api_key,
            bucket="history",
            penalize_s=_429_BACKOFF_S,
            max_wait_s=slot_deadline + _429_BACKOFF_S,
        ):
            raise RuntimeError("kite REST history rate slot wait exceeded after 429")
        response = await client.get(full_url, headers=headers, timeout=timeout_s)

    if response.status_code >= 400:
        body = response.text[:240]
        raise RuntimeError(f"kite REST historical {response.status_code}: {body}")
    payload = response.json()
    if not isinstance(payload, dict):
        return _normalize_kite_history_payload({})
    if payload.get("status") == "error":
        raise RuntimeError(
            str(payload.get("message") or "kite REST historical error")[:240]
        )
    return _normalize_kite_history_payload(payload.get("data"))


async def fetch_kite_quotes(
    *,
    api_key: str,
    access_token: str,
    symbols: list[str],
    prefer: str | None = None,
    timeout_s: float = 8.0,
) -> dict[str, Any]:
    """Fetch quotes via first-party Kite REST with in-flight coalescing.

    ``prefer`` maps to quote / ltp / ohlc endpoints (``get_quote`` → quote).
    """
    clean = [s.strip() for s in symbols if s and str(s).strip()]
    if not clean or not api_key or not access_token:
        return {}

    mode = "quote"
    if prefer in {"get_ltp", "ltp"}:
        mode = "ltp"
    elif prefer in {"get_ohlc", "ohlc"}:
        mode = "ohlc"
    elif prefer in {"get_quote", "quote", None}:
        mode = "quote"

    key = _coalesce_key(api_key, mode=mode, symbols=clean)
    async with _inflight_lock:
        existing = _inflight.get(key)
        if existing is not None and not existing.done():
            waiter: asyncio.Future[dict[str, Any]] = existing
        else:
            loop = asyncio.get_running_loop()
            waiter = loop.create_future()
            _inflight[key] = waiter

            async def _run() -> None:
                try:
                    result = await _http_get_quotes(
                        api_key=api_key,
                        access_token=access_token,
                        symbols=clean,
                        mode=mode,
                        timeout_s=timeout_s,
                    )
                    if not waiter.done():
                        waiter.set_result(result)
                except Exception as exc:  # noqa: BLE001
                    if not waiter.done():
                        waiter.set_exception(exc)
                finally:
                    async with _inflight_lock:
                        if _inflight.get(key) is waiter:
                            _inflight.pop(key, None)

            asyncio.create_task(_run())

    try:
        return await asyncio.shield(waiter)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "kite_rest_quote_failed",
            mode=mode,
            symbols=clean[:8],
            error=str(exc)[:240],
        )
        return {}


async def fetch_kite_history(
    *,
    api_key: str,
    access_token: str,
    instrument_token: int,
    interval: str,
    from_date: str,
    to_date: str,
    continuous: int = 0,
    oi: int = 0,
    timeout_s: float = 12.0,
) -> dict[str, Any] | None:
    """Fetch historical candles via first-party Kite REST (~3 req/s bucket).

    Returns toolkit-shaped ``{ok: True, data: {candles: [...]}}`` on success,
    or ``None`` so callers can fall back to the sandbox toolkit path.
    """
    if not api_key or not access_token:
        return None
    try:
        token = int(instrument_token)
    except (TypeError, ValueError):
        return None
    if token <= 0:
        return None
    bucket = str(interval or "").strip().lower()
    if bucket not in _HISTORY_INTERVALS:
        logger.warning("kite_rest_history_bad_interval", interval=interval)
        return None
    from_s = str(from_date or "").strip()
    to_s = str(to_date or "").strip()
    if not from_s or not to_s:
        return None

    key = _history_coalesce_key(
        api_key,
        instrument_token=token,
        interval=bucket,
        from_date=from_s,
        to_date=to_s,
        continuous=int(continuous),
        oi=int(oi),
    )
    async with _inflight_lock:
        existing = _inflight.get(key)
        if existing is not None and not existing.done():
            waiter: asyncio.Future[Any] = existing
        else:
            loop = asyncio.get_running_loop()
            waiter = loop.create_future()
            _inflight[key] = waiter

            async def _run() -> None:
                try:
                    result = await _http_get_history(
                        api_key=api_key,
                        access_token=access_token,
                        instrument_token=token,
                        interval=bucket,
                        from_date=from_s,
                        to_date=to_s,
                        continuous=int(continuous),
                        oi=int(oi),
                        timeout_s=timeout_s,
                    )
                    if not waiter.done():
                        waiter.set_result(result)
                except Exception as exc:  # noqa: BLE001
                    if not waiter.done():
                        waiter.set_exception(exc)
                finally:
                    async with _inflight_lock:
                        if _inflight.get(key) is waiter:
                            _inflight.pop(key, None)

            asyncio.create_task(_run())

    try:
        out = await asyncio.shield(waiter)
        return out if isinstance(out, dict) else None
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "kite_rest_history_failed",
            instrument_token=token,
            interval=bucket,
            error=str(exc)[:240],
        )
        return None
