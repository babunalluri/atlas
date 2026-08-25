"""Durable shared-checklist metrics for Param Chart day history.

Redis month packs are short-TTL and rebuilt per interval — they cannot own
overlay history. This cold store mirrors the candle dump pattern: write once
(per tenant/month), merge into day cards whenever a pack is rebuilt.

``get_month_metrics`` is on the SSE hot path (~2 Hz). Reads are memoized for
~60s so object-storage / disk I/O is not on every ``month_state`` tick.
Writes (at most every ~5 minutes via Redis gate) refresh the memo immediately.
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

PREFIX = "param-chart/metrics"
# Metrics persist at most every ~5 minutes; 60s is fresh enough for overlays.
READ_CACHE_TTL_S = 60.0

_s3_cached: tuple[tuple[Any, ...], Any, str] | None = None
# tenant|year|month → (expires_monotonic, days_or_None)
_read_cache: dict[str, tuple[float, dict[str, Any] | None]] = {}


def _month_key(tenant_id: str, year: int, month: int) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(tenant_id))[:64]
    return f"{PREFIX}/{safe}/{year:04d}-{month:02d}.json"


def _tenant_dir_name(tenant_id: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(tenant_id))[:64]


def _cache_key(tenant_id: str, year: int, month: int) -> str:
    return f"{tenant_id}|{year}|{month}"


def _local_root() -> Path:
    cfg = get_settings()
    return Path(cfg.document_upload_dir) / "param-chart-metrics"


def reset_metrics_store_for_tests() -> None:
    _read_cache.clear()


def _s3_client_and_bucket() -> tuple[Any, str] | None:
    """Reuse the same bucket/client shape as candle dumps."""
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


def _parse_days_payload(raw: bytes) -> dict[str, Any] | None:
    try:
        data = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    days = data.get("days")
    return days if isinstance(days, dict) else None


async def _load_month_metrics(
    tenant_id: str, *, year: int, month: int
) -> dict[str, Any] | None:
    """Cold read from object storage or local disk (no memo)."""
    key = _month_key(tenant_id, year, month)
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
            days = _parse_days_payload(raw)
            if days is not None:
                return days
            logger.warning("param_chart_metrics_s3_bad_json", key=key)

    path = _local_root() / _tenant_dir_name(tenant_id) / f"{year:04d}-{month:02d}.json"

    def _read() -> bytes | None:
        if not path.is_file():
            return None
        return path.read_bytes()

    try:
        raw = await to_thread.run_sync(_read)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "param_chart_metrics_local_get_failed",
            path=str(path),
            error=str(exc)[:200],
        )
        return None
    if not raw:
        return None
    return _parse_days_payload(raw)


def _memo_set(
    tenant_id: str, year: int, month: int, days: dict[str, Any] | None
) -> None:
    _read_cache[_cache_key(tenant_id, year, month)] = (
        time.monotonic() + READ_CACHE_TTL_S,
        dict(days) if isinstance(days, dict) else None,
    )


async def get_month_metrics(
    tenant_id: str, *, year: int, month: int
) -> dict[str, Any] | None:
    """Return ``{day_iso: {metric_id: row, ...}, ...}`` or None.

    Memoized ~60s so SSE ``month_state`` does not hit S3/disk at 2 Hz.
    """
    ck = _cache_key(tenant_id, year, month)
    entry = _read_cache.get(ck)
    if entry is not None:
        expires, cached = entry
        if expires > time.monotonic():
            return dict(cached) if isinstance(cached, dict) else None

    days = await _load_month_metrics(tenant_id, year=year, month=month)
    _memo_set(tenant_id, year, month, days)
    return dict(days) if isinstance(days, dict) else None


async def upsert_day_metrics(
    tenant_id: str,
    *,
    year: int,
    month: int,
    day: str,
    metrics: dict[str, Any],
) -> str | None:
    """Merge one day's shared metrics into the durable month document."""
    if not metrics:
        return None
    existing = await get_month_metrics(tenant_id, year=year, month=month) or {}
    day_key = str(day)[:10]
    raw_prev = existing.get(day_key)
    prev: dict[str, Any] = raw_prev if isinstance(raw_prev, dict) else {}
    existing[day_key] = {**prev, **metrics}
    payload = {
        "ok": True,
        "tenant_id": str(tenant_id),
        "year": year,
        "month": month,
        "days": existing,
        "updated_at": int(time.time()),
    }
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    key = _month_key(tenant_id, year, month)
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
                "param_chart_metrics_s3_put_failed",
                key=key,
                error=str(exc)[:200],
            )

    if uri is None:
        path = (
            _local_root()
            / _tenant_dir_name(tenant_id)
            / f"{year:04d}-{month:02d}.json"
        )

        def _write() -> str:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
            return path.resolve().as_uri()

        try:
            uri = await to_thread.run_sync(_write)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "param_chart_metrics_local_put_failed",
                path=str(path),
                error=str(exc)[:200],
            )
            return None

    # Refresh memo so the next SSE tick sees the write without waiting TTL.
    _memo_set(tenant_id, year, month, existing)
    return uri


def merge_metrics_into_days(
    days: list[dict[str, Any]],
    stored: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Attach durable metrics onto day cards (by calendar date prefix).

    Prefer ``metrics_by_day`` on the pack + client lookup for intraday — embedding
    a 30-metric blob on every 1m bar balloons SSE frames (~tens of MB at 2 Hz).
    Kept for tests / rare callers that still need inlined metrics.
    """
    if not stored or not days:
        return days
    out: list[dict[str, Any]] = []
    for row in days:
        updated = dict(row)
        raw_date = str(row.get("date") or "")
        day_key = raw_date[:10]
        metrics = stored.get(day_key)
        if isinstance(metrics, dict) and metrics:
            # Prefer stored history; live overlay may still replace "today".
            existing = updated.get("metrics") if isinstance(updated.get("metrics"), dict) else {}
            updated["metrics"] = {**metrics, **existing} if existing else dict(metrics)
        out.append(updated)
    return out


def strip_embedded_metrics(days: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop per-bar ``metrics`` blobs so packs/SSE stay lean."""
    out: list[dict[str, Any]] = []
    for row in days:
        if not isinstance(row, dict):
            continue
        if not row.get("metrics"):
            out.append(row)
            continue
        updated = dict(row)
        updated["metrics"] = {}
        out.append(updated)
    return out


def normalize_metrics_by_day(stored: dict[str, Any] | None) -> dict[str, Any]:
    """Ensure cold-store / pack map is ``{YYYY-MM-DD: {metric_id: …}}``."""
    if not isinstance(stored, dict):
        return {}
    out: dict[str, Any] = {}
    for key, val in stored.items():
        if key in ("ok", "tenant_id", "year", "month", "fetched_at", "source"):
            continue
        if isinstance(val, dict) and len(str(key)) >= 10:
            out[str(key)[:10]] = val
    return out
