"""TradeForge Academy toolkit (Atlas tenant_python starter).

Contracts follow PRD canonical APIs. Wire `base_url` to the TradeForge API host
(must be allowlisted). Bind a service credential (Bearer) via tool settings.

Customer live demat broker: Groww.
Mutating methods should be marked mutating in Atlas (HITL).

Until the TradeForge API is live, methods return structured stubs when
`settings.mock=true` (default) so agents can be rehearsed safely.
"""

from __future__ import annotations

from typing import Any

from supertools.common.base_tool import BaseToolkit

_JSON_HEADERS = {"Content-Type": "application/json", "Accept": "application/json"}


def _cfg(self) -> dict[str, Any]:
    s = getattr(self, "settings", {}) or {}
    return {
        "base_url": str(s.get("base_url", "")).rstrip("/"),
        "timeout": int(s.get("timeout") or 60),
        "mock": bool(s.get("mock", True)),
        "default_broker": str(s.get("default_broker") or "groww"),
    }


def _ok(data: Any) -> dict[str, Any]:
    return {"ok": True, "data": data}


def _err(message: str) -> dict[str, Any]:
    return {"ok": False, "error": message}


class TradeForgeToolkit(BaseToolkit):
    """Ops + customer assistance tools for TradeForge."""

    def __init__(self, name: str | None = None, tools: list[Any] | None = None, **kwargs: Any) -> None:
        super().__init__(name=name or "tradeforge", tools=tools, **kwargs)

    async def _get(self, path: str) -> Any:
        cfg = _cfg(self)
        if cfg["mock"] or not cfg["base_url"]:
            return None
        url = f"{cfg['base_url']}{path}"
        # ctx.http is injected at runtime in Atlas sandbox; when running as
        # BaseToolkit-only rehearsal, mock branch is used instead.
        http = getattr(self, "http", None)
        if http is None:
            return None
        return await http.get(url, headers=_JSON_HEADERS)

    async def _post(self, path: str, payload: dict[str, Any]) -> Any:
        cfg = _cfg(self)
        if cfg["mock"] or not cfg["base_url"]:
            return None
        url = f"{cfg['base_url']}{path}"
        http = getattr(self, "http", None)
        if http is None:
            return None
        return await http.post(url, json=payload, headers=_JSON_HEADERS)

    # --- Signals (customer + ops) ---

    async def list_signals(self, segment: str = "", limit: int = 20) -> dict[str, Any]:
        """List published customer-visible signals (hides suppressed)."""
        live = await self._get(f"/v1/signals?segment={segment}&limit={limit}")
        if live is not None:
            return _ok(live)
        return _ok(
            {
                "items": [
                    {
                        "id": "sig_demo_nifty_ce",
                        "symbol": "NIFTY",
                        "segment": segment or "F&O",
                        "side": "BUY",
                        "entry": 100,
                        "sl": 90,
                        "targets": [110, 120],
                        "pack_id": "EX-SMA-X",
                        "suppressed": False,
                    }
                ]
            }
        )

    async def get_signal(self, signal_id: str) -> dict[str, Any]:
        """Get signal detail including customer-visible param subset."""
        live = await self._get(f"/v1/signals/{signal_id}")
        if live is not None:
            return _ok(live)
        return _ok(
            {
                "id": signal_id,
                "symbol": "NIFTY",
                "side": "BUY",
                "entry": 100,
                "sl": 90,
                "targets": [110, 120],
                "rationale": "Demo stub — replace with API",
                "pack_id": "EX-SMA-X",
                "suppressed": False,
            }
        )

    async def list_draft_signals(self, segment: str = "") -> dict[str, Any]:
        """Ops: list draft signals awaiting publish."""
        live = await self._get(f"/v1/ops/signals?status=draft&segment={segment}")
        if live is not None:
            return _ok(live)
        return _ok({"items": [{"id": "draft_001", "segment": segment or "EQ", "status": "draft"}]})

    async def publish_signal(self, signal_id: str, notes: str = "") -> dict[str, Any]:
        """Ops: publish a draft signal to the customer feed (mutating)."""
        live = await self._post(f"/v1/ops/signals/{signal_id}/publish", {"notes": notes})
        if live is not None:
            return _ok(live)
        return _ok({"id": signal_id, "status": "published", "notes": notes, "mock": True})

    async def suppress_signal(self, signal_id: str, reason: str) -> dict[str, Any]:
        """Ops: suppress a live signal so customers no longer see it (mutating)."""
        live = await self._post(
            f"/v1/ops/signals/{signal_id}/suppress", {"reason": reason}
        )
        if live is not None:
            return _ok(live)
        return _ok({"id": signal_id, "status": "suppressed", "reason": reason, "mock": True})

    async def preview_push(self, signal_id: str, segment: str = "") -> dict[str, Any]:
        """Ops: preview push payload for a signal."""
        live = await self._post(
            f"/v1/ops/signals/{signal_id}/push-preview", {"segment": segment}
        )
        if live is not None:
            return _ok(live)
        return _ok(
            {
                "signal_id": signal_id,
                "title": "New signal",
                "body": "Demo push preview",
                "segment": segment or "EQ",
            }
        )

    # --- Params ---

    async def get_param_schema(self, segment: str = "stock") -> dict[str, Any]:
        """Get published (+ draft if any) param schema for stock or crypto."""
        live = await self._get(f"/v1/ops/params/schema?segment={segment}")
        if live is not None:
            return _ok(live)
        return _ok(
            {
                "segment": segment,
                "version": 1,
                "key_count": 115,
                "keys_sample": ["sma_fast", "sma_slow", "rsi_period"],
            }
        )

    async def update_param_draft(self, segment: str, patch_json: str) -> dict[str, Any]:
        """Save a param draft patch (mutating). patch_json is a JSON object string."""
        live = await self._post(
            f"/v1/ops/params/draft", {"segment": segment, "patch": patch_json}
        )
        if live is not None:
            return _ok(live)
        return _ok({"segment": segment, "status": "draft_saved", "mock": True})

    async def diff_param_versions(self, segment: str = "stock") -> dict[str, Any]:
        """Diff draft vs published param schema."""
        live = await self._get(f"/v1/ops/params/diff?segment={segment}")
        if live is not None:
            return _ok(live)
        return _ok({"segment": segment, "changed": [{"key": "sma_fast", "from": 9, "to": 12}]})

    # --- Feed ---

    async def get_feed_health(self) -> dict[str, Any]:
        """Feed lag, last tick, error rate, stale count."""
        live = await self._get("/v1/ops/feed/health")
        if live is not None:
            return _ok(live)
        return _ok(
            {
                "status": "OK",
                "lag_ms": 120,
                "last_tick": "2026-08-10T10:00:00Z",
                "error_rate": 0.0,
                "stale_signal_count": 0,
            }
        )

    # --- Live / compliance ---

    async def list_live_requests(self, status: str = "pending") -> dict[str, Any]:
        """Compliance: live approval queue."""
        live = await self._get(f"/v1/ops/live-requests?status={status}")
        if live is not None:
            return _ok(live)
        return _ok(
            {
                "items": [
                    {
                        "id": "lr_001",
                        "user_id": "u_demo",
                        "status": status,
                        "broker": "groww",
                        "disclosure_accepted_at": "2026-08-01T09:00:00Z",
                    }
                ]
            }
        )

    async def approve_live_request(self, request_id: str, notes: str = "") -> dict[str, Any]:
        """Approve live trading request (mutating). TTL 365 days."""
        live = await self._post(
            f"/v1/ops/live-requests/{request_id}/approve", {"notes": notes}
        )
        if live is not None:
            return _ok(live)
        return _ok(
            {
                "id": request_id,
                "status": "approved",
                "ttl_days": 365,
                "notes": notes,
                "mock": True,
            }
        )

    async def deny_live_request(self, request_id: str, notes: str) -> dict[str, Any]:
        """Deny live trading request (mutating)."""
        live = await self._post(
            f"/v1/ops/live-requests/{request_id}/deny", {"notes": notes}
        )
        if live is not None:
            return _ok(live)
        return _ok({"id": request_id, "status": "denied", "notes": notes, "mock": True})

    async def get_algo_status(self, user_id: str) -> dict[str, Any]:
        """Algo arm state, packs, kill flags for a user."""
        live = await self._get(f"/v1/algo/status?user_id={user_id}")
        if live is not None:
            return _ok(live)
        return _ok(
            {
                "user_id": user_id,
                "armed": False,
                "live_status": "approved",
                "broker": "groww",
                "pack_ids": ["EX-SMA-X"],
                "mode": "Paper",
            }
        )

    async def arm_algo(self, user_id: str, mode: str = "Paper") -> dict[str, Any]:
        """Arm algo when approved + Groww token valid + caps + packs (mutating)."""
        live = await self._post(
            "/v1/algo/arm", {"user_id": user_id, "mode": mode, "broker": "groww"}
        )
        if live is not None:
            return _ok(live)
        return _ok({"user_id": user_id, "armed": True, "mode": mode, "broker": "groww", "mock": True})

    async def disarm_algo(self, user_id: str, reason: str = "") -> dict[str, Any]:
        """Disarm algo for a user (mutating)."""
        live = await self._post("/v1/algo/disarm", {"user_id": user_id, "reason": reason})
        if live is not None:
            return _ok(live)
        return _ok({"user_id": user_id, "armed": False, "reason": reason, "mock": True})

    async def kill_switch(
        self, scope: str = "user", user_id: str = "", cancel_open: bool = False
    ) -> dict[str, Any]:
        """Global or per-user kill switch (mutating). Compliance/Admin only."""
        live = await self._post(
            "/v1/ops/kill-switch",
            {"scope": scope, "user_id": user_id, "cancel_open": cancel_open},
        )
        if live is not None:
            return _ok(live)
        return _ok(
            {
                "scope": scope,
                "user_id": user_id or None,
                "cancel_open": cancel_open,
                "status": "disarmed",
                "mock": True,
            }
        )

    # --- Paper / account ---

    async def get_paper_hub(self, user_id: str) -> dict[str, Any]:
        """Paper capital, today P&L, open position count."""
        live = await self._get(f"/v1/paper/hub?user_id={user_id}")
        if live is not None:
            return _ok(live)
        return _ok(
            {
                "user_id": user_id,
                "virtual_capital": 10_000_000,
                "today_pnl": 0,
                "open_positions": 0,
            }
        )

    async def place_paper_order(
        self,
        user_id: str,
        symbol: str,
        side: str,
        qty: int,
        order_type: str = "MARKET",
        signal_id: str = "",
        idempotency_key: str = "",
    ) -> dict[str, Any]:
        """Place paper order (mutating). Always send idempotency_key."""
        if not idempotency_key:
            return _err("idempotency_key is required")
        payload = {
            "user_id": user_id,
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "order_type": order_type,
            "signal_id": signal_id,
            "idempotency_key": idempotency_key,
        }
        live = await self._post("/v1/paper/orders", payload)
        if live is not None:
            return _ok(live)
        return _ok({**payload, "status": "filled", "mock": True})

    async def list_positions(self, user_id: str, mode: str = "paper") -> dict[str, Any]:
        """List open/closed positions for paper or live."""
        live = await self._get(f"/v1/{mode}/positions?user_id={user_id}")
        if live is not None:
            return _ok(live)
        return _ok({"user_id": user_id, "mode": mode, "items": []})

    async def list_strategy_packs(self) -> dict[str, Any]:
        """List deployable strategy packs (EX-* + published)."""
        live = await self._get("/v1/algo/packs")
        if live is not None:
            return _ok(live)
        return _ok(
            {
                "items": [
                    {"id": "EX-SMA-X", "name": "SMA crossover"},
                    {"id": "EX-RSI-MR", "name": "RSI mean reversion"},
                    {"id": "EX-MACD-M", "name": "MACD momentum"},
                    {"id": "EX-VWAP-B", "name": "VWAP band"},
                ]
            }
        )

    async def get_account_health(self, user_id: str) -> dict[str, Any]:
        """Demat/account health — customer broker is Groww."""
        broker = _cfg(self)["default_broker"]
        live = await self._get(f"/v1/demat/health?user_id={user_id}&broker={broker}")
        if live is not None:
            return _ok(live)
        return _ok(
            {
                "user_id": user_id,
                "broker": "groww",
                "linked": True,
                "margin_available": 0,
                "token_ttl_seconds": 3600,
                "last_sync": "2026-08-10T10:00:00Z",
            }
        )
