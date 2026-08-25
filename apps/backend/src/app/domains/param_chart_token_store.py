"""Persistent tradingsymbol → instrument_token map for Param Chart.

Expired F&O contracts disappear from Kite ``get_quote`` / instruments dumps, but
``get_historical_candles`` still works if we remember the token. Capture tokens
while contracts are live; reuse them for past-month CE/PE premium trails.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from anyio import to_thread

from app.core.logging import get_logger
from app.core.settings import get_settings

logger = get_logger(__name__)

PREFIX = "param-chart/symbol-tokens"
READ_CACHE_TTL_S = 3600.0

_s3_cached: tuple[tuple[Any, ...], Any, str] | None = None
_read_cache: dict[str, tuple[float, int | None]] = {}


def _norm_symbol(symbol: str) -> tuple[str, str] | None:
    raw = (symbol or "").strip().upper()
    if not raw:
        return None
    if ":" in raw:
        exchange, ts = raw.split(":", 1)
    else:
        exchange, ts = "NFO", raw
    exchange = exchange.strip() or "NFO"
    ts = ts.strip()
    if not ts:
        return None
    return exchange, ts


def _object_key(exchange: str, tradingsymbol: str) -> str:
    safe_ex = "".join(ch if ch.isalnum() else "_" for ch in exchange)[:16]
    safe_ts = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in tradingsymbol)[
        :80
    ]
    return f"{PREFIX}/{safe_ex}/{safe_ts}.json"


def _local_path(exchange: str, tradingsymbol: str) -> Path:
    cfg = get_settings()
    safe_ex = "".join(ch if ch.isalnum() else "_" for ch in exchange)[:16]
    safe_ts = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in tradingsymbol)[
        :80
    ]
    return Path(cfg.document_upload_dir) / "param-chart-symbol-tokens" / safe_ex / f"{safe_ts}.json"


def _cache_key(exchange: str, tradingsymbol: str) -> str:
    return f"{exchange}|{tradingsymbol}"


def reset_token_store_for_tests() -> None:
    global _s3_cached
    _s3_cached = None
    _read_cache.clear()


def _s3_client_and_bucket() -> tuple[Any, str] | None:
    global _s3_cached
    cfg = get_settings()
    bucket = (cfg.document_bucket or "").strip()
    if not bucket:
        return None
    secret = None
    if cfg.aws_secret_access_key is not None:
        secret = cfg.aws_secret_access_key.get_secret_value()
    cache_key = (
        bucket,
        cfg.aws_region,
        cfg.aws_endpoint_url,
        cfg.aws_access_key_id,
        secret,
    )
    if _s3_cached is not None and _s3_cached[0] == cache_key:
        return _s3_cached[1], _s3_cached[2]
    try:
        import boto3
        from botocore.client import Config as BotoConfig
    except Exception:  # noqa: BLE001
        return None
    kwargs: dict[str, object] = {
        "region_name": cfg.aws_region or "us-east-1",
        "config": BotoConfig(signature_version="s3v4"),
    }
    if cfg.aws_endpoint_url:
        kwargs["endpoint_url"] = cfg.aws_endpoint_url
        kwargs["config"] = BotoConfig(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
        )
    if cfg.aws_access_key_id:
        kwargs["aws_access_key_id"] = cfg.aws_access_key_id
    if secret:
        kwargs["aws_secret_access_key"] = secret
    client = boto3.client("s3", **kwargs)
    _s3_cached = (cache_key, client, bucket)
    return client, bucket


def _memo_get(exchange: str, tradingsymbol: str) -> tuple[bool, int | None]:
    entry = _read_cache.get(_cache_key(exchange, tradingsymbol))
    if entry is None:
        return False, None
    expires, token = entry
    if expires <= time.monotonic():
        return False, None
    return True, token


def _memo_set(exchange: str, tradingsymbol: str, token: int | None) -> None:
    _read_cache[_cache_key(exchange, tradingsymbol)] = (
        time.monotonic() + READ_CACHE_TTL_S,
        int(token) if token else None,
    )


async def get_instrument_token(symbol: str) -> int | None:
    """Return a previously saved instrument_token for ``EXCHANGE:SYMBOL``."""
    parsed = _norm_symbol(symbol)
    if parsed is None:
        return None
    exchange, ts = parsed
    hit, cached = _memo_get(exchange, ts)
    if hit:
        return cached if cached and cached > 0 else None

    key = _object_key(exchange, ts)
    client_bucket = _s3_client_and_bucket()
    raw: bytes | None = None
    if client_bucket is not None:
        client, bucket = client_bucket

        def _get() -> bytes | None:
            try:
                obj = client.get_object(Bucket=bucket, Key=key)
                return obj["Body"].read()
            except Exception:  # noqa: BLE001
                return None

        raw = await to_thread.run_sync(_get)

    if raw is None:
        path = _local_path(exchange, ts)

        def _read() -> bytes | None:
            if not path.is_file():
                return None
            return path.read_bytes()

        try:
            raw = await to_thread.run_sync(_read)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "param_chart_token_local_get_failed",
                path=str(path),
                error=str(exc)[:200],
            )
            _memo_set(exchange, ts, None)
            return None

    if not raw:
        _memo_set(exchange, ts, None)
        return None
    try:
        data = json.loads(raw.decode("utf-8"))
        token = int((data or {}).get("instrument_token") or 0)
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError):
        _memo_set(exchange, ts, None)
        return None
    if token <= 0:
        _memo_set(exchange, ts, None)
        return None
    _memo_set(exchange, ts, token)
    return token


async def put_instrument_token(symbol: str, token: int) -> str | None:
    """Persist ``symbol → instrument_token`` for later hist after expiry."""
    if int(token or 0) <= 0:
        return None
    parsed = _norm_symbol(symbol)
    if parsed is None:
        return None
    exchange, ts = parsed
    payload = {
        "ok": True,
        "exchange": exchange,
        "tradingsymbol": ts,
        "symbol": f"{exchange}:{ts}",
        "instrument_token": int(token),
        "saved_at": int(time.time()),
    }
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    key = _object_key(exchange, ts)
    uri: str | None = None

    client_bucket = _s3_client_and_bucket()
    if client_bucket is not None:
        client, bucket = client_bucket

        def _put() -> str:
            client.put_object(
                Bucket=bucket,
                Key=key,
                Body=body,
                ContentType="application/json",
            )
            return f"s3://{bucket}/{key}"

        try:
            uri = await to_thread.run_sync(_put)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "param_chart_token_s3_put_failed",
                key=key,
                error=str(exc)[:200],
            )

    if uri is None:
        path = _local_path(exchange, ts)

        def _write() -> str:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
            return path.resolve().as_uri()

        try:
            uri = await to_thread.run_sync(_write)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "param_chart_token_local_put_failed",
                path=str(path),
                error=str(exc)[:200],
            )
            return None

    _memo_set(exchange, ts, int(token))
    return uri
