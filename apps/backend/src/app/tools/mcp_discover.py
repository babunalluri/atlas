"""Backend-only MCP discovery. The browser must never call remote MCP hosts."""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from app.core.settings import GROWW_MCP_HOST
from app.tools.providers import (
    MAX_TOOLS,
    Capability,
    ProviderValidationError,
    is_destructive_name,
)

logger = logging.getLogger(__name__)

_MAX_REDIRECTS = 5
_CLIENT_INFO = {"name": "atlas", "version": "0.1.0"}
_PROTOCOL = "2025-03-26"


def humanize_outbound_error(
    exc: BaseException,
    *,
    url: str | None = None,
    has_credential: bool = False,
) -> str:
    """Map network/library failures to a banner-safe sentence."""
    host = (urlsplit(url).hostname if url else None) or (url or "the remote host")
    if isinstance(exc, ProviderValidationError):
        return str(exc)
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        return f"Timed out connecting to {host} over HTTPS."
    if isinstance(exc, httpx.ConnectError):
        text = str(exc).lower()
        if any(
            token in text
            for token in (
                "name or service not known",
                "nodename nor servname",
                "getaddrinfo",
                "name resolution",
                "could not contact dns",
            )
        ):
            return f"Could not resolve host {host!r} (DNS)."
        return f"Could not connect to {host} over HTTPS."
    if isinstance(exc, ImportError):
        return (
            "MCP client library is unavailable or incompatible on this Atlas backend "
            f"({exc}). Enumerate and Test connection do not require that library for "
            "Streamable HTTP; switch transport if this host uses it."
        )
    text = str(exc).strip() or type(exc).__name__
    cred = (
        " A credential is already bound; the server still rejected the request."
        if has_credential
        else " No credential is bound."
    )
    if _looks_like_auth_failure(text):
        if has_credential:
            return (
                f"MCP server at {host} rejected the credential ({text}). "
                "Check the token and try again."
            )
        return (
            f"MCP server at {host} requires authentication ({text})."
            f"{cred} Bind a server-side credential, then try again."
        )
    return f"Failed to reach MCP server at {host}: {text[:300]}"


def _looks_like_auth_failure(text: str) -> bool:
    lowered = text.lower()
    return any(
        token in lowered
        for token in (
            "401",
            "403",
            "unauthorized",
            "authentication required",
            "invalid_token",
            "invalid token",
            "www-authenticate",
            "oauth",
        )
    )


def streamable_http_required_message(url: str) -> str | None:
    """Return a 422 hint when SSE was chosen for a Streamable HTTP MCP URL."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    path = (parts.path or "").rstrip("/")
    if host != GROWW_MCP_HOST and path != "/mcp" and not path.endswith("/mcp"):
        return None
    shown = host or url
    return (
        f"{shown} at this URL uses Streamable HTTP, not SSE (legacy). "
        "Switch Transport to Streamable HTTP and try Enumerate again."
    )


def _parse_sse_endpoint(text: str) -> str | None:
    event: str | None = None
    data_lines: list[str] = []
    for raw in text.splitlines():
        line = raw.rstrip("\r")
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
        elif line == "":
            if event == "endpoint":
                endpoint = "\n".join(data_lines).strip()
                if endpoint:
                    return endpoint
            event = None
            data_lines = []
    if event == "endpoint":
        endpoint = "\n".join(data_lines).strip()
        return endpoint or None
    return None


def _capabilities_from_tools(tools: list[Any]) -> list[Capability]:
    capabilities: list[Capability] = []
    for item in tools[:MAX_TOOLS]:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        schema = item.get("inputSchema") or item.get("input_schema") or {}
        if not isinstance(schema, dict):
            schema = {}
        capabilities.append(
            Capability(
                name=name,
                description=str(item.get("description") or ""),
                approval_required=is_destructive_name(name),
                input_schema=schema,
            )
        )
    return capabilities


def _body_snippet(response: httpx.Response) -> str:
    text = (response.text or "").strip()
    if not text:
        return response.reason_phrase or ""
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return text[:200]
    if isinstance(parsed, str):
        return parsed[:200]
    if isinstance(parsed, dict):
        for key in ("error_description", "detail", "message", "error"):
            value = parsed.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:200]
            if isinstance(value, dict):
                nested = value.get("message") or value.get("description")
                if isinstance(nested, str) and nested.strip():
                    return nested.strip()[:200]
    return text[:200]


def _http_status_message(
    response: httpx.Response,
    *,
    url: str,
    has_credential: bool,
) -> str:
    host = urlsplit(url).hostname or url
    snippet = _body_snippet(response)
    status = response.status_code
    suffix = f": {snippet}" if snippet else ""
    if status in {401, 403}:
        if has_credential:
            return (
                f"MCP server at {host} rejected the credential ({status}{suffix}). "
                "Check the token and try again."
            )
        return (
            f"MCP server at {host} requires authentication ({status}{suffix}). "
            "No credential is bound. Bind a server-side credential, then try again."
        )
    if status == 404:
        return f"MCP endpoint not found at {url} (404{suffix})."
    if status >= 500:
        return f"MCP server at {host} returned {status}{suffix}."
    return f"MCP server at {host} returned {status}{suffix}."


def _parse_jsonrpc_payload(response: httpx.Response) -> dict[str, Any]:
    text = response.text or ""
    ctype = (response.headers.get("content-type") or "").lower()
    if "text/event-stream" in ctype:
        chunks: list[str] = []
        for line in text.splitlines():
            if line.startswith("data:"):
                chunks.append(line[5:].lstrip())
        text = "\n".join(chunks)
    if not text.strip():
        return {}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProviderValidationError(
            f"MCP server returned a non-JSON body ({response.status_code})"
        ) from exc
    if not isinstance(payload, dict):
        raise ProviderValidationError("MCP server returned an invalid JSON-RPC payload")
    return payload


async def _post_allowlisted(
    client: httpx.AsyncClient,
    url: str,
    *,
    payload: dict[str, Any],
    headers: dict[str, str],
    validate_url: Callable[[str], Awaitable[Any]],
) -> tuple[httpx.Response, str]:
    current = url
    for _ in range(_MAX_REDIRECTS):
        await validate_url(current)
        response = await client.post(current, json=payload, headers=headers)
        if response.is_redirect:
            location = response.headers.get("location")
            if not location:
                raise ProviderValidationError(
                    f"MCP server redirected without a Location header ({response.status_code})"
                )
            nxt = urljoin(current, location)
            if urlsplit(nxt).scheme != "https":
                raise ProviderValidationError(
                    "MCP server redirected to a non-HTTPS URL; refusing to follow"
                )
            current = nxt
            continue
        return response, current
    raise ProviderValidationError("MCP server redirected too many times")


async def _jsonrpc_list_tools(
    client: httpx.AsyncClient,
    url: str,
    *,
    request_headers: dict[str, str],
    has_credential: bool,
    validate_url: Callable[[str], Awaitable[Any]],
) -> list[Any]:
    rpc_id = 0

    def next_id() -> int:
        nonlocal rpc_id
        rpc_id += 1
        return rpc_id

    init_payload = {
        "jsonrpc": "2.0",
        "id": next_id(),
        "method": "initialize",
        "params": {
            "protocolVersion": _PROTOCOL,
            "capabilities": {},
            "clientInfo": _CLIENT_INFO,
        },
    }
    response, current_url = await _post_allowlisted(
        client,
        url,
        payload=init_payload,
        headers=request_headers,
        validate_url=validate_url,
    )
    if response.status_code >= 400:
        raise ProviderValidationError(
            _http_status_message(
                response, url=current_url, has_credential=has_credential
            )
        )
    session_id = response.headers.get("mcp-session-id")
    if session_id:
        request_headers["Mcp-Session-Id"] = session_id
    init_body = _parse_jsonrpc_payload(response)
    if "error" in init_body:
        raise ProviderValidationError(
            humanize_outbound_error(
                RuntimeError(str(init_body["error"])),
                url=current_url,
                has_credential=has_credential,
            )
        )
    result = init_body.get("result")
    if isinstance(result, dict):
        protocol = result.get("protocolVersion")
        if isinstance(protocol, str) and protocol.strip():
            request_headers["MCP-Protocol-Version"] = protocol.strip()
    else:
        request_headers["MCP-Protocol-Version"] = _PROTOCOL

    notify_response, current_url = await _post_allowlisted(
        client,
        current_url,
        payload={"jsonrpc": "2.0", "method": "notifications/initialized"},
        headers=request_headers,
        validate_url=validate_url,
    )
    if notify_response.status_code >= 400:
        raise ProviderValidationError(
            _http_status_message(
                notify_response, url=current_url, has_credential=has_credential
            )
        )

    list_response, current_url = await _post_allowlisted(
        client,
        current_url,
        payload={
            "jsonrpc": "2.0",
            "id": next_id(),
            "method": "tools/list",
            "params": {},
        },
        headers=request_headers,
        validate_url=validate_url,
    )
    if list_response.status_code >= 400:
        raise ProviderValidationError(
            _http_status_message(
                list_response, url=current_url, has_credential=has_credential
            )
        )
    listed = _parse_jsonrpc_payload(list_response)
    if "error" in listed:
        raise ProviderValidationError(f"MCP tools/list failed: {listed['error']}")
    tools = (
        (listed.get("result") or {}).get("tools")
        if isinstance(listed.get("result"), dict)
        else None
    )
    if not isinstance(tools, list):
        raise ProviderValidationError("MCP server did not return a tools/list result")
    return tools


async def discover_streamable_http_tools(
    url: str,
    *,
    headers: Mapping[str, str],
    timeout_seconds: float,
    validate_url: Callable[[str], Awaitable[Any]],
) -> list[Capability]:
    """Initialize + tools/list over Streamable HTTP from the Atlas backend."""
    has_credential = bool(headers)
    request_headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        **dict(headers),
    }
    timeout = httpx.Timeout(timeout_seconds)
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            tools = await _jsonrpc_list_tools(
                client,
                url,
                request_headers=request_headers,
                has_credential=has_credential,
                validate_url=validate_url,
            )
    except ProviderValidationError:
        raise
    except Exception as exc:
        logger.warning("mcp discover failed url=%s error=%s", url, exc)
        raise ProviderValidationError(
            humanize_outbound_error(exc, url=url, has_credential=has_credential)
        ) from exc
    return _capabilities_from_tools(tools)


async def discover_sse_tools(
    url: str,
    *,
    headers: Mapping[str, str],
    timeout_seconds: float,
    validate_url: Callable[[str], Awaitable[Any]],
) -> list[Capability]:
    """Legacy SSE inspect over httpx. Never imports the Agno MCP client."""
    has_credential = bool(headers)
    mismatch = streamable_http_required_message(url)
    if mismatch:
        raise ProviderValidationError(mismatch)
    get_headers = {"Accept": "text/event-stream", **dict(headers)}
    timeout = httpx.Timeout(timeout_seconds)
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            await validate_url(url)
            response = await client.get(url, headers=get_headers)
            if response.status_code in {404, 405, 406}:
                host = urlsplit(url).hostname or url
                raise ProviderValidationError(
                    f"{host} did not speak SSE (legacy) at this URL. "
                    "Switch Transport to Streamable HTTP and try Enumerate again."
                )
            if response.status_code >= 400:
                raise ProviderValidationError(
                    _http_status_message(
                        response, url=url, has_credential=has_credential
                    )
                )
            ctype = (response.headers.get("content-type") or "").lower()
            if "text/event-stream" not in ctype:
                host = urlsplit(url).hostname or url
                raise ProviderValidationError(
                    f"{host} did not speak SSE (legacy) at this URL. "
                    "Switch Transport to Streamable HTTP and try Enumerate again."
                )
            endpoint = _parse_sse_endpoint(response.text)
            if not endpoint:
                raise ProviderValidationError(
                    "MCP SSE server did not send an endpoint event"
                )
            post_url = urljoin(str(response.url), endpoint)
            if urlsplit(post_url).scheme != "https":
                raise ProviderValidationError(
                    "MCP SSE endpoint is not HTTPS; refusing to follow"
                )
            request_headers = {
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
                **dict(headers),
            }
            tools = await _jsonrpc_list_tools(
                client,
                post_url,
                request_headers=request_headers,
                has_credential=has_credential,
                validate_url=validate_url,
            )
    except ProviderValidationError:
        raise
    except Exception as exc:
        logger.warning("mcp sse discover failed url=%s error=%s", url, exc)
        raise ProviderValidationError(
            humanize_outbound_error(exc, url=url, has_credential=has_credential)
        ) from exc
    return _capabilities_from_tools(tools)
