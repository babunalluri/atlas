"""Cold store for Kite daily candles used by Param Chart.

Production (OCI): Object Storage via Amazon S3 Compatibility API
(``DOCUMENT_BUCKET`` + ``AWS_ENDPOINT_URL`` =
``https://<ns>.compat.objectstorage.<region>.oraclecloud.com``).

Local/pilot: Compose MinIO with the same env shape, or local disk under
``document_upload_dir/param-chart-candles`` when no bucket is set.

Keys are deterministic — not knowledge UUID uploads.
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

# Shared across tenants: candles are market data keyed by instrument_token.
PREFIX = "param-chart/candles"
_s3_cached: tuple[tuple[Any, ...], Any, str] | None = None


def _canon_interval(interval: str | None) -> str:
    from app.domains.param_chart_constants import normalize_param_chart_interval

    return normalize_param_chart_interval(interval)


def _month_key(token: int, year: int, month: int, interval: str = "1D") -> str:
    iv = _canon_interval(interval)
    if iv == "1D":
        # Keep legacy day dump path for cache hits already written.
        return f"{PREFIX}/{int(token)}/{year:04d}-{month:02d}.json"
    if iv == "1M":
        return f"{PREFIX}/{int(token)}/{iv}/{year:04d}.json"
    # Includes 1m / 1H / 1W — keep literal id so 1m ≠ 1M.
    return f"{PREFIX}/{int(token)}/{iv}/{year:04d}-{month:02d}.json"


def _local_root() -> Path:
    cfg = get_settings()
    root = Path(cfg.document_upload_dir)
    return root / "param-chart-candles"


def _s3_client_and_bucket() -> tuple[Any, str] | None:
    """Cached boto3 client for MinIO **or** OCI Object Storage S3 Compatibility API."""
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
        from botocore.client import Config
    except Exception:  # noqa: BLE001
        return None
    kwargs: dict[str, object] = {
        "region_name": cfg.aws_region or "us-east-1",
        "config": Config(signature_version="s3v4"),
    }
    if cfg.aws_endpoint_url:
        kwargs["endpoint_url"] = cfg.aws_endpoint_url
        kwargs["config"] = Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
        )
    if cfg.aws_access_key_id and secret:
        kwargs["aws_access_key_id"] = cfg.aws_access_key_id
        kwargs["aws_secret_access_key"] = secret
    client = boto3.client("s3", **kwargs)
    _s3_cached = (cache_key, client, bucket)
    return client, bucket


def reset_candle_store_for_tests() -> None:
    global _s3_cached
    _s3_cached = None


async def get_month_candles(
    token: int, *, year: int, month: int, interval: str = "1D"
) -> dict[str, Any] | None:
    """Return stored hist payload ``{ok, data:{candles}, fetched_at, source}`` or None."""
    key = _month_key(token, year, month, interval)

    client_bucket = _s3_client_and_bucket()
    if client_bucket is not None:
        client, bucket = client_bucket

        def _get() -> bytes | None:
            try:
                obj = client.get_object(Bucket=bucket, Key=key)
                return obj["Body"].read()
            except Exception:  # noqa: BLE001
                return None

        raw = await to_thread.run_sync(_get)
        if raw:
            try:
                payload = json.loads(raw)
                if isinstance(payload, dict) and payload.get("data"):
                    return payload
            except json.JSONDecodeError:
                logger.warning("param_chart_candle_s3_bad_json", key=key)

    iv = _canon_interval(interval)
    if iv == "1M":
        path = _local_root() / f"{int(token)}" / iv / f"{year:04d}.json"
    elif iv == "1D":
        path = _local_root() / f"{int(token)}" / f"{year:04d}-{month:02d}.json"
    else:
        path = _local_root() / f"{int(token)}" / iv / f"{year:04d}-{month:02d}.json"

    def _read_local() -> bytes | None:
        if not path.is_file():
            return None
        return path.read_bytes()

    raw_local = await to_thread.run_sync(_read_local)
    if not raw_local:
        return None
    try:
        payload = json.loads(raw_local)
        return payload if isinstance(payload, dict) else None
    except json.JSONDecodeError:
        return None


async def put_month_candles(
    token: int,
    *,
    year: int,
    month: int,
    hist: Any,
    source: str = "kite",
    interval: str = "1D",
) -> str | None:
    """Persist Kite hist response for token/month. Returns storage URI or None."""
    if not hist or token <= 0:
        return None
    # Normalize to toolkit-shaped envelope.
    if isinstance(hist, dict) and hist.get("ok") is False:
        return None
    candles: list[Any] = []
    if isinstance(hist, dict):
        data = hist.get("data", hist)
        if isinstance(data, dict) and isinstance(data.get("candles"), list):
            candles = data["candles"]
        elif isinstance(hist.get("candles"), list):
            candles = hist["candles"]
    elif isinstance(hist, list):
        candles = hist
    if not candles:
        return None

    payload = {
        "ok": True,
        "data": {"candles": candles},
        "instrument_token": int(token),
        "year": year,
        "month": month,
        "interval": _canon_interval(interval),
        "fetched_at": int(time.time()),
        "source": source,
        "candle_count": len(candles),
    }
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    key = _month_key(token, year, month, interval)

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
            return await to_thread.run_sync(_put)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "param_chart_candle_s3_put_failed",
                key=key,
                error=str(exc)[:200],
            )

    iv = _canon_interval(interval)
    if iv == "1M":
        path = _local_root() / f"{int(token)}" / iv / f"{year:04d}.json"
    elif iv == "1D":
        path = _local_root() / f"{int(token)}" / f"{year:04d}-{month:02d}.json"
    else:
        path = _local_root() / f"{int(token)}" / iv / f"{year:04d}-{month:02d}.json"

    def _write() -> str:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        return path.resolve().as_uri()

    try:
        return await to_thread.run_sync(_write)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "param_chart_candle_local_put_failed",
            path=str(path),
            error=str(exc)[:200],
        )
        return None


def should_refresh_month_dump(
    stored: dict[str, Any] | None,
    *,
    year: int,
    month: int,
    now_ts: int | None = None,
) -> bool:
    """Past months are stable forever; current month refreshes at most once/hour.

    Design: fetch OHLC from Kite once → dump to disk/object storage → reuse.
    Manual Refresh / missing dump is what triggers a new Kite hist pull.
    """
    if stored is None:
        return True
    from datetime import datetime
    from zoneinfo import ZoneInfo

    now = datetime.now(ZoneInfo("Asia/Kolkata"))
    data = stored.get("data") if isinstance(stored.get("data"), dict) else None
    candles = data.get("candles") if isinstance(data, dict) else None
    if not candles:
        return True
    if year != now.year or month != now.month:
        # Completed / future month — keep dump forever once we have bars.
        return False
    fetched = int(stored.get("fetched_at") or 0)
    ts = now_ts if now_ts is not None else int(time.time())
    return (ts - fetched) > 3600
