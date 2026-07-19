import asyncio
import inspect
import ipaddress
import json
import re
import socket
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, TypeVar
from urllib.parse import quote, urlencode, urlparse

import httpx
from jsonschema import ValidationError as JsonSchemaValidationError
from jsonschema import validate as validate_json

try:
    from agno.approval.decorator import approval as agno_approval
    from agno.tools.decorator import tool
except ImportError:  # pragma: no cover - compatibility with older Agno releases
    agno_approval = None

    def tool(*args: Any, **kwargs: Any) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorate(function: Callable[..., Any]) -> Callable[..., Any]:
            return function

        return decorate


F = TypeVar("F", bound=Callable[..., Any])


def approval(function: F) -> F:
    """Mark a mutating tool as requiring AgentOS confirmation."""
    if agno_approval is not None:
        return agno_approval(function)  # type: ignore[no-any-return]
    try:
        return tool(requires_confirmation=True)(function)  # type: ignore[return-value]
    except TypeError:  # pragma: no cover - Agno API compatibility
        function.requires_confirmation = True
        return function


class UnsafeOutboundRequest(ValueError):
    pass


class SafeRestClient:
    def __init__(
        self,
        allowed_hosts: set[str],
        *,
        max_response_bytes: int = 1_000_000,
        timeout_seconds: float = 10,
    ) -> None:
        self.allowed_hosts = {host.lower().rstrip(".") for host in allowed_hosts}
        self.max_response_bytes = max_response_bytes
        self.timeout = httpx.Timeout(timeout_seconds)

    async def validate_url(self, url: str) -> None:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme != "https" or not host or host not in self.allowed_hosts:
            raise UnsafeOutboundRequest("Only allowlisted HTTPS hosts are permitted")
        try:
            addresses = await _resolve(host, parsed.port or 443)
        except OSError as exc:
            raise UnsafeOutboundRequest("Host could not be resolved") from exc
        for address in addresses:
            ip = ipaddress.ip_address(address)
            if not ip.is_global:
                raise UnsafeOutboundRequest("Private or non-global addresses are forbidden")

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
        allowed_methods: Sequence[str] = ("GET",),
    ) -> dict[str, Any]:
        method = method.upper()
        if method not in allowed_methods:
            raise UnsafeOutboundRequest("HTTP method is not permitted")
        await self.validate_url(url)
        body = json.dumps(json_body).encode() if json_body is not None else b""
        if len(body) > 100_000:
            raise UnsafeOutboundRequest("Request body is too large")
        safe_headers = {
            key: value
            for key, value in (headers or {}).items()
            if key.lower() not in {"host", "content-length", "connection"}
        }
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=False) as client:
            async with client.stream(
                method, url, headers=safe_headers, content=body or None
            ) as response:
                response.raise_for_status()
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > self.max_response_bytes:
                        raise UnsafeOutboundRequest("Response body is too large")
        return {
            "status": response.status_code,
            "content_type": response.headers.get("content-type"),
            "body": content.decode(errors="replace"),
        }


async def _resolve(host: str, port: int) -> set[str]:
    loop = asyncio.get_running_loop()
    records = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return {record[4][0] for record in records}


def build_read_rest_tool(
    client: SafeRestClient,
    base_url: str,
    headers: dict[str, str] | None = None,
) -> Callable[..., Awaitable[Any]]:
    async def read_rest(path: str) -> dict[str, Any]:
        """Read data from an approved tenant connector."""
        return await client.request(
            "GET",
            f"{base_url.rstrip('/')}/{path.lstrip('/')}",
            headers=headers,
        )

    return read_rest


def build_mutating_rest_tool(
    client: SafeRestClient,
    base_url: str,
    headers: dict[str, str] | None = None,
) -> Callable[..., Awaitable[Any]]:
    @approval
    async def mutate_rest(path: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Perform an approved write against a tenant connector."""
        return await client.request(
            "POST",
            f"{base_url.rstrip('/')}/{path.lstrip('/')}",
            headers=headers,
            json_body=payload,
            allowed_methods=("POST",),
        )

    return mutate_rest


def build_definition_tool(
    client: SafeRestClient,
    *,
    name: str,
    description: str,
    method: str,
    base_url: str,
    path_template: str,
    request_schema: dict[str, Any],
    headers: dict[str, str] | None = None,
    approval_required: bool = False,
) -> Callable[..., Awaitable[Any]]:
    """Build a named Agno-compatible callable from a constrained JSON schema."""
    properties = request_schema.get("properties", {})
    required = set(request_schema.get("required", []))
    path_names = set(re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", path_template))

    async def invoke(**arguments: Any) -> dict[str, Any]:
        try:
            validate_json(arguments, request_schema)
        except JsonSchemaValidationError as exc:
            raise ValueError(f"Arguments do not match the reviewed schema: {exc.message}") from exc
        path = path_template
        for parameter in path_names:
            if parameter not in arguments:
                raise ValueError(f"Missing path parameter: {parameter}")
            path = path.replace(f"{{{parameter}}}", quote(str(arguments[parameter]), safe=""))
        remaining = {key: value for key, value in arguments.items() if key not in path_names}
        url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
        body = None
        if method == "GET":
            if remaining:
                url = f"{url}?{urlencode(remaining, doseq=True)}"
        else:
            body = remaining
        return await client.request(
            method,
            url,
            headers=headers,
            json_body=body,
            allowed_methods=(method,),
        )

    annotations: dict[str, Any] = {
        "string": str,
        "number": float,
        "integer": int,
        "boolean": bool,
        "object": dict[str, Any],
        "array": list[Any],
    }
    parameters: list[inspect.Parameter] = []
    for parameter_name, schema in properties.items():
        if not parameter_name.isidentifier():
            raise ValueError(f"Tool parameter is not a valid identifier: {parameter_name}")
        default = inspect.Parameter.empty if parameter_name in required else schema.get("default")
        parameters.append(
            inspect.Parameter(
                parameter_name,
                inspect.Parameter.KEYWORD_ONLY,
                default=default,
                annotation=annotations.get(schema.get("type"), Any),
            )
        )
    invoke.__name__ = name.replace("-", "_")
    invoke.__doc__ = description or f"Call the approved {name} tenant tool."
    invoke.__signature__ = inspect.Signature(  # type: ignore[attr-defined]
        parameters=parameters,
        return_annotation=dict[str, Any],
    )
    if approval_required or method != "GET":
        return approval(invoke)
    return invoke


async def web_search(query: str) -> str:
    """Search the public web and return a bounded, source-linked result set."""
    from ddgs import DDGS

    def search() -> list[dict[str, str]]:
        rows = DDGS().text(query, max_results=5)
        return [
            {
                "title": str(row.get("title", "")),
                "url": str(row.get("href", "")),
                "summary": str(row.get("body", ""))[:1_000],
            }
            for row in rows
        ]

    return json.dumps(await asyncio.to_thread(search), ensure_ascii=False)
