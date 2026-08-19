"""NSE public endpoints — FII/DII and market breadth (slow tier)."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

NSE_CACHE_TTL_SECONDS = 3_600
NSE_COOLDOWN_SECONDS = 1_800
NSE_BASE = "https://www.nseindia.com"

_cache: dict[str, Any] = {"fetched_at": 0.0, "payload": {}, "cooldown_until": 0.0}
_lock = threading.Lock()


def _session():
    try:
        from curl_cffi import requests as curl_requests

        return curl_requests.Session(impersonate="chrome")
    except ImportError:
        return None


def _nse_get(path: str, session: Any) -> Any | None:
    if session is None:
        return None
    try:
        session.get(f"{NSE_BASE}/", timeout=15)
        resp = session.get(f"{NSE_BASE}{path}", timeout=20)
        if resp.status_code != 200:
            return None
        return resp.json()
    except Exception as exc:
        logger.warning("nse_fetch_failed path=%s err=%s", path, exc)
        return None


def _parse_fii_dii(body: Any) -> float | None:
    if not isinstance(body, dict):
        return None
    rows = body.get("data")
    if not isinstance(rows, list):
        return None
    for row in rows:
        if not isinstance(row, dict):
            continue
        cat = str(row.get("category") or row.get("Category") or "").upper()
        if "FII" in cat or "FPI" in cat:
            for key in ("netValue", "fiiNetValue", "Net Value (₹ Crores)", "net"):
                val = row.get(key)
                if val is None or val == "":
                    continue
                try:
                    return round(float(str(val).replace(",", "")), 2)
                except (TypeError, ValueError):
                    continue
    return None


def _unwrap_nse_rows(body: Any) -> list[Any] | None:
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        data = body.get("data")
        if isinstance(data, list):
            return data
    return None


def _parse_advance_decline(body: Any) -> float | None:
    """Breadth proxy from NSE allIndices if available."""
    rows = _unwrap_nse_rows(body)
    if not rows:
        return None
    adv = dec = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        pct = row.get("percentChange") or row.get("pChange")
        if pct is None:
            continue
        try:
            chg = float(pct)
        except (TypeError, ValueError):
            continue
        if chg > 0:
            adv += 1
        elif chg < 0:
            dec += 1
    if adv + dec == 0:
        return None
    return round(adv / max(dec, 1), 3)


def fetch_nse_slow_fields(*, force: bool = False) -> dict[str, float]:
    """FII net + advance/decline ratio. Cached ~1 h."""
    ts = time.monotonic()
    with _lock:
        if not force and _cache["cooldown_until"] > ts:
            return dict(_cache["payload"])
        age = ts - float(_cache["fetched_at"] or 0)
        if not force and _cache["payload"] and age < NSE_CACHE_TTL_SECONDS:
            return dict(_cache["payload"])

        session = _session()
        payload: dict[str, float] = dict(_cache["payload"])

        for path in (
            "/api/fiidiiTradeReact",
            "/api/fiidii-trend-data",
            "/api/fiidiiTradeData",
        ):
            body = _nse_get(path, session)
            fii = _parse_fii_dii(body)
            if fii is not None:
                payload["fii_net"] = fii
                break

        indices = _nse_get("/api/allIndices", session)
        adr = _parse_advance_decline(indices)
        if adr is not None:
            payload["advance_decline_ratio"] = adr

        if payload:
            _cache["payload"] = payload
            _cache["fetched_at"] = ts
            _cache["cooldown_until"] = 0.0
        else:
            _cache["cooldown_until"] = ts + NSE_COOLDOWN_SECONDS

        return dict(payload)


def mock_nse_fields() -> dict[str, float]:
    return {"fii_net": 850.0, "advance_decline_ratio": 1.15}


def reset_nse_cache_for_tests() -> None:
    global _cache
    _cache = {"fetched_at": 0.0, "payload": {}, "cooldown_until": 0.0}
