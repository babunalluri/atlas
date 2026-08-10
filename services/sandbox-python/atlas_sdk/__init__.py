"""Minimal guest SDK — all HTTP goes through host HttpProxy IPC."""

from __future__ import annotations

from typing import Any

from atlas_sdk.ipc import http_proxy, rpc

__all__ = ["Context", "HttpClient", "emit_result", "http_proxy", "rpc"]


class HttpClient:
    async def get(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        return await self.request("GET", url, params=params, headers=headers)

    async def post(
        self,
        url: str,
        *,
        json: dict[str, Any] | list[Any] | None = None,
        form: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        return await self.request("POST", url, json=json, form=form, headers=headers)

    async def put(
        self,
        url: str,
        *,
        json: dict[str, Any] | list[Any] | None = None,
        form: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        return await self.request("PUT", url, json=json, form=form, headers=headers)

    async def delete(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        return await self.request("DELETE", url, params=params, headers=headers)

    async def request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | list[Any] | None = None,
        form: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        # Async surface for author ergonomics; IPC itself is synchronous.
        result = http_proxy(
            method,
            url,
            params=params,
            json_body=json,
            form_body=form,
            headers=headers,
        )
        if not result.get("ok", True):
            raise RuntimeError(result.get("error") or "HTTP proxy failed")
        return result.get("body", result)


class Context:
    def __init__(self, settings: dict[str, Any]) -> None:
        self.settings = settings
        self.http = HttpClient()


def emit_result(*, ok: bool, value: Any = None, error: str | None = None) -> None:
    rpc("RunResult", {"ok": ok, "value": value, "error": error})
