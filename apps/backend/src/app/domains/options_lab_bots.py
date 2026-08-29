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
from app.domains.signal_engine_calendar import (
    FOMC_MEETING_DATES,
    days_until_next_fomc,
)

logger = get_logger(__name__)

BOTS_FIELD = "options_lab:bots"
ARMED_SET_KEY = "atlas:options-lab:bots:armed"
MAX_BOTS = 24
# Per-user ceiling inside the shared tenant blob (operators are uncapped).
MAX_BOTS_PER_OWNER = 10
MAX_LOG = 40
DEFAULT_COOLDOWN_SEC = 300
DEFAULT_MAX_RUNS_DAY = 3
IST = ZoneInfo("Asia/Kolkata")

# Static NSE trading holidays (India) — desk fallback when live holiday feed is offline.
# 2026 sourced from NSE/CMTR circulars (via public calendars). 2027 is provisional
# until the exchange publishes the official list — verify yearly.
NSE_HOLIDAYS_STATIC: frozenset[date] = frozenset(
    {
        # 2026
        date(2026, 1, 15),  # Municipal elections (Maharashtra)
        date(2026, 1, 26),  # Republic Day
        date(2026, 3, 3),  # Holi
        date(2026, 3, 26),  # Shri Ram Navami
        date(2026, 3, 31),  # Mahavir Jayanti
        date(2026, 4, 3),  # Good Friday
        date(2026, 4, 14),  # Ambedkar Jayanti
        date(2026, 5, 1),  # Maharashtra Day
        date(2026, 5, 28),  # Bakri Id
        date(2026, 6, 26),  # Muharram
        date(2026, 8, 15),  # Independence Day (Sat)
        date(2026, 9, 14),  # Ganesh Chaturthi
        date(2026, 10, 2),  # Gandhi Jayanti
        date(2026, 10, 20),  # Dussehra
        date(2026, 11, 8),  # Diwali Laxmi Pujan (Muhurat day)
        date(2026, 11, 10),  # Diwali Balipratipada
        date(2026, 11, 24),  # Guru Nanak Jayanti
        date(2026, 12, 25),  # Christmas
        # 2027 (provisional third-party calendar — replace when NSE circular lands)
        date(2027, 1, 26),  # Republic Day
        date(2027, 3, 6),  # Maha Shivaratri (Sat)
        date(2027, 3, 10),  # Id-ul-Fitr
        date(2027, 3, 22),  # Holi
        date(2027, 3, 26),  # Good Friday
        date(2027, 4, 14),  # Ambedkar Jayanti
        date(2027, 4, 15),  # Ram Navami
        date(2027, 4, 19),  # Mahavir Jayanti
        date(2027, 5, 1),  # Maharashtra Day (Sat)
        date(2027, 5, 17),  # Bakri Id
        date(2027, 6, 15),  # Muharram
        date(2027, 8, 15),  # Independence Day (Sun)
        date(2027, 9, 4),  # Ganesh Chaturthi (Sat)
        date(2027, 10, 2),  # Gandhi Jayanti (Sat)
        date(2027, 10, 10),  # Dussehra (Sun)
        date(2027, 10, 29),  # Diwali window
        date(2027, 11, 14),  # Guru Nanak Jayanti (Sun)
        date(2027, 12, 25),  # Christmas (Sat)
    }
)
_HOLIDAY_THIN_WEEKDAY_MIN = 8
_holiday_thin_years_warned: set[int] = set()
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
    _holiday_thin_years_warned.clear()


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


def _warn_holiday_coverage(ref: date, holidays: frozenset[date]) -> None:
    """Log once per year when weekday holiday density looks thin or table is past max."""
    if holidays:
        max_known = max(holidays)
        if ref > max_known:
            logger.warning(
                "options_lab_bots_holiday_table_stale",
                ref=ref.isoformat(),
                max_known=max_known.isoformat(),
            )
    year = ref.year
    if year in _holiday_thin_years_warned:
        return
    weekdays = sum(1 for d in holidays if d.year == year and d.weekday() < 5)
    if weekdays < _HOLIDAY_THIN_WEEKDAY_MIN:
        _holiday_thin_years_warned.add(year)
        logger.warning(
            "options_lab_bots_holiday_table_thin",
            year=year,
            weekday_holidays=weekdays,
            min_expected=_HOLIDAY_THIN_WEEKDAY_MIN,
        )


def nse_holidays_effective(*, live: frozenset[date] | set[date] | None = None) -> frozenset[date]:
    """Static desk table ∪ optional live NSE dates (caller supplies live; no network here)."""
    if live is None:
        return NSE_HOLIDAYS_STATIC
    return frozenset(NSE_HOLIDAYS_STATIC | set(live))


async def load_nse_holidays_effective() -> frozenset[date]:
    """Fetch live holiday-master off the event loop, then union with static table."""
    import asyncio

    live: frozenset[date] = frozenset()
    try:
        from app.domains.signal_engine_nse import fetch_nse_holiday_dates

        live = await asyncio.to_thread(fetch_nse_holiday_dates)
    except Exception as exc:  # noqa: BLE001
        logger.debug("options_lab_bots_nse_holiday_live_unavailable err=%s", exc)
    return nse_holidays_effective(live=live)


def event_avoid_reason(
    *,
    now: datetime | None = None,
    holidays: frozenset[date] | None = None,
) -> str | None:
    """Why auto entry should skip today (IST). None = clear to enter.

    Pass ``holidays`` from ``load_nse_holidays_effective()`` on async paths.
    Default is static-only (never blocks on NSE HTTP).
    """
    ist = (now or _now_ist()).astimezone(IST)
    ref = ist.date()
    hol = holidays if holidays is not None else NSE_HOLIDAYS_STATIC
    _warn_holiday_coverage(ref, hol)
    if ref in hol:
        return f"NSE holiday ({ref.isoformat()})."
    fomc = days_until_next_fomc(ref)
    if fomc is not None and fomc == 0:
        return "Fed / FOMC meeting day (macro event-avoid)."
    if ref in FOMC_MEETING_DATES:
        return "Fed / FOMC meeting day (macro event-avoid)."
    return None


def dte_exit_due(bot: dict[str, Any], *, dte: int | None) -> tuple[bool, str]:
    """True when open position should flatten (remaining DTE <= max_dte_hold)."""
    raw = bot.get("max_dte_hold")
    if raw is None or raw == "":
        return False, "No max_dte_hold."
    try:
        hold = int(raw)
    except (TypeError, ValueError):
        return False, "Invalid max_dte_hold."
    if hold < 0:
        return False, "max_dte_hold disabled."
    if dte is None:
        return False, "DTE unavailable."
    if dte <= hold:
        return True, f"DTE {dte} ≤ hold {hold}."
    return False, "Within hold window."


def flip_legs_for_exit(legs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reverse buy/sell to flatten a multi-leg book."""
    out: list[dict[str, Any]] = []
    for idx, leg in enumerate(legs):
        if not isinstance(leg, dict):
            continue
        side = str(leg.get("side") or "buy").lower()
        flipped = "sell" if side == "buy" else "buy"
        out.append({**leg, "side": flipped, "id": str(leg.get("id") or f"exit-{idx}")})
    return out


def submitted_leg_indices(placed: dict[str, Any]) -> set[int] | None:
    """Indices of legs the broker accepted.

    Returns ``None`` for mock / unknown-all-ok (caller should treat as every leg).
    Empty set means nothing submitted.
    """
    if placed.get("mock"):
        return None
    orders = placed.get("orders") if isinstance(placed.get("orders"), list) else None
    if not orders:
        # Full ok with no order rows — treat as all legs (defensive).
        if placed.get("ok") and not placed.get("partial"):
            return None
        return set()
    idxs: set[int] = set()
    for row in orders:
        if not isinstance(row, dict):
            continue
        if str(row.get("status") or "") != "submitted":
            continue
        try:
            idxs.add(int(row["leg_index"]))
        except (KeyError, TypeError, ValueError):
            continue
    return idxs


def legs_from_placement(
    legs: list[dict[str, Any]],
    placed: dict[str, Any],
) -> list[dict[str, Any]]:
    """Subset of ``legs`` that were actually submitted (or all legs on mock/full-ok)."""
    idxs = submitted_leg_indices(placed)
    if idxs is None:
        return [leg for leg in legs if isinstance(leg, dict)]
    return [leg for i, leg in enumerate(legs) if i in idxs and isinstance(leg, dict)]


def residual_open_legs_after_exit(
    open_legs: list[dict[str, Any]],
    placed: dict[str, Any],
) -> list[dict[str, Any]]:
    """Legs still open after an exit place (drop only successfully closed indices)."""
    idxs = submitted_leg_indices(placed)
    if idxs is None:
        return []
    return [
        leg
        for i, leg in enumerate(open_legs)
        if i not in idxs and isinstance(leg, dict)
    ]


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
    # Ownership is set once, on create, and never moves: an update payload can
    # not reassign a bot to another user.
    owner_id = base.get("owner_id") or payload.get("owner_id")
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

    avoid_events = bool(payload.get("avoid_events", base.get("avoid_events", False)))
    max_dte_hold_raw = payload.get("max_dte_hold", base.get("max_dte_hold"))
    max_dte_hold: int | None
    if max_dte_hold_raw is None or max_dte_hold_raw == "":
        max_dte_hold = None
    else:
        try:
            max_dte_hold = max(0, min(30, int(max_dte_hold_raw)))
        except (TypeError, ValueError):
            max_dte_hold = None

    # Operator escape hatch when exit is wedged (symbols off-chain, etc.).
    open_position = base.get("open_position") if isinstance(base.get("open_position"), dict) else None
    if payload.get("clear_open_position") is True:
        open_position = None

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
        "owner_id": str(owner_id) if owner_id else None,
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
        "avoid_events": avoid_events,
        "max_dte_hold": max_dte_hold,
        "schedule": _normalize_schedule(payload.get("schedule", base.get("schedule"))),
        "entry": _normalize_entry(payload.get("entry", base.get("entry"))),
        "cooldown_sec": cooldown,
        "max_runs_per_day": max_runs,
        "runs_today": int(base.get("runs_today") or 0),
        "runs_today_date": base.get("runs_today_date"),
        "last_run_at": base.get("last_run_at"),
        "last_run_message": base.get("last_run_message"),
        "open_position": open_position
        if isinstance(open_position, dict)
        else None,
        "log": list(base.get("log") or [])[:MAX_LOG] if isinstance(base.get("log"), list) else [],
        "created_at": int(base.get("created_at") or now),
        "updated_at": now,
        "source": str(base.get("source") or payload.get("source") or "lab"),
    }


def bot_due_for_auto(
    bot: dict[str, Any],
    *,
    now: datetime | None = None,
    holidays: frozenset[date] | None = None,
) -> tuple[bool, str]:
    """Whether an armed paper bot may auto-fire now."""
    if bot.get("kill"):
        return False, "Kill switch on."
    if not bot.get("enabled"):
        return False, "Disarmed."
    if str(bot.get("mode") or "paper") != "paper":
        return False, "Live never auto-fires."
    if not in_schedule(bot.get("schedule") if isinstance(bot.get("schedule"), dict) else None, now=now):
        return False, "Outside IST schedule window."
    if bot.get("avoid_events"):
        reason = event_avoid_reason(now=now, holidays=holidays)
        if reason:
            return False, reason
    # One open book at a time for v1 DTE-exit tracking.
    if isinstance(bot.get("open_position"), dict) and bot["open_position"].get("legs"):
        return False, "Open position — waiting for DTE exit."
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
    disarm_on_fail: bool | None = None,
    count_toward_daily: bool = True,
) -> dict[str, Any]:
    """Update run counters + ring log after an attempt.

    Entry auto-fails disarm by default. Exits should pass ``disarm_on_fail=False``
    and ``count_toward_daily=False`` so a flat attempt does not kill arming or
    burn the daily entry budget.
    """
    now = _now_ts()
    day = _ist_day_key()
    runs = int(bot.get("runs_today") or 0)
    if bot.get("runs_today_date") != day:
        runs = 0
    if ok and count_toward_daily:
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
    do_disarm = auto if disarm_on_fail is None else disarm_on_fail
    if not ok and do_disarm:
        bot["enabled"] = False
    return bot


def owned_by(row: dict[str, Any], owner_id: str | None) -> bool:
    """True when ``owner_id`` may see and act on this row.

    ``None`` is the operator scope: an admin sees the whole tenant, which is
    what the bot worker and the desk both need. A trader passes their user id
    and sees only their own rows.

    Rows written before ownership existed carry no ``owner_id``. Every one of
    them was created while these routes were admin-only, so they stay
    operator-visible rather than becoming shared with every end user.
    """
    if owner_id is None:
        return True
    return str(row.get("owner_id") or "") == owner_id


def visible_rows(
    rows: list[dict[str, Any]], owner_id: str | None
) -> list[dict[str, Any]]:
    return [row for row in rows if owned_by(row, owner_id)]


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


async def list_bots(tenant_id: str, *, owner_id: str | None = None) -> dict[str, Any]:
    items = visible_rows(await load_bots(tenant_id), owner_id)
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
                "avoid_events": bool(row.get("avoid_events")),
                "max_dte_hold": row.get("max_dte_hold"),
                "schedule": row.get("schedule"),
                "entry": row.get("entry"),
                "cooldown_sec": row.get("cooldown_sec"),
                "max_runs_per_day": row.get("max_runs_per_day"),
                "runs_today": row.get("runs_today"),
                "runs_today_date": row.get("runs_today_date"),
                "last_run_at": row.get("last_run_at"),
                "last_run_message": row.get("last_run_message"),
                "open_position": bool(
                    isinstance(row.get("open_position"), dict)
                    and (row.get("open_position") or {}).get("legs")
                ),
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


async def get_bot(
    tenant_id: str, bot_id: str, *, owner_id: str | None = None
) -> dict[str, Any]:
    for row in await load_bots(tenant_id):
        if row.get("id") == bot_id:
            # Someone else's bot reads as absent rather than forbidden, so the
            # response cannot be used to enumerate other traders' bot ids.
            if not owned_by(row, owner_id):
                break
            return {"ok": True, "bot": row}
    return {"ok": False, "error": "Bot not found."}


async def create_bot(
    tenant_id: str, payload: dict[str, Any], *, owner_id: str | None = None
) -> dict[str, Any]:
    items = await load_bots(tenant_id)
    if len(items) >= MAX_BOTS:
        return {"ok": False, "error": f"Maximum {MAX_BOTS} bots reached."}
    # Cap per trader as well as per tenant, so one user cannot fill the blob
    # and lock every other user out of creating a bot.
    mine = visible_rows(items, owner_id)
    if owner_id is not None and len(mine) >= MAX_BOTS_PER_OWNER:
        return {
            "ok": False,
            "error": f"Maximum {MAX_BOTS_PER_OWNER} bots reached for this user.",
        }
    bot = normalize_bot({**payload, "owner_id": owner_id})
    if not bot.get("template") and not bot.get("backtest_id") and not bot.get("legs"):
        return {"ok": False, "error": "template, backtest_id, or legs required."}
    # Allow legs from payload on create (backtest handoff).
    if isinstance(payload.get("legs"), list) and payload["legs"]:
        bot["legs"] = payload["legs"]
    items.insert(0, bot)
    await save_bots(tenant_id, items)
    return {"ok": True, "bot": bot}


async def update_bot(
    tenant_id: str,
    bot_id: str,
    payload: dict[str, Any],
    *,
    owner_id: str | None = None,
) -> dict[str, Any]:
    items = await load_bots(tenant_id)
    for idx, row in enumerate(items):
        if row.get("id") != bot_id:
            continue
        if not owned_by(row, owner_id):
            break
        next_bot = normalize_bot(payload, existing=row)
        if isinstance(payload.get("legs"), list):
            next_bot["legs"] = payload["legs"]
        if not next_bot.get("template") and not next_bot.get("backtest_id") and not next_bot.get("legs"):
            return {"ok": False, "error": "template, backtest_id, or legs required."}
        items[idx] = next_bot
        await save_bots(tenant_id, items)
        return {"ok": True, "bot": next_bot}
    return {"ok": False, "error": "Bot not found."}


async def delete_bot(
    tenant_id: str, bot_id: str, *, owner_id: str | None = None
) -> dict[str, Any]:
    items = await load_bots(tenant_id)
    next_items = [
        row
        for row in items
        if not (row.get("id") == bot_id and owned_by(row, owner_id))
    ]
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
