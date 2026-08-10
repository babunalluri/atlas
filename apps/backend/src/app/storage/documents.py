"""Document object storage for knowledge uploads (local disk or S3/MinIO)."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse

from anyio import to_thread

from app.core.settings import Settings, get_settings


class DocumentStore(Protocol):
    async def put(self, *, tenant_id: str, name: str, data: bytes) -> str: ...

    async def get(self, uri: str) -> bytes: ...

    async def delete(self, uri: str) -> None: ...


class LocalDocumentStore:
    def __init__(self, root: str) -> None:
        self.root = Path(root)

    def _path_for(self, tenant_id: str, name: str) -> Path:
        safe = Path(name).name.replace("\x00", "_")
        return self.root / tenant_id / f"{uuid.uuid4()}-{safe}"

    async def put(self, *, tenant_id: str, name: str, data: bytes) -> str:
        path = self._path_for(tenant_id, name)

        def _write() -> str:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            return path.resolve().as_uri()

        return await to_thread.run_sync(_write)

    async def get(self, uri: str) -> bytes:
        path = _uri_to_path(uri, root=self.root)

        def _read() -> bytes:
            if not path.is_file():
                raise FileNotFoundError(uri)
            return path.read_bytes()

        return await to_thread.run_sync(_read)

    async def delete(self, uri: str) -> None:
        path = _uri_to_path(uri, root=self.root)

        def _unlink() -> None:
            path.unlink(missing_ok=True)

        try:
            await to_thread.run_sync(_unlink)
        except OSError:
            pass


class S3DocumentStore:
    def __init__(
        self,
        *,
        bucket: str,
        region: str,
        endpoint_url: str | None = None,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
    ) -> None:
        import boto3
        from botocore.client import Config

        kwargs: dict[str, object] = {
            "region_name": region,
            "config": Config(signature_version="s3v4"),
        }
        if endpoint_url:
            # Path-style required for MinIO and most custom S3 endpoints.
            kwargs["endpoint_url"] = endpoint_url
            kwargs["config"] = Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
            )
        if access_key_id and secret_access_key:
            kwargs["aws_access_key_id"] = access_key_id
            kwargs["aws_secret_access_key"] = secret_access_key
        self.bucket = bucket
        self._client = boto3.client("s3", **kwargs)

    def _key(self, tenant_id: str, name: str) -> str:
        safe = Path(name).name.replace("\x00", "_")
        return f"{tenant_id}/{uuid.uuid4()}-{safe}"

    async def put(self, *, tenant_id: str, name: str, data: bytes) -> str:
        key = self._key(tenant_id, name)

        def _upload() -> str:
            self._client.put_object(Bucket=self.bucket, Key=key, Body=data)
            return f"s3://{self.bucket}/{key}"

        return await to_thread.run_sync(_upload)

    async def get(self, uri: str) -> bytes:
        bucket, key = _parse_s3_uri(uri)
        if bucket != self.bucket:
            raise FileNotFoundError(uri)

        def _download() -> bytes:
            obj = self._client.get_object(Bucket=bucket, Key=key)
            return obj["Body"].read()

        return await to_thread.run_sync(_download)

    async def delete(self, uri: str) -> None:
        bucket, key = _parse_s3_uri(uri)
        if bucket != self.bucket:
            return

        def _remove() -> None:
            self._client.delete_object(Bucket=bucket, Key=key)

        try:
            await to_thread.run_sync(_remove)
        except Exception:
            pass


def _uri_to_path(uri: str, *, root: Path | None = None) -> Path:
    if uri.startswith("file:"):
        path = Path(urlparse(uri).path)
    else:
        path = Path(uri)
    if root is not None:
        resolved = path.resolve()
        root_resolved = root.resolve()
        if not resolved.is_relative_to(root_resolved):
            raise FileNotFoundError(uri)
        return resolved
    return path


def _parse_s3_uri(uri: str) -> tuple[str, str]:
    parsed = urlparse(uri)
    if parsed.scheme != "s3" or not parsed.netloc or not parsed.path:
        raise ValueError(f"Not an s3 URI: {uri}")
    return parsed.netloc, parsed.path.lstrip("/")


def display_name_from_uri(uri: str) -> str:
    if uri.startswith("s3://"):
        return Path(urlparse(uri).path).name
    if uri.startswith("file:"):
        return Path(urlparse(uri).path).name
    return Path(uri).name


_cached_store: DocumentStore | None = None
_cached_store_key: tuple[object, ...] | None = None


def get_document_store(settings: Settings | None = None) -> DocumentStore:
    global _cached_store, _cached_store_key
    cfg = settings or get_settings()
    bucket = (cfg.document_bucket or "").strip()
    secret = None
    if cfg.aws_secret_access_key is not None:
        secret = cfg.aws_secret_access_key.get_secret_value()
    cache_key = (
        bucket,
        cfg.document_upload_dir,
        cfg.aws_region,
        cfg.aws_endpoint_url,
        cfg.aws_access_key_id,
        secret,
    )
    if _cached_store is not None and _cached_store_key == cache_key:
        return _cached_store
    if bucket:
        store: DocumentStore = S3DocumentStore(
            bucket=bucket,
            region=cfg.aws_region,
            endpoint_url=cfg.aws_endpoint_url or None,
            access_key_id=cfg.aws_access_key_id,
            secret_access_key=secret,
        )
    else:
        store = LocalDocumentStore(cfg.document_upload_dir)
    _cached_store = store
    _cached_store_key = cache_key
    return store


def reset_document_store_for_tests() -> None:
    global _cached_store, _cached_store_key
    _cached_store = None
    _cached_store_key = None
