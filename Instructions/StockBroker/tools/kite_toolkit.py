"""Zerodha Kite Connect toolkit (Atlas tenant_python starter).

Docs: https://kite.trade/docs/connect/v3/
Host (allowlist): api.kite.trade

Auth:
1. Complete Kite login → request_token, then call `create_session(request_token=...)`
   (needs api_key + api_secret in settings). Store returned access_token.
2. Or set api_key + access_token directly (daily session; expires ~06:00 IST).

Authorization header: `token {api_key}:{access_token}`
Kite Version header: X-Kite-Version: 3

Mutating (HITL): place_order, modify_order, cancel_order, convert_position, invalidate_session.

Coverage: equity (NSE/BSE), F&O (NFO/BFO), currency (CDS), commodity (MCX) —
whatever exchanges are enabled on the Kite user profile.
"""

from __future__ import annotations

import hashlib
from typing import Any
from urllib.parse import quote, urlencode

_DEFAULT_BASE = "https://api.kite.trade"
_LOGIN_BASE = "https://kite.zerodha.com/connect/login"
_KITE_VERSION = "3"


def _settings(ctx) -> dict[str, Any]:
    s = ctx.settings if hasattr(ctx, "settings") else {}
    return s if isinstance(s, dict) else dict(s or {})


def _base_url(ctx) -> str:
    return str(_settings(ctx).get("base_url") or _DEFAULT_BASE).rstrip("/")


def _api_key(ctx) -> str:
    key = str(_settings(ctx).get("api_key") or "").strip()
    if not key:
        raise RuntimeError("api_key is required in tool settings/credential")
    return key


def _access_token(ctx) -> str:
    s = _settings(ctx)
    token = str(s.get("access_token") or s.get("token") or "").strip()
    if not token:
        raise RuntimeError(
            "access_token is required. Run create_session after Kite login, "
            "or set access_token in credential/settings."
        )
    return token


def _auth_headers(ctx) -> dict[str, str]:
    return {
        "Authorization": f"token {_api_key(ctx)}:{_access_token(ctx)}",
        "X-Kite-Version": str(_settings(ctx).get("kite_version") or _KITE_VERSION),
    }


def _checksum(api_key: str, request_token: str, api_secret: str) -> str:
    raw = f"{api_key}{request_token}{api_secret}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _unwrap(body: Any, label: str) -> Any:
    if isinstance(body, str) and body.startswith("Error"):
        raise RuntimeError(body)
    if not isinstance(body, dict):
        return {"ok": True, "data": body}
    status = str(body.get("status") or "").lower()
    if status == "error":
        raise RuntimeError(
            f"{label} failed: {body.get('message') or body.get('error_type') or body}"
        )
    if "data" in body:
        return {"ok": True, "status": body.get("status", "success"), "data": body["data"]}
    return {"ok": True, "data": body}


def _drop_empty(payload: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in payload.items():
        if value is None or value == "":
            continue
        out[key] = value
    return out


async def _get(ctx, path: str, label: str, query: dict[str, Any] | None = None) -> Any:
    url = f"{_base_url(ctx)}{path}"
    if query:
        cleaned = {k: v for k, v in query.items() if v is not None and v != ""}
        if cleaned:
            url = f"{url}?{urlencode(cleaned, doseq=True)}"
    body = await ctx.http.get(url, headers=_auth_headers(ctx))
    return _unwrap(body, label)


async def _form(
    ctx,
    method: str,
    path: str,
    payload: dict[str, Any],
    label: str,
    *,
    auth: bool = True,
) -> Any:
    url = f"{_base_url(ctx)}{path}"
    headers = {
        "X-Kite-Version": str(_settings(ctx).get("kite_version") or _KITE_VERSION),
        "Content-Type": "application/x-www-form-urlencoded",
    }
    if auth:
        headers.update(_auth_headers(ctx))
    form = _drop_empty(payload)
    if method.upper() == "POST":
        body = await ctx.http.post(url, form=form, headers=headers)
    elif method.upper() == "PUT":
        body = await ctx.http.put(url, form=form, headers=headers)
    elif method.upper() == "DELETE":
        # Kite cancel uses DELETE with form/query; proxy form via PUT-style not available —
        # send fields as query string on DELETE (Kite accepts query for cancel).
        if form:
            url = f"{url}?{urlencode({k: str(v) for k, v in form.items()})}"
        body = await ctx.http.delete(url, headers=_auth_headers(ctx) if auth else headers)
    else:
        raise RuntimeError(f"Unsupported method {method}")
    return _unwrap(body, label)


async def get_instruments(ctx, exchange: str = "NFO") -> Any:
    """GET /instruments or /instruments/{exchange} — CSV dump (string in data.csv)."""
    exch = str(exchange or "").strip().upper()
    path = f"/instruments/{exch}" if exch else "/instruments"
    url = f"{_base_url(ctx)}{path}"
    body = await ctx.http.get(url, headers=_auth_headers(ctx))
    if isinstance(body, (bytes, bytearray)):
        text = body.decode("utf-8", errors="replace")
    elif isinstance(body, str):
        text = body
    elif isinstance(body, dict):
        # Some proxies wrap CSV; prefer explicit fields, else stringify.
        text = str(body.get("data") or body.get("csv") or body)
    else:
        text = str(body)
    return {"ok": True, "data": {"exchange": exch or "ALL", "csv": text, "bytes": len(text)}}


# --- Auth / session ---


async def get_login_url(ctx) -> Any:
    """Return the Kite Connect login URL for the configured api_key (no HTTP call)."""
    key = _api_key(ctx)
    url = f"{_LOGIN_BASE}?v=3&api_key={quote(key)}"
    return {"ok": True, "data": {"login_url": url, "api_key": key}}


async def create_session(ctx, request_token: str) -> Any:
    """Exchange request_token for access_token (form POST /session/token)."""
    if not request_token or not str(request_token).strip():
        raise RuntimeError("request_token is required")
    s = _settings(ctx)
    api_key = _api_key(ctx)
    api_secret = str(s.get("api_secret") or "").strip()
    if not api_secret:
        raise RuntimeError("api_secret is required for create_session")
    token = str(request_token).strip()
    payload = {
        "api_key": api_key,
        "request_token": token,
        "checksum": _checksum(api_key, token, api_secret),
    }
    # Token exchange is unauthenticated (no access_token yet).
    url = f"{_base_url(ctx)}/session/token"
    headers = {
        "X-Kite-Version": str(s.get("kite_version") or _KITE_VERSION),
        "Content-Type": "application/x-www-form-urlencoded",
    }
    body = await ctx.http.post(url, form=payload, headers=headers)
    return _unwrap(body, "create_session")


async def invalidate_session(ctx) -> Any:
    """Logout / invalidate access_token (DELETE /session/token)."""
    params = {"api_key": _api_key(ctx), "access_token": _access_token(ctx)}
    url = f"{_base_url(ctx)}/session/token?{urlencode(params)}"
    headers = {
        "X-Kite-Version": str(_settings(ctx).get("kite_version") or _KITE_VERSION),
    }
    body = await ctx.http.delete(url, headers=headers)
    return _unwrap(body, "invalidate_session")


async def get_profile(ctx) -> Any:
    """GET /user/profile."""
    return await _get(ctx, "/user/profile", "get_profile")


async def get_user_margins(ctx, segment: str = "") -> Any:
    """GET /user/margins or /user/margins/{equity|commodity}."""
    seg = segment.lower().strip()
    if seg in {"equity", "commodity"}:
        return await _get(ctx, f"/user/margins/{seg}", "get_user_margins")
    return await _get(ctx, "/user/margins", "get_user_margins")


async def get_account_health(ctx) -> Any:
    """Convenience: profile + margins without returning tokens."""
    s = _settings(ctx)
    has_token = bool(str(s.get("access_token") or s.get("token") or "").strip())
    has_key = bool(str(s.get("api_key") or "").strip())
    profile = None
    margins = None
    error = None
    if has_token and has_key:
        try:
            profile = await get_profile(ctx)
            margins = await get_user_margins(ctx)
        except Exception as exc:  # noqa: BLE001
            error = str(exc)
    return {
        "ok": True,
        "data": {
            "broker": "zerodha",
            "api_key_configured": has_key,
            "access_token_configured": has_token,
            "base_url": _base_url(ctx),
            "profile": profile,
            "margins": margins,
            "error": error,
            "note": "Tokens are never returned. Kite access_token typically expires ~06:00 IST.",
        },
    }


# --- Portfolio ---


async def get_holdings(ctx) -> Any:
    """GET /portfolio/holdings."""
    return await _get(ctx, "/portfolio/holdings", "get_holdings")


async def get_positions(ctx) -> Any:
    """GET /portfolio/positions (net + day)."""
    return await _get(ctx, "/portfolio/positions", "get_positions")


async def convert_position(
    ctx,
    tradingsymbol: str,
    exchange: str,
    transaction_type: str,
    position_type: str,
    quantity: int,
    old_product: str,
    new_product: str,
) -> Any:
    """PUT /portfolio/positions — convert margin product (mutating)."""
    payload = {
        "tradingsymbol": tradingsymbol,
        "exchange": exchange.upper(),
        "transaction_type": transaction_type.upper(),
        "position_type": position_type,
        "quantity": int(quantity),
        "old_product": old_product.upper(),
        "new_product": new_product.upper(),
    }
    return await _form(ctx, "PUT", "/portfolio/positions", payload, "convert_position")


# --- Orders ---


async def place_order(
    ctx,
    tradingsymbol: str,
    exchange: str,
    transaction_type: str,
    quantity: int,
    order_type: str = "MARKET",
    product: str = "CNC",
    variety: str = "regular",
    validity: str = "DAY",
    price: float = 0,
    trigger_price: float = 0,
    disclosed_quantity: int = 0,
    tag: str = "",
) -> Any:
    """POST /orders/{variety} (mutating). tag max 20 chars — use as idempotency hint."""
    variety = (variety or "regular").lower()
    payload: dict[str, Any] = {
        "tradingsymbol": tradingsymbol,
        "exchange": exchange.upper(),
        "transaction_type": transaction_type.upper(),
        "quantity": int(quantity),
        "order_type": order_type.upper(),
        "product": product.upper(),
        "validity": validity.upper(),
    }
    if price:
        payload["price"] = float(price)
    if trigger_price:
        payload["trigger_price"] = float(trigger_price)
    if disclosed_quantity:
        payload["disclosed_quantity"] = int(disclosed_quantity)
    if tag:
        payload["tag"] = str(tag)[:20]
    return await _form(ctx, "POST", f"/orders/{variety}", payload, "place_order")


async def modify_order(
    ctx,
    order_id: str,
    variety: str = "regular",
    quantity: int = 0,
    order_type: str = "",
    price: float = 0,
    trigger_price: float = 0,
    validity: str = "",
) -> Any:
    """PUT /orders/{variety}/{order_id} (mutating)."""
    variety = (variety or "regular").lower()
    payload: dict[str, Any] = {}
    if quantity:
        payload["quantity"] = int(quantity)
    if order_type:
        payload["order_type"] = order_type.upper()
    if price:
        payload["price"] = float(price)
    if trigger_price:
        payload["trigger_price"] = float(trigger_price)
    if validity:
        payload["validity"] = validity.upper()
    oid = quote(str(order_id), safe="")
    return await _form(ctx, "PUT", f"/orders/{variety}/{oid}", payload, "modify_order")


async def cancel_order(ctx, order_id: str, variety: str = "regular") -> Any:
    """DELETE /orders/{variety}/{order_id} (mutating)."""
    variety = (variety or "regular").lower()
    oid = quote(str(order_id), safe="")
    return await _form(ctx, "DELETE", f"/orders/{variety}/{oid}", {}, "cancel_order")


async def list_orders(ctx) -> Any:
    """GET /orders — day order book."""
    return await _get(ctx, "/orders", "list_orders")


async def get_order_history(ctx, order_id: str) -> Any:
    """GET /orders/{order_id}."""
    oid = quote(str(order_id), safe="")
    return await _get(ctx, f"/orders/{oid}", "get_order_history")


async def list_trades(ctx) -> Any:
    """GET /trades — day trades."""
    return await _get(ctx, "/trades", "list_trades")


async def get_order_trades(ctx, order_id: str) -> Any:
    """GET /orders/{order_id}/trades."""
    oid = quote(str(order_id), safe="")
    return await _get(ctx, f"/orders/{oid}/trades", "get_order_trades")


# --- Margins / quotes ---


async def get_order_margins(
    ctx,
    exchange: str,
    tradingsymbol: str,
    transaction_type: str,
    variety: str,
    product: str,
    order_type: str,
    quantity: int,
    price: float = 0,
    trigger_price: float = 0,
) -> Any:
    """POST /margins/orders (JSON body — Kite margins API is JSON)."""
    item: dict[str, Any] = {
        "exchange": exchange.upper(),
        "tradingsymbol": tradingsymbol,
        "transaction_type": transaction_type.upper(),
        "variety": variety.lower(),
        "product": product.upper(),
        "order_type": order_type.upper(),
        "quantity": int(quantity),
    }
    if price:
        item["price"] = float(price)
    if trigger_price:
        item["trigger_price"] = float(trigger_price)
    url = f"{_base_url(ctx)}/margins/orders"
    headers = {**_auth_headers(ctx), "Content-Type": "application/json"}
    body = await ctx.http.post(url, json=[item], headers=headers)
    return _unwrap(body, "get_order_margins")


# --- Live data (read) ---

_QUOTE_BATCH_SIZE = 200


def _split_csv(*parts: str) -> list[str]:
    items: list[str] = []
    for part in parts:
        for piece in str(part or "").split(","):
            text = piece.strip()
            if text:
                items.append(text)
    return items


def _normalize_kite_instrument(symbol: str) -> str:
    """Normalize Atlas-style symbols to Kite `exchange:tradingsymbol`."""
    text = symbol.strip()
    if ":" not in text:
        return f"NSE:{text}"
    exchange, tradingsymbol = text.split(":", 1)
    return f"{exchange.upper()}:{tradingsymbol.strip()}"


def _groww_style_to_instruments(
    *,
    exchange: str = "NSE",
    segment: str = "CASH",
    trading_symbols: str = "",
) -> list[str]:
    """Convert Groww-style batch args into Kite instrument ids."""
    instruments: list[str] = []
    exch = exchange.upper()
    seg = segment.upper()
    for sym in _split_csv(trading_symbols):
        if ":" in sym:
            instruments.append(_normalize_kite_instrument(sym))
            continue
        if seg == "FNO":
            kite_ex = "BFO" if exch == "BSE" else "NFO"
            instruments.append(f"{kite_ex}:{sym}")
        elif seg == "COMMODITY" or exch == "MCX":
            instruments.append(f"MCX:{sym}")
        else:
            instruments.append(f"{exch}:{sym}")
    return instruments


def _resolve_instruments(
    *,
    instruments: str = "",
    trading_symbols: str = "",
    exchange: str = "",
    segment: str = "",
) -> list[str]:
    if instruments:
        return [_normalize_kite_instrument(item) for item in _split_csv(instruments)]
    if trading_symbols:
        return _groww_style_to_instruments(
            exchange=exchange or "NSE",
            segment=segment or "CASH",
            trading_symbols=trading_symbols,
        )
    raise RuntimeError(
        "instruments is required (comma-separated), e.g. NSE:NIFTY 50,NFO:NIFTY26AUGFUT"
    )


def _merge_quote_payloads(results: list[Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for result in results:
        if not isinstance(result, dict):
            continue
        if result.get("ok") is False:
            continue
        data = result.get("data", result)
        if isinstance(data, dict):
            merged.update(data)
    return merged


async def _quote_batches(
    ctx,
    path: str,
    label: str,
    instruments: list[str],
) -> Any:
    if not instruments:
        raise RuntimeError("At least one instrument is required")
    if len(instruments) <= _QUOTE_BATCH_SIZE:
        return await _get(ctx, path, label, query={"i": instruments})
    chunks: list[Any] = []
    for start in range(0, len(instruments), _QUOTE_BATCH_SIZE):
        batch = instruments[start : start + _QUOTE_BATCH_SIZE]
        chunks.append(await _get(ctx, path, label, query={"i": batch}))
    data = _merge_quote_payloads(chunks)
    return {"ok": True, "data": data}


async def get_quote(
    ctx,
    instruments: str = "",
    trading_symbols: str = "",
    exchange: str = "",
    segment: str = "",
) -> Any:
    """Full market quote. Use Kite ids (`NSE:NIFTY 50,NFO:NIFTY26AUG24500CE`) or Groww-style batch args."""
    resolved = _resolve_instruments(
        instruments=instruments,
        trading_symbols=trading_symbols,
        exchange=exchange,
        segment=segment,
    )
    return await _quote_batches(ctx, "/quote", "get_quote", resolved)


async def get_ltp(
    ctx,
    instruments: str = "",
    trading_symbols: str = "",
    exchange: str = "",
    segment: str = "",
) -> Any:
    """Last traded price. Instruments as `exchange:tradingsymbol` (comma-separated)."""
    resolved = _resolve_instruments(
        instruments=instruments,
        trading_symbols=trading_symbols,
        exchange=exchange,
        segment=segment,
    )
    return await _quote_batches(ctx, "/quote/ltp", "get_ltp", resolved)


async def get_ohlc(
    ctx,
    instruments: str = "",
    trading_symbols: str = "",
    exchange: str = "",
    segment: str = "",
) -> Any:
    """OHLC snapshot. Instruments as `exchange:tradingsymbol` (comma-separated)."""
    resolved = _resolve_instruments(
        instruments=instruments,
        trading_symbols=trading_symbols,
        exchange=exchange,
        segment=segment,
    )
    return await _quote_batches(ctx, "/quote/ohlc", "get_ohlc", resolved)


async def get_historical_candles(
    ctx,
    instrument_token: int,
    interval: str = "15minute",
    from_date: str = "",
    to_date: str = "",
    continuous: int = 0,
    oi: int = 0,
) -> Any:
    """Historical OHLC candles for ADX / trend filters.

    interval: minute, 3minute, 5minute, 10minute, 15minute, 30minute, 60minute, day
    from_date / to_date: `YYYY-MM-DD HH:MM:SS` (IST session times for Indian markets).
    instrument_token: from a full `get_quote` on the underlying.
    """
    if not instrument_token:
        raise RuntimeError("instrument_token is required")
    allowed = {
        "minute",
        "3minute",
        "5minute",
        "10minute",
        "15minute",
        "30minute",
        "60minute",
        "day",
    }
    bucket = str(interval or "15minute").strip().lower()
    if bucket not in allowed:
        raise RuntimeError(f"unsupported interval: {interval}")
    query: dict[str, Any] = {"continuous": int(continuous), "oi": int(oi)}
    if from_date:
        query["from"] = from_date
    if to_date:
        query["to"] = to_date
    path = f"/instruments/historical/{int(instrument_token)}/{bucket}"
    return await _get(ctx, path, "get_historical_candles", query=query)
