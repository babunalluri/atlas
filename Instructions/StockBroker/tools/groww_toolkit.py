"""Groww Trading API toolkit (Atlas tenant_python starter).

Customer demat / live broker for Stock Broker.

Docs: https://groww.in/trade-api/docs/curl
Host (allowlist): api.groww.in

Auth (pick one):
1. Direct access token — settings/credential `access_token`
   (Groww UI → Trading APIs → Access Token; expires daily ~06:00 IST).
2. API key + secret — settings `api_key` + `api_secret`, then call
   `create_access_token` (checksum approval flow) before trading calls.
3. API key + TOTP — call `create_access_token_totp(totp=...)`.

Mutating: place_order, modify_order, cancel_order → Atlas HITL.

Supports equity (CASH) and F&O (FNO). MCX/commodity not supported by Groww API yet.
"""

from __future__ import annotations

import hashlib
import time
from typing import Any
from urllib.parse import quote, urlencode

_API_VERSION = "1.0"
_DEFAULT_BASE = "https://api.groww.in"


def _settings(ctx) -> dict[str, Any]:
    s = ctx.settings if hasattr(ctx, "settings") else {}
    return s if isinstance(s, dict) else dict(s or {})


def _base_url(ctx) -> str:
    return str(_settings(ctx).get("base_url") or _DEFAULT_BASE).rstrip("/")


def _access_token(ctx) -> str:
    s = _settings(ctx)
    return str(
        s.get("access_token")
        or s.get("token")
        or s.get("credential_value")
        or ""
    ).strip()


def _auth_headers(ctx, *, use_api_key: bool = False) -> dict[str, str]:
    s = _settings(ctx)
    if use_api_key:
        key = str(s.get("api_key") or "").strip()
        if not key:
            raise RuntimeError("api_key is required in tool settings/credential")
        token = key
    else:
        token = _access_token(ctx)
        if not token:
            raise RuntimeError(
                "access_token is required. Set it in credential/settings, or call "
                "create_access_token / create_access_token_totp first and store the token."
            )
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-API-VERSION": str(s.get("api_version") or _API_VERSION),
    }


def _checksum(secret: str, timestamp: str) -> str:
    return hashlib.sha256(f"{secret}{timestamp}".encode("utf-8")).hexdigest()


def _unwrap(body: Any, label: str) -> Any:
    if isinstance(body, str) and body.startswith("Error"):
        raise RuntimeError(body)
    if not isinstance(body, dict):
        return body
    status = str(body.get("status") or "").upper()
    if status == "FAILURE" or (status and status not in {"SUCCESS", "OK", ""}):
        err = body.get("error") or body
        raise RuntimeError(f"{label} failed: {err}")
    if "payload" in body:
        return {"ok": True, "status": body.get("status", "SUCCESS"), "data": body["payload"]}
    return {"ok": True, "data": body}


async def _get(ctx, path: str, label: str, query: dict[str, Any] | None = None) -> Any:
    url = f"{_base_url(ctx)}{path}"
    if query:
        cleaned = {k: v for k, v in query.items() if v is not None and v != ""}
        if cleaned:
            url = f"{url}?{urlencode(cleaned)}"
    body = await ctx.http.get(url, headers=_auth_headers(ctx))
    return _unwrap(body, label)


async def _post(ctx, path: str, payload: dict[str, Any], label: str, *, use_api_key: bool = False) -> Any:
    url = f"{_base_url(ctx)}{path}"
    body = await ctx.http.post(
        url, json=payload, headers=_auth_headers(ctx, use_api_key=use_api_key)
    )
    return _unwrap(body, label)


# --- Auth ---


async def create_access_token(ctx) -> Any:
    """Exchange api_key + api_secret for a daily access token (approval checksum flow)."""
    s = _settings(ctx)
    secret = str(s.get("api_secret") or "").strip()
    if not secret:
        raise RuntimeError("api_secret is required in tool settings/credential")
    timestamp = str(int(time.time()))
    payload = {
        "key_type": "approval",
        "checksum": _checksum(secret, timestamp),
        "timestamp": timestamp,
    }
    return await _post(ctx, "/v1/token/api/access", payload, "create_access_token", use_api_key=True)


async def create_access_token_totp(ctx, totp: str) -> Any:
    """Exchange api_key + TOTP code for a daily access token."""
    if not totp or not str(totp).strip():
        raise RuntimeError("totp code is required")
    payload = {"key_type": "totp", "totp": str(totp).strip()}
    return await _post(
        ctx, "/v1/token/api/access", payload, "create_access_token_totp", use_api_key=True
    )


# --- Portfolio / account health ---


async def get_holdings(ctx) -> Any:
    """List demat equity holdings for the linked Groww user."""
    return await _get(ctx, "/v1/holdings/user", "get_holdings")


async def get_positions(ctx, segment: str = "") -> Any:
    """List positions. Optional segment: CASH | FNO."""
    query = {"segment": segment.upper()} if segment else None
    return await _get(ctx, "/v1/positions/user", "get_positions", query=query)


async def get_position_for_symbol(ctx, trading_symbol: str, segment: str = "CASH") -> Any:
    """Position for one trading symbol."""
    return await _get(
        ctx,
        "/v1/positions/trading-symbol",
        "get_position_for_symbol",
        query={"trading_symbol": trading_symbol, "segment": segment.upper()},
    )


async def get_user_margin(ctx) -> Any:
    """Available margin / funds for the Groww account."""
    return await _get(ctx, "/v1/margins/detail/user", "get_user_margin")


async def get_required_margin(
    ctx,
    trading_symbol: str,
    quantity: int,
    exchange: str,
    segment: str,
    product: str,
    order_type: str,
    transaction_type: str,
    price: float = 0,
) -> Any:
    """Calculate margin required for an order (or basket item)."""
    payload: dict[str, Any] = {
        "trading_symbol": trading_symbol,
        "quantity": int(quantity),
        "exchange": exchange.upper(),
        "segment": segment.upper(),
        "product": product.upper(),
        "order_type": order_type.upper(),
        "transaction_type": transaction_type.upper(),
    }
    if price is not None and float(price) > 0:
        payload["price"] = float(price)
    return await _post(ctx, "/v1/margins/detail/orders", payload, "get_required_margin")


# --- Orders ---


async def place_order(
    ctx,
    trading_symbol: str,
    quantity: int,
    transaction_type: str,
    order_type: str = "MARKET",
    exchange: str = "NSE",
    segment: str = "CASH",
    product: str = "CNC",
    validity: str = "DAY",
    price: float = 0,
    trigger_price: float = 0,
    order_reference_id: str = "",
) -> Any:
    """Place a Groww order (mutating). Use order_reference_id for idempotency when provided."""
    payload: dict[str, Any] = {
        "trading_symbol": trading_symbol,
        "quantity": int(quantity),
        "transaction_type": transaction_type.upper(),
        "order_type": order_type.upper(),
        "exchange": exchange.upper(),
        "segment": segment.upper(),
        "product": product.upper(),
        "validity": validity.upper(),
        "price": float(price or 0),
    }
    if trigger_price:
        payload["trigger_price"] = float(trigger_price)
    if order_reference_id:
        payload["order_reference_id"] = order_reference_id
    return await _post(ctx, "/v1/order/create", payload, "place_order")


async def modify_order(
    ctx,
    groww_order_id: str,
    segment: str,
    quantity: int = 0,
    order_type: str = "",
    price: float = 0,
    trigger_price: float = 0,
) -> Any:
    """Modify an open Groww order (mutating)."""
    payload: dict[str, Any] = {
        "groww_order_id": groww_order_id,
        "segment": segment.upper(),
    }
    if quantity:
        payload["quantity"] = int(quantity)
    if order_type:
        payload["order_type"] = order_type.upper()
    if price:
        payload["price"] = float(price)
    if trigger_price:
        payload["trigger_price"] = float(trigger_price)
    return await _post(ctx, "/v1/order/modify", payload, "modify_order")


async def cancel_order(ctx, groww_order_id: str, segment: str) -> Any:
    """Cancel an open/pending Groww order (mutating)."""
    payload = {"groww_order_id": groww_order_id, "segment": segment.upper()}
    return await _post(ctx, "/v1/order/cancel", payload, "cancel_order")


async def get_order_detail(ctx, groww_order_id: str, segment: str = "CASH") -> Any:
    """Fetch one order by Groww order id."""
    oid = quote(str(groww_order_id), safe="")
    return await _get(
        ctx,
        f"/v1/order/detail/{oid}",
        "get_order_detail",
        query={"segment": segment.upper()},
    )


async def get_order_status(ctx, groww_order_id: str, segment: str = "CASH") -> Any:
    """Order status by Groww order id."""
    oid = quote(str(groww_order_id), safe="")
    return await _get(
        ctx,
        f"/v1/order/status/{oid}",
        "get_order_status",
        query={"segment": segment.upper()},
    )


async def get_order_status_by_reference(ctx, order_reference_id: str, segment: str = "CASH") -> Any:
    """Order status by client order_reference_id (idempotency lookup)."""
    rid = quote(str(order_reference_id), safe="")
    return await _get(
        ctx,
        f"/v1/order/status/reference/{rid}",
        "get_order_status_by_reference",
        query={"segment": segment.upper()},
    )


async def list_orders(ctx, segment: str = "", page: int = 0, page_size: int = 50) -> Any:
    """List today's orders (max page_size 100)."""
    size = max(1, min(int(page_size or 50), 100))
    query: dict[str, Any] = {"page": int(page or 0), "page_size": size}
    if segment:
        query["segment"] = segment.upper()
    return await _get(ctx, "/v1/order/list", "list_orders", query=query)


async def get_order_trades(ctx, groww_order_id: str, segment: str = "CASH") -> Any:
    """Trades / fills for an order."""
    oid = quote(str(groww_order_id), safe="")
    return await _get(
        ctx,
        f"/v1/order/trades/{oid}",
        "get_order_trades",
        query={"segment": segment.upper()},
    )


# --- Live data (read) ---


def _split_trading_symbols(*parts: str) -> list[str]:
    symbols: list[str] = []
    for part in parts:
        for piece in str(part or "").split(","):
            text = piece.strip()
            if text:
                symbols.append(text)
    return symbols


async def get_quote(
    ctx,
    exchange: str = "NSE",
    segment: str = "CASH",
    trading_symbols: str = "",
    trading_symbol: str = "",
) -> Any:
    """Market quote. Groww accepts one trading_symbol per request; comma-separated batching is handled here."""
    symbols = _split_trading_symbols(trading_symbols, trading_symbol)
    if not symbols:
        raise RuntimeError("trading_symbol or trading_symbols is required")
    merged: dict[str, Any] = {}
    for sym in symbols:
        result = await _get(
            ctx,
            "/v1/live-data/quote",
            "get_quote",
            query={
                "exchange": exchange.upper(),
                "segment": segment.upper(),
                "trading_symbol": sym,
            },
        )
        payload = result.get("data") if isinstance(result, dict) else result
        if not isinstance(payload, dict):
            continue
        merged[sym] = payload
        ts = payload.get("trading_symbol")
        if ts and str(ts) not in merged:
            merged[str(ts)] = payload
    if len(symbols) == 1 and merged:
        return {"ok": True, "data": merged[symbols[0]]}
    return {"ok": True, "data": merged}


async def get_ltp(
    ctx,
    exchange: str = "NSE",
    segment: str = "CASH",
    trading_symbols: str = "",
    exchange_symbols: str = "",
) -> Any:
    """Last traded price. Prefer exchange_symbols (NSE_RELIANCE); or trading_symbols + exchange."""
    if exchange_symbols:
        symbols_param = exchange_symbols
    else:
        syms = _split_trading_symbols(trading_symbols)
        if not syms:
            raise RuntimeError("exchange_symbols or trading_symbols is required")
        symbols_param = ",".join(f"{exchange.upper()}_{sym}" for sym in syms)
    return await _get(
        ctx,
        "/v1/live-data/ltp",
        "get_ltp",
        query={
            "segment": segment.upper(),
            "exchange_symbols": symbols_param,
        },
    )


async def get_ohlc(
    ctx,
    exchange: str = "NSE",
    segment: str = "CASH",
    trading_symbols: str = "",
    exchange_symbols: str = "",
) -> Any:
    """OHLC snapshot. Prefer exchange_symbols (NSE_RELIANCE); or trading_symbols + exchange."""
    if exchange_symbols:
        symbols_param = exchange_symbols
    else:
        syms = _split_trading_symbols(trading_symbols)
        if not syms:
            raise RuntimeError("exchange_symbols or trading_symbols is required")
        symbols_param = ",".join(f"{exchange.upper()}_{sym}" for sym in syms)
    return await _get(
        ctx,
        "/v1/live-data/ohlc",
        "get_ohlc",
        query={
            "segment": segment.upper(),
            "exchange_symbols": symbols_param,
        },
    )


async def get_account_health(ctx) -> Any:
    """Convenience: margin + whether access token appears configured (never returns the token)."""
    s = _settings(ctx)
    has_token = bool(_access_token(ctx))
    has_key = bool(str(s.get("api_key") or "").strip())
    margin = None
    margin_error = None
    if has_token:
        try:
            margin = await get_user_margin(ctx)
        except Exception as exc:  # noqa: BLE001 - surface soft health
            margin_error = str(exc)
    return {
        "ok": True,
        "data": {
            "broker": "groww",
            "access_token_configured": has_token,
            "api_key_configured": has_key,
            "base_url": _base_url(ctx),
            "margin": margin,
            "margin_error": margin_error,
            "note": "Token values are never returned. Tokens typically expire daily ~06:00 IST.",
        },
    }
