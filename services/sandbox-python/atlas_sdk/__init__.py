"""Minimal guest SDK — all HTTP goes through host HttpProxy IPC."""

from __future__ import annotations

import json
import sys
from typing import Any


def _rpc(method: str, params: dict[str, Any]) -> Any:
    request = {"jsonrpc": "2.0", "id": _next_id(), "method": method, "params": params}
    sys.stdout.write(json.dumps(request, separators=(",", ":")) + "\n")
    sys.stdout.flush()
    line = sys.stdin.readline()
    if not line:
        raise RuntimeError("Sandbox host closed IPC channel")
    message = json.loads(line)
    if "error" in message:
        raise RuntimeError(str(message["error"]))
    return message.get("result")


_counter = 0


def _next_id() -> int:
    global _counter
    _counter += 1
    return _counter


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
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        return await self.request("POST", url, json=json, headers=headers)

    async def request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        # Async surface for author ergonomics; IPC itself is synchronous.
        result = _rpc(
            "HttpProxy",
            {
                "method": method,
                "url": url,
                "params": params or {},
                "json": json,
                "headers": headers or {},
            },
        )
        if not result.get("ok", True):
            raise RuntimeError(result.get("error") or "HTTP proxy failed")
        return result.get("body", result)


class Context:
    def __init__(self, settings: dict[str, Any]) -> None:
        self.settings = settings
        self.http = HttpClient()


def emit_result(*, ok: bool, value: Any = None, error: str | None = None) -> None:
    _rpc("RunResult", {"ok": ok, "value": value, "error": error})
