"""Compatibility shim for `import requests` inside the network-less sandbox.

Outbound calls go through host HttpProxy IPC (same path as atlas_sdk / ctx.http).
Real PyPI requests is not installed; this package shadows the name on PYTHONPATH.
"""

from __future__ import annotations

import base64
import json
from typing import Any
from urllib.parse import urlencode

from atlas_sdk.ipc import http_proxy
from .exceptions import HTTPError, RequestException

__all__ = [
    "HTTPError",
    "RequestException",
    "Response",
    "Session",
    "delete",
    "get",
    "patch",
    "post",
    "put",
    "request",
]


class Response:
    """Subset of requests.Response used by common toolkits."""

    def __init__(
        self,
        *,
        status_code: int,
        body: Any = None,
        ok: bool | None = None,
        url: str = "",
    ) -> None:
        self.status_code = int(status_code)
        self.url = url
        self._json: Any | None
        if isinstance(body, (dict, list)):
            self._json = body
            self.text = json.dumps(body)
        elif body is None:
            self._json = None
            self.text = ""
        else:
            self.text = str(body)
            try:
                self._json = json.loads(self.text)
            except (TypeError, ValueError, json.JSONDecodeError):
                self._json = None
        self.ok = (200 <= self.status_code < 300) if ok is None else bool(ok)
        self.content = self.text.encode("utf-8")

    def json(self, **kwargs: Any) -> Any:  # noqa: ARG002 - match requests signature
        if self._json is not None:
            return self._json
        return json.loads(self.text)

    def raise_for_status(self) -> None:
        if self.ok:
            return
        raise HTTPError(
            f"{self.status_code} Error for url: {self.url}",
            response=self,
        )


class Session:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.auth: tuple[str, str] | None = None

    def request(self, method: str, url: str, **kwargs: Any) -> Response:
        headers = {**self.headers, **dict(kwargs.pop("headers", None) or {})}
        auth = kwargs.pop("auth", self.auth)
        return request(method, url, headers=headers, auth=auth, **kwargs)

    def get(self, url: str, **kwargs: Any) -> Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> Response:
        return self.request("POST", url, **kwargs)

    def put(self, url: str, **kwargs: Any) -> Response:
        return self.request("PUT", url, **kwargs)

    def patch(self, url: str, **kwargs: Any) -> Response:
        return self.request("PATCH", url, **kwargs)

    def delete(self, url: str, **kwargs: Any) -> Response:
        return self.request("DELETE", url, **kwargs)


def _apply_auth(headers: dict[str, str], auth: Any) -> None:
    if auth is None:
        return
    if isinstance(auth, (tuple, list)) and len(auth) == 2:
        user, password = str(auth[0]), str(auth[1])
        token = base64.b64encode(f"{user}:{password}".encode()).decode("ascii")
        headers.setdefault("Authorization", f"Basic {token}")
        return
    raise TypeError("auth must be a (username, password) tuple")


def _normalize_json_body(*, json_body: Any, data: Any) -> Any:
    if json_body is not None:
        return json_body
    if data is None:
        return None
    if isinstance(data, dict):
        # Host proxy currently accepts JSON bodies only; dict data → JSON.
        return data
    if isinstance(data, (bytes, bytearray)):
        data = data.decode("utf-8", errors="replace")
    if isinstance(data, str):
        try:
            return json.loads(data)
        except json.JSONDecodeError as exc:
            raise NotImplementedError(
                "requests shim only supports json= or dict/JSON-string data= "
                "(form/raw bodies are not proxied)"
            ) from exc
    raise TypeError("data must be a dict, JSON string, or bytes")


def request(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    json: Any = None,  # noqa: A002 - match requests API
    data: Any = None,
    timeout: Any = None,  # noqa: ARG001 - accepted for API compat; host enforces timeout
    auth: Any = None,
    **_unused: Any,
) -> Response:
    hdrs = {str(k): str(v) for k, v in dict(headers or {}).items()}
    _apply_auth(hdrs, auth)
    json_body = _normalize_json_body(json_body=json, data=data)

    # Append params to URL for display; host also applies params from IPC.
    display_url = url
    if params:
        sep = "&" if "?" in url else "?"
        display_url = f"{url}{sep}{urlencode({str(k): str(v) for k, v in params.items()})}"

    result = http_proxy(
        method.upper(),
        url,
        params=params,
        json_body=json_body,
        headers=hdrs,
    )
    status = int(result.get("status_code") or (200 if result.get("ok") else 502))
    if result.get("ok", False):
        return Response(status_code=status, body=result.get("body"), url=display_url)
    return Response(
        status_code=status,
        body=result.get("error") or "HTTP proxy failed",
        ok=False,
        url=display_url,
    )


def get(url: str, **kwargs: Any) -> Response:
    return request("GET", url, **kwargs)


def post(url: str, **kwargs: Any) -> Response:
    return request("POST", url, **kwargs)


def put(url: str, **kwargs: Any) -> Response:
    return request("PUT", url, **kwargs)


def patch(url: str, **kwargs: Any) -> Response:
    return request("PATCH", url, **kwargs)


def delete(url: str, **kwargs: Any) -> Response:
    return request("DELETE", url, **kwargs)
