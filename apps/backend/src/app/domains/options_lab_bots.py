"""Options Lab paper bots — tenant session store + entry gates + run log.

Live never auto-fires (worker / evaluate only paper). HITL Run once may
place live when ``confirm=true`` and kill switch is off.
"""

from __future__ import annotations

import calendar
import re
import time
import uuid
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.core.logging import get_logger
from app.core.redis_client import get_redis, invalidate_redis
from app.domains.options_lab_templates import TEMPLATE_IDS, GATED_TEMPLATES
from app.domains.signal_engine_cache import get_session_value, set_session_value

logger = get_logger(__name__)

BOTS_FIELD = "options_lab:bots"
ARMED_SET_KEY = "atlas:options-lab:bots:armed"
MAX_BOTS = 24
MAX_LOG = 40
DEFAULT_COOLDOWN_SEC = 300
DEFAULT_MAX_RUNS_DAY = 3
IST = ZoneInfo("Asia/Kolkata")
MONTH_INDEX = {
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
}

_local_armed: set[str] = set()
# In-process claim fallback when Redis is down (single-process only).
_local_claims: dict[str, float] = {}


def _now_ts() -> int:
    return int(time.time())


def _now_ist() -> datetime:
    return datetime.now(IST)


def reset_bots_armed_for_tests() -> None:
    _local_armed.clear()
    _local_claims.clear()


def _last_thursday(year: int, month: int) -> date:
    last_day = calendar.monthrange(year, month)[1]
    d = date(year, month, last_day)
    while d.weekday() != 3:
        d -= timedelta(days=1)
    return d


def days_to_expiry_from_fut(fut_symbol: str | None, *, now: datetime | None = None) -> int | None:
    """Calendar days until monthly FUT last-Thursday expiry (IST date)."""
    raw = str(fut_symbol or "").strip().upper()
    if not raw:
        return None
    body = raw.split(":", 1)[-1]
    match = re.search(r"(\d{2})([A-Z]{3})FUT$", body)
    if not match:
        return None
    yy = int(match.group(1))
    mon = MONTH_INDEX.get(match.group(2))
    if mon is None:
        return None
    year = 2000 + yy
    expiry = _last_thursday(year, mon)
    ref = (now or _now_ist()).astimezone(IST).date()
    return (expiry - ref).days


def _parse_hhmm(raw: str, default: str) -> tuple[int, int]:
    text = str(raw or default).strip()
    parts = text.split(":")
    try:
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
    except (TypeError, ValueError, IndexError):
        hour, minute = (9, 15) if default.startswith("09") else (15, 30)
    return max(0, min(23, hour)), max(0, min(59, minute))


def in_schedule(schedule: dict[str, Any] | None, *, now: datetime | None = None) -> bool:
    """True when ``now`` (IST) is inside bot schedule window."""
    ist = (now or _now_ist()).astimezone(IST)
    sched = schedule if isinstance(schedule, dict) else {}
    days_raw = sched.get("days")
    if isinstance(days_raw, list) and days_raw:
        days = {int(d) for d in days_raw if str(d).lstrip("-").isdigit()}
    else:
        days = {0, 1, 2, 3, 4}  # Mon–Fri
    if ist.weekday() not in days:
        return False
    sh, sm = _parse_hhmm(str(sched.get("window_start") or "09:15"), "09:15")
    eh, em = _parse_hhmm(str(sched.get("window_end") or "15:30"), "15:30")
    mins = ist.hour * 60 + ist.minute
    return (sh * 60 + sm) <= mins <= (eh * 60 + em)


def entry_conditions_ok(
    bot: dict[str, Any],
    *,
    ivp: float | None,
    pcr: float | None,
    dte: int | None,
) -> tuple[bool, str]:
    """Evaluate optional IVP / PCR / DTE gates. Missing metric fails that gate."""
    entry = bot.get("entry") if isinstance(bot.get("entry"), dict) else {}

    def _bound(key: str) -> float | None:
        raw = entry.get(key)
        if raw is None or raw == "":
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None

    min_ivp, max_ivp = _bound("min_ivp"), _bound("max_ivp")
    min_pcr, max_pcr = _bound("min_pcr"), _bound("max_pcr")
    max_dte = _bound("max_dte")

    if min_ivp is not None or max_ivp is not None:
        if ivp is None:
            return False, "IVP unavailable for entry gate."
        if min_ivp is not None and ivp < min_ivp:
            return False, f"IVP {ivp:.0f} below min {min_ivp:.0f}."
        if max_ivp is not None and ivp > max_ivp:
            return False, f"IVP {ivp:.0f} above max {max_ivp:.0f}."

    if min_pcr is not None or max_pcr is not None:
        if pcr is None:
            return False, "PCR unavailable for entry gate."
        if min_pcr is not None and pcr < min_pcr:
            return False, f"PCR {pcr:.2f} below min {min_pcr:.2f}."
        if max_pcr is not None and pcr > max_pcr:
            return False, f"PCR {pcr:.2f} above max {max_pcr:.2f}."

    if max_dte is not None:
        if dte is None:
            return False, "DTE unavailable for entry gate."
        if dte < 0:
            return False, f"Underlying FUT expired (DTE {dte})."
        if dte > max_dte:
            return False, f"DTE {dte} above max {int(max_dte)}."

    if dte is not None and dte < 0:
        return False, f"Underlying FUT expired (DTE {dte})."

    return True, "ok"


def _ist_day_key(now: datetime | None = None) -> str:
    return (now or _now_ist()).astimezone(IST).strftime("%Y-%m-%d")


def _normalize_schedule(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {
            "days": [0, 1, 2, 3, 4],
            "window_start": "09:15",
            "window_end": "15:30",
        }
    days = raw.get("days")
    if not isinstance(days, list) or not days:
        days = [0, 1, 2, 3, 4]
    else:
        days = [int(d) for d in days if str(d).lstrip("-").isdigit()]
        days = [d for d in days if 0 <= d <= 6] or [0, 1, 2, 3, 4]
    return {
        "days": days,
        "window_start": str(raw.get("window_start") or "09:15")[:5],
        "window_end": str(raw.get("window_end") or "15:30")[:5],
    }


def _normalize_entry(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, Any] = {}
    for key in ("min_ivp", "max_ivp", "min_pcr", "max_pcr", "max_dte"):
        if raw.get(key) is None or raw.get(key) == "":
            continue
        try:
            out[key] = float(raw[key])
        except (TypeError, ValueError):
            continue
    return out


def _clamp_pct(raw: Any, *, default: float, lo: float, hi: float) -> float:
    try:
        val = float(raw)
    except (TypeError, ValueError):
        val = default
    if not (val > 0):
        return default
    return max(lo, min(hi, val))


def normalize_bot(payload: dict[str, Any], *, existing: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build a persisted bot record from create/update payload."""
    base = dict(existing or {})
    name = str(payload.get("name") or base.get("name") or "Untitled bot").strip()[:120]
    mode = str(payload.get("mode") or base.get("mode") or "paper").lower()
    if mode not in {"paper", "live"}:
        mode = "paper"
    template = payload.get("template")
    if template is None:
        template = base.get("template")
    template_s = str(template).strip() if template else None
    if template_s and (template_s not in TEMPLATE_IDS or template_s in GATED_TEMPLATES):
        template_s = None

    backtest_id = payload.get("backtest_id")
    if backtest_id is None:
        backtest_id = base.get("backtest_id")
    backtest_s = str(backtest_id).strip() if backtest_id else None

    enabled = bool(payload.get("enabled", base.get("enabled", False)))
    kill = bool(payload.get("kill", base.get("kill", False)))
    if kill:
        enabled = False

    profit_pct = _clamp_pct(
        payload.get("profit_pct", base.get("profit_pct")),
        default=50.0,
        lo=0.5,
        hi=200.0,
    )
    stop_pct = _clamp_pct(
        payload.get("stop_pct", base.get("stop_pct")),
        default=40.0,
        lo=0.5,
        hi=90.0,
    )

    try:
        cooldown = int(payload.get("cooldown_sec", base.get("cooldown_sec") or DEFAULT_COOLDOWN_SEC))
    except (TypeError, ValueError):
        cooldown = DEFAULT_COOLDOWN_SEC
    cooldown = max(60, min(3600, cooldown))

    try:
        max_runs = int(
            payload.get("max_runs_per_day", base.get("max_runs_per_day") or DEFAULT_MAX_RUNS_DAY)
        )
    except (TypeError, ValueError):
        max_runs = DEFAULT_MAX_RUNS_DAY
    max_runs = max(1, min(20, max_runs))

    width_steps = payload.get("width_steps", base.get("width_steps", 1))
    try:
        width_steps_i = max(1, min(5, int(width_steps)))
    except (TypeError, ValueError):
        width_steps_i = 1

    underlying = payload.get("underlying_symbol", base.get("underlying_symbol"))
    underlying_s = str(underlying).strip() if underlying else None

    now = _now_ts()
    bot_id = str(base.get("id") or f"bot-{uuid.uuid4().hex[:12]}")
    return {
        "id": bot_id,
        "name": name or "Untitled bot",
        "enabled": enabled,
        "kill": kill,
        "mode": mode,
        "template": template_s,
        "backtest_id": backtest_s,
        "legs": base.get("legs") if isinstance(base.get("legs"), list) else None,
        "underlying_symbol": underlying_s,
        "width_steps": width_steps_i,
        "profit_pct": profit_pct,
        "stop_pct": stop_pct,
        "schedule": _normalize_schedule(payload.get("schedule", base.get("schedule"))),
        "entry": _normalize_entry(payload.get("entry", base.get("entry"))),
        "cooldown_sec": cooldown,
        "max_runs_per_day": max_runs,
        "runs_today": int(base.get("runs_today") or 0),
        "runs_today_date": base.get("runs_today_date"),
        "last_run_at": base.get("last_run_at"),
        "last_run_message": base.get("last_run_message"),
        "log": list(base.get("log") or [])[:MAX_LOG] if isinstance(base.get("log"), list) else [],
        "created_at": int(base.get("created_at") or now),
        "updated_at": now,
        "source": str(base.get("source") or payload.get("source") or "lab"),
    }


def bot_due_for_auto(bot: dict[str, Any], *, now: datetime | None = None) -> tuple[bool, str]:
    """Whether an armed paper bot may auto-fire now."""
    if bot.get("kill"):
        return False, "Kill switch on."
    if not bot.get("enabled"):
        return False, "Disarmed."
    if str(bot.get("mode") or "paper") != "paper":
        return False, "Live never auto-fires."
    if not in_schedule(bot.get("schedule") if isinstance(bot.get("schedule"), dict) else None, now=now):
        return False, "Outside IST schedule window."
    day = _ist_day_key(now)
    runs = int(bot.get("runs_today") or 0)
    if bot.get("runs_today_date") != day:
        runs = 0
    if runs >= int(bot.get("max_runs_per_day") or DEFAULT_MAX_RUNS_DAY):
        return False, "Daily run cap reached."
    last = bot.get("last_run_at")
    if last is not None:
        try:
            elapsed = _now_ts() - int(last)
        except (TypeError, ValueError):
            elapsed = 10**9
        cool = int(bot.get("cooldown_sec") or DEFAULT_COOLDOWN_SEC)
        if elapsed < cool:
            return False, f"Cooldown ({cool - elapsed}s left)."
    return True, "ok"


def claim_run_slot(bot: dict[str, Any], *, message: str = "Claimed — placing…") -> dict[str, Any]:
    """Stamp last_run_at before place so concurrent due-checks fail cooldown."""
    now = _now_ts()
    return {
        **bot,
        "last_run_at": now,
        "last_run_message": str(message)[:400],
        "updated_at": now,
    }


def _claim_key(tenant_id: str, bot_id: str) -> str:
    return f"atlas:options-lab:bots:claim:{tenant_id}:{bot_id}"


async def try_claim_bot_run(
    tenant_id: str,
    bot_id: str,
    *,
    cooldown_sec: int = DEFAULT_COOLDOWN_SEC,
) -> bool:
    """Atomic NX claim across processes. TTL ≈ cooldown so duplicates cannot re-enter."""
    cool = max(60, min(3600, int(cooldown_sec or DEFAULT_COOLDOWN_SEC)))
    key = _claim_key(tenant_id, bot_id)
    px = cool * 1000
    client = await get_redis()
    if client is not None:
        try:
            acquired = await client.set(key, str(_now_ts()), nx=True, px=px)
            return bool(acquired)
        except Exception:
            await invalidate_redis()
            logger.warning(
                "options_lab_bots_claim_failed",
                tenant_id=tenant_id,
                bot_id=bot_id,
            )

    # Local fallback — only safe within one process.
    now = time.monotonic()
    expires = _local_claims.get(key)
    if expires is not None and expires > now:
        return False
    _local_claims[key] = now + cool
    return True


def append_run_log(
    bot: dict[str, Any],
    *,
    ok: bool,
    message: str,
    auto: bool,
) -> dict[str, Any]:
    """Update run counters + ring log after an attempt."""
    now = _now_ts()
    day = _ist_day_key()
    runs = int(bot.get("runs_today") or 0)
    if bot.get("runs_today_date") != day:
        runs = 0
    if ok:
        runs += 1
    log = list(bot.get("log") or [])
    log.insert(
        0,
        {
            "ts": now,
            "ok": ok,
            "auto": auto,
            "message": str(message)[:400],
        },
    )
    bot = {
        **bot,
        "runs_today": runs,
        "runs_today_date": day,
        "last_run_at": now,
        "last_run_message": str(message)[:400],
        "log": log[:MAX_LOG],
        "updated_at": now,
    }
    if not ok and auto:
        bot["enabled"] = False
    return bot


async def load_bots(tenant_id: str) -> list[dict[str, Any]]:
    stored = await get_session_value(tenant_id, BOTS_FIELD)
    if not isinstance(stored, dict):
        return []
    rows = stored.get("items")
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


async def save_bots(tenant_id: str, items: list[dict[str, Any]]) -> None:
    await set_session_value(
        tenant_id,
        BOTS_FIELD,
        {"items": items[:MAX_BOTS], "updated_at": _now_ts()},
    )
    await sync_armed_watch(tenant_id, items)


def _has_armed_paper(items: list[dict[str, Any]]) -> bool:
    return any(
        b.get("enabled") and not b.get("kill") and str(b.get("mode") or "paper") == "paper"
        for b in items
    )


async def sync_armed_watch(tenant_id: str, items: list[dict[str, Any]]) -> None:
    armed = _has_armed_paper(items)
    client = await get_redis()
    if client is not None:
        try:
            if armed:
                await client.sadd(ARMED_SET_KEY, tenant_id)
            else:
                await client.srem(ARMED_SET_KEY, tenant_id)
            return
        except Exception:
            await invalidate_redis()
            logger.warning("options_lab_bots_armed_sync_failed", tenant_id=tenant_id)
    if armed:
        _local_armed.add(tenant_id)
    else:
        _local_armed.discard(tenant_id)


async def list_armed_tenant_ids() -> list[str]:
    client = await get_redis()
    if client is not None:
        try:
            members = await client.smembers(ARMED_SET_KEY)
            return sorted(str(m) for m in (members or []))
        except Exception:
            await invalidate_redis()
            logger.warning("options_lab_bots_armed_list_failed")
    return sorted(_local_armed)


async def list_bots(tenant_id: str) -> dict[str, Any]:
    items = await load_bots(tenant_id)
    slim = []
    for row in items:
        slim.append(
            {
                "id": row.get("id"),
                "name": row.get("name"),
                "enabled": bool(row.get("enabled")),
                "kill": bool(row.get("kill")),
                "mode": row.get("mode") or "paper",
                "template": row.get("template"),
                "backtest_id": row.get("backtest_id"),
                "underlying_symbol": row.get("underlying_symbol"),
                "profit_pct": row.get("profit_pct"),
                "stop_pct": row.get("stop_pct"),
                "schedule": row.get("schedule"),
                "entry": row.get("entry"),
                "cooldown_sec": row.get("cooldown_sec"),
                "max_runs_per_day": row.get("max_runs_per_day"),
                "runs_today": row.get("runs_today"),
                "runs_today_date": row.get("runs_today_date"),
                "last_run_at": row.get("last_run_at"),
                "last_run_message": row.get("last_run_message"),
                "log": (row.get("log") or [])[:8],
                "created_at": row.get("created_at"),
                "updated_at": row.get("updated_at"),
                "source": row.get("source"),
                "leg_count": len(row.get("legs") or []) if isinstance(row.get("legs"), list) else 0,
            }
        )
    return {
        "ok": True,
        "bots": slim,
        "count": len(slim),
        "armed_paper": sum(
            1
            for b in slim
            if b.get("enabled") and not b.get("kill") and b.get("mode") == "paper"
        ),
    }


async def get_bot(tenant_id: str, bot_id: str) -> dict[str, Any]:
    for row in await load_bots(tenant_id):
        if row.get("id") == bot_id:
            return {"ok": True, "bot": row}
    return {"ok": False, "error": "Bot not found."}


async def create_bot(tenant_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    items = await load_bots(tenant_id)
    if len(items) >= MAX_BOTS:
        return {"ok": False, "error": f"Maximum {MAX_BOTS} bots reached."}
    bot = normalize_bot(payload)
    if not bot.get("template") and not bot.get("backtest_id") and not bot.get("legs"):
        return {"ok": False, "error": "template, backtest_id, or legs required."}
    # Allow legs from payload on create (backtest handoff).
    if isinstance(payload.get("legs"), list) and payload["legs"]:
        bot["legs"] = payload["legs"]
    items.insert(0, bot)
    await save_bots(tenant_id, items)
    return {"ok": True, "bot": bot}


async def update_bot(tenant_id: str, bot_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    items = await load_bots(tenant_id)
    for idx, row in enumerate(items):
        if row.get("id") != bot_id:
            continue
        next_bot = normalize_bot(payload, existing=row)
        if isinstance(payload.get("legs"), list):
            next_bot["legs"] = payload["legs"]
        if not next_bot.get("template") and not next_bot.get("backtest_id") and not next_bot.get("legs"):
            return {"ok": False, "error": "template, backtest_id, or legs required."}
        items[idx] = next_bot
        await save_bots(tenant_id, items)
        return {"ok": True, "bot": next_bot}
    return {"ok": False, "error": "Bot not found."}


async def delete_bot(tenant_id: str, bot_id: str) -> dict[str, Any]:
    items = await load_bots(tenant_id)
    next_items = [row for row in items if row.get("id") != bot_id]
    if len(next_items) == len(items):
        return {"ok": False, "error": "Bot not found."}
    await save_bots(tenant_id, next_items)
    return {"ok": True}


async def replace_bot(tenant_id: str, bot: dict[str, Any]) -> None:
    """Persist an already-mutated bot row (after run log update)."""
    items = await load_bots(tenant_id)
    for idx, row in enumerate(items):
        if row.get("id") == bot.get("id"):
            items[idx] = bot
            await save_bots(tenant_id, items)
            return
    items.insert(0, bot)
    await save_bots(tenant_id, items)
