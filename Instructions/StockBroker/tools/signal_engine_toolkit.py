"""Signal engine toolkit (Atlas tenant_python starter).

Admin-only: bind on the **Signals ops** team (`signals-ops`). Computes entry
metrics (~8×/sec SSE on the admin desk; broker quotes ~2×/sec), evaluates BUY conditions,
and can fan-out in-app notifications to all users when entry passes.

Data sources (configure in tool settings):
- NIFTY / ATM / CE / PE — **Kite** toolkit (`get_ltp` / `get_quote`) bound on Signals ops
- OI / ADX / RSI — Kite full quote + `get_historical_candles`
- CrudeOil / VIX — Kite MCX/NSE symbols
- Global indices — backend Yahoo slow tier (~1 h); optional manual `dow_change_pct`

Extend metrics later via `metrics_json` in settings (array of metric defs).
"""

from __future__ import annotations

import json
import math
import time
from typing import Any

from supertools.common.base_tool import BaseToolkit

DEFAULT_METRICS: list[dict[str, Any]] = [
    {"id": "adx", "label": "ADX", "rule": "lt", "target": 25, "tier": "medium"},
    {"id": "oi", "label": "OI", "rule": "gt", "target": 50, "tier": "fast"},
    {"id": "iv", "label": "IV", "rule": "iv_pct_day_high", "target": 50, "tier": "fast"},
    {"id": "crude_oil", "label": "CrudeOil", "rule": "below_prev_close", "target": 0, "tier": "medium"},
    {"id": "dow_jones", "label": "DowJones", "rule": "abs_lte", "target": 0.5, "tier": "slow"},
    {"id": "atm", "label": "ATM", "rule": "info", "target": 0, "tier": "fast"},
    {"id": "ce", "label": "CE", "rule": "ce_pe_balance", "target": 0, "tier": "fast"},
    {"id": "pe", "label": "PE", "rule": "ce_pe_balance", "target": 0, "tier": "fast"},
    {"id": "pcr", "label": "PCR", "rule": "between", "target": 1.0, "target_high": 1.3, "tier": "fast"},
    {"id": "ivp", "label": "IVP", "rule": "lt", "target": 70, "tier": "medium"},
    {"id": "india_vix", "label": "India VIX", "rule": "lt", "target": 18, "tier": "medium"},
    {"id": "max_pain", "label": "Max Pain", "rule": "spot_below_max_pain", "target": 0, "tier": "medium"},
    {"id": "oi_pct_chg", "label": "OI % Chg", "rule": "gt", "target": 0, "tier": "fast"},
    {"id": "iv_chg", "label": "IV Chg", "rule": "lte", "target": 0, "tier": "fast"},
]

TIER_TTL_SEC = {"slow": 3600, "medium": 60, "fast": 0.334}
_cache: dict[str, tuple[float, Any]] = {}
_iv_day_high: float | None = None


def _ok(data: Any) -> dict[str, Any]:
    return {"ok": True, "data": data}


def _err(message: str) -> dict[str, Any]:
    return {"ok": False, "error": message}


def _settings(ctx) -> dict[str, Any]:
    s = ctx.settings if hasattr(ctx, "settings") else {}
    return s if isinstance(s, dict) else dict(s or {})


def _cache_get(key: str) -> Any | None:
    row = _cache.get(key)
    if not row:
        return None
    expires, value = row
    if time.monotonic() >= expires:
        return None
    return value


def _cache_set(key: str, tier: str, value: Any) -> None:
    _cache[key] = (time.monotonic() + TIER_TTL_SEC.get(tier, 1), value)


def _round_strike(ltp: float, step: int) -> int:
    step = max(step, 1)
    return int(round(ltp / step) * step)


def _mock_feed() -> dict[str, Any]:
    return {
        "nifty_ltp": 24312.5,
        "atm": 24300,
        "ce": 125.0,
        "pe": 55.0,
        "oi": 50.0,
        "adx": 45.0,
        "iv": -11.0,
        "iv_day_high": 22.0,
        "crude_ltp": 87.0,
        "crude_prev_close": 88.5,
        "dow_change_pct": -0.50,
        "pcr": 1.25,
        "ivp": 45.0,
        "india_vix": 14.2,
        "max_pain": 24400.0,
        "oi_pct_chg": 0.4,
        "iv_chg": -0.3,
        "source": "mock",
    }


def _evaluate_metrics(metrics: list[dict[str, Any]], feed: dict[str, Any]) -> dict[str, Any]:
    ce = feed.get("ce")
    pe = feed.get("pe")
    rows: list[dict[str, Any]] = []
    evaluable = passed = 0
    for spec in metrics:
        mid = spec["id"]
        rule = spec.get("rule", "info")
        target = float(spec.get("target") or 0)
        value = feed.get(
            {
                "adx": "adx",
                "oi": "oi",
                "iv": "iv",
                "iv_chg": "iv_chg",
                "crude_oil": "crude_ltp",
                "dow_jones": "dow_change_pct",
                "atm": "atm",
                "ce": "ce",
                "pe": "pe",
                "pcr": "pcr",
                "ivp": "ivp",
                "india_vix": "india_vix",
                "max_pain": "max_pain",
                "oi_pct_chg": "oi_pct_chg",
            }.get(mid, mid)
        )
        ok: bool | None = None if rule == "info" else None
        if rule == "lt" and value is not None:
            ok = float(value) < target
        elif rule == "gt" and value is not None:
            ok = float(value) > target
        elif rule == "lte" and value is not None:
            ok = float(value) <= target
        elif rule == "abs_lte" and value is not None:
            ok = abs(float(value)) <= target
        elif rule == "between" and value is not None:
            high = float(spec.get("target_high", target))
            ok = target <= float(value) <= high
        elif rule == "below_prev_close":
            ok = (
                feed.get("crude_ltp") is not None
                and feed.get("crude_prev_close") is not None
                and float(feed["crude_ltp"]) < float(feed["crude_prev_close"])
            )
        elif rule == "ce_pe_balance" and ce is not None and pe is not None:
            ok = math.isclose(float(ce), float(pe), abs_tol=0.5)
        elif rule == "iv_pct_day_high" and feed.get("iv") is not None and feed.get("iv_day_high"):
            ok = (float(feed["iv"]) / float(feed["iv_day_high"])) * 100 <= target
        elif rule == "spot_below_max_pain":
            spot = feed.get("nifty_ltp") or feed.get("atm")
            mp = feed.get("max_pain")
            ok = spot is not None and mp is not None and float(spot) < float(mp)
        if ok is not None:
            evaluable += 1
            if ok:
                passed += 1
        rows.append({"id": mid, "label": spec.get("label", mid), "value": value, "passed": ok, "tier": spec.get("tier")})
    entry_ready = evaluable > 0 and passed == evaluable
    return {"metrics": rows, "entry_ready": entry_ready, "passed": passed, "evaluable": evaluable}


class SignalEngineToolkit(BaseToolkit):
    """Admin signal metrics + entry evaluation."""

    def __init__(self, name: str | None = None, tools: list[Any] | None = None, **kwargs: Any) -> None:
        super().__init__(name=name or "signal_engine", tools=tools, **kwargs)

    async def get_metric_config(self) -> dict[str, Any]:
        """Return active metric definitions (extend via metrics_json in settings)."""
        s = _settings(self)
        metrics = list(DEFAULT_METRICS)
        raw = s.get("metrics_json")
        if raw:
            try:
                parsed = json.loads(raw) if isinstance(raw, str) else raw
                if isinstance(parsed, list) and parsed:
                    metrics = parsed
            except (TypeError, json.JSONDecodeError):
                pass
        return _ok({"metrics": metrics, "poll_ms": int(TIER_TTL_SEC["fast"] * 1000)})

    async def get_signal_state(
        self,
        nifty_ltp: float = 0,
        ce_premium: float = 0,
        pe_premium: float = 0,
        oi: float = 0,
        adx: float = 0,
        iv: float = 0,
        crude_ltp: float = 0,
        crude_prev_close: float = 0,
    ) -> dict[str, Any]:
        """Evaluate all metrics. In mock mode uses demo feed; else pass live prints."""
        s = _settings(self)
        metrics_cfg = (await self.get_metric_config())["data"]["metrics"]
        if s.get("mock", False):
            feed = _mock_feed()
        else:
            global _iv_day_high
            ltp = nifty_ltp or float(s.get("nifty_ltp") or 0)
            atm = _round_strike(ltp, int(s.get("strike_step") or 50)) if ltp else None
            iv_val = iv or None
            if iv_val is not None:
                _iv_day_high = max(_iv_day_high or float(iv_val), float(iv_val))
            dow = _cache_get("dow_jones")
            if dow is None and s.get("dow_change_pct") is not None:
                dow = float(s["dow_change_pct"])
                _cache_set("dow_jones", "slow", dow)
            feed = {
                "nifty_ltp": ltp or None,
                "atm": atm,
                "ce": ce_premium or None,
                "pe": pe_premium or None,
                "oi": oi or None,
                "adx": adx or None,
                "iv": iv_val,
                "iv_day_high": _iv_day_high,
                "crude_ltp": crude_ltp or None,
                "crude_prev_close": crude_prev_close or None,
                "dow_change_pct": dow,
                "source": "live_inputs",
            }
        evaluated = _evaluate_metrics(metrics_cfg, feed)
        exit_pct = float(s.get("exit_pct") or 5)
        ce_entry = float(s.get("entry_ce_premium") or 100)
        pe_entry = float(s.get("entry_pe_premium") or 100)
        entry = None
        if evaluated["entry_ready"] and feed.get("atm") is not None:
            atm = int(feed["atm"])
            entry = {
                "side": "BUY",
                "atm": atm,
                "ce": ce_entry,
                "pe": pe_entry,
                "exit_pct": exit_pct,
                "label": f"BUY= {atm}, CE={ce_entry:g}, PE={pe_entry:g}, EXIT +{exit_pct:g}%",
            }
        return _ok({**evaluated, "entry": entry, "feed": feed, "mock": bool(s.get("mock", False))})

    async def publish_entry_signal(self, title: str = "New trading signal") -> dict[str, Any]:
        """When entry passes, POST to platform API and notify all users (mutating)."""
        state = await self.get_signal_state()
        if not state.get("ok"):
            return state
        data = state["data"]
        if not data.get("entry_ready") or not data.get("entry"):
            return _err("Entry conditions not met")
        entry = data["entry"]
        cfg = _settings(self)
        base = str(cfg.get("base_url") or "").rstrip("/")
        http = self.http if hasattr(self, "http") else None
        platform = None
        if base and http and not cfg.get("mock", False):
            platform = await http.post(
                f"{base}/v1/ops/signals/publish",
                json={"entry": entry, "title": title},
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            )
        notify = None
        notifications_url = str(cfg.get("notifications_path") or "/admin/notifications")
        if http and not cfg.get("mock", False):
            notify = await http.post(
                notifications_url,
                json={"title": title, "body": entry["label"], "user_id": None},
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            )
        return _ok({"entry": entry, "platform": platform, "notification": notify, "mock": bool(cfg.get("mock", False))})
