"""Shared JSON-RPC stdin/stdout helpers for guest ↔ host IPC."""

from __future__ import annotations

import json
import sys
from typing import Any

_counter = 0


def _next_id() -> int:
    global _counter
    _counter += 1
    return _counter


def rpc(method: str, params: dict[str, Any]) -> Any:
    """Send a JSON-RPC request on stdout and read one response from stdin."""
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


def http_proxy(
    method: str,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: Any = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Call host HttpProxy; returns the raw result dict (ok/status_code/body/error)."""
    result = rpc(
        "HttpProxy",
        {
            "method": method,
            "url": url,
            "params": params or {},
            "json": json_body,
            "headers": headers or {},
        },
    )
    if not isinstance(result, dict):
        return {"ok": False, "error": "Invalid HttpProxy response", "status_code": 502}
    return result
