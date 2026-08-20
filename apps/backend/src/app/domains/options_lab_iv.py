"""Options Lab — ATM IV history and IV percentile (IVP)."""

from __future__ import annotations

import hashlib
import time
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.domains.signal_engine_cache import get_session_value, set_session_value

IV_HISTORY_FIELD = "options_lab:iv_history"
IV_HISTORY_MAX = 252
IVP_MIN_SAMPLES = 5
MOCK_HISTORY_DAYS = 90


def _ist_trading_day(when: datetime | None = None) -> str:
    now = when or datetime.now(ZoneInfo("Asia/Kolkata"))
    return now.strftime("%Y-%m-%d")


def compute_ivp(samples: list[float], current_iv: float | None) -> float | None:
    if current_iv is None:
        return None
    clean = [float(v) for v in samples if v is not None]
    if len(clean) < IVP_MIN_SAMPLES:
        return None
    below = sum(1 for value in clean if value < current_iv)
    return round(below / len(clean) * 100, 1)


def _seed_for_symbol(symbol: str) -> int:
    digest = hashlib.md5(symbol.encode()).hexdigest()
    return int(digest[:8], 16)


def generate_mock_iv_history(
    symbol: str,
    anchor_iv: float,
    *,
    days: int = MOCK_HISTORY_DAYS,
) -> list[dict[str, Any]]:
    seed = _seed_for_symbol(symbol)
    anchor = max(5.0, float(anchor_iv))
    today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
    out: list[dict[str, Any]] = []
    for offset in range(days - 1, -1, -1):
        day = today - timedelta(days=offset)
        wave = (seed % 17) / 10 + (offset % 11) * 0.08
        iv = max(4.0, anchor + (offset % 7 - 3) * 0.35 + wave)
        out.append({"day": day.isoformat(), "iv": round(iv, 2)})
    return out


async def get_iv_series(tenant_id: str, symbol: str) -> list[dict[str, Any]]:
    stored = await get_session_value(tenant_id, IV_HISTORY_FIELD)
    if not isinstance(stored, dict):
        return []
    series = stored.get(symbol)
    return list(series) if isinstance(series, list) else []


async def record_atm_iv(
    tenant_id: str,
    symbol: str,
    atm_iv: float | None,
    *,
    mock: bool = False,
) -> list[dict[str, Any]]:
    if not symbol.strip() or atm_iv is None:
        return await get_iv_series(tenant_id, symbol)

    stored_raw = await get_session_value(tenant_id, IV_HISTORY_FIELD)
    stored: dict[str, Any] = dict(stored_raw) if isinstance(stored_raw, dict) else {}
    series: list[dict[str, Any]] = list(stored.get(symbol) or [])

    if mock and len(series) < IVP_MIN_SAMPLES:
        series = generate_mock_iv_history(symbol, atm_iv)

    today = _ist_trading_day()
    point = {"day": today, "iv": round(float(atm_iv), 2), "t": int(time.time())}
    if series and series[-1].get("day") == today:
        series[-1] = point
    else:
        series.append(point)
    if len(series) > IV_HISTORY_MAX:
        series = series[-IV_HISTORY_MAX:]

    stored[symbol] = series
    await set_session_value(tenant_id, IV_HISTORY_FIELD, stored)
    return series


async def ivp_for_symbol(
    tenant_id: str,
    symbol: str,
    current_iv: float | None,
    *,
    mock: bool = False,
) -> float | None:
    series = await record_atm_iv(tenant_id, symbol, current_iv, mock=mock)
    return compute_ivp([float(row["iv"]) for row in series if row.get("iv") is not None], current_iv)


async def build_iv_chart_payload(
    tenant_id: str,
    symbol: str,
    atm_iv: float | None,
    *,
    mock: bool = False,
) -> dict[str, Any]:
    series = await record_atm_iv(tenant_id, symbol, atm_iv, mock=mock)
    samples = [float(row["iv"]) for row in series if row.get("iv") is not None]
    return {
        "points": series,
        "atm_iv": atm_iv,
        "ivp": compute_ivp(samples, atm_iv),
        "sample_days": len(samples),
    }


async def reset_iv_history(tenant_id: str) -> None:
    await set_session_value(tenant_id, IV_HISTORY_FIELD, None)
