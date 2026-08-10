"""Local sandbox-manager sidecar — owns Docker socket; backend never mounts it."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shlex
import shutil
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger("sandbox-manager")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Atlas Sandbox Manager", version="1.0.0")

DOCKER_BIN = os.environ.get("DOCKER_BIN", "docker")
DEFAULT_IMAGE = os.environ.get("SANDBOX_PYTHON_IMAGE", "atlas-sandbox-python:local")
ALLOWED_IMAGES = {
    item.strip()
    for item in os.environ.get("SANDBOX_ALLOWED_IMAGES", DEFAULT_IMAGE).split(",")
    if item.strip()
}
MAX_CONCURRENT = int(os.environ.get("SANDBOX_MAX_CONCURRENT", "8"))
REDIS_URL = os.environ.get("REDIS_URL", "").strip()
INTERNAL_TOKEN = os.environ.get("SANDBOX_INTERNAL_TOKEN", "").strip()
ALLOWED_PROXY_PREFIXES = tuple(
    p.strip()
    for p in os.environ.get(
        "SANDBOX_ALLOWED_PROXY_PREFIXES", "http://backend:7777/"
    ).split(",")
    if p.strip()
)
GLOBAL_SLOT_KEY = "atlas:sandbox:global:slots"

_local_semaphore = asyncio.Semaphore(MAX_CONCURRENT)
_redis: Any | None = None


def _docker_missing_message() -> str:
    return (
        f"Sandbox manager cannot find docker CLI at {DOCKER_BIN!r}. "
        "Rebuild the sandbox-manager image (it must include the Docker CLI)."
    )


async def _get_redis() -> Any | None:
    global _redis
    if not REDIS_URL or REDIS_URL in {"memory://", "memory", "none", "off"}:
        return None
    if _redis is not None:
        return _redis
    try:
        from redis.asyncio import Redis

        client = Redis.from_url(REDIS_URL, decode_responses=True)
        await client.ping()
        _redis = client
        return _redis
    except Exception as exc:  # noqa: BLE001
        logger.warning("sandbox-manager redis unavailable: %s", exc)
        return None


class _Slot:
    """Async context manager for local or Redis-backed global concurrency."""

    def __init__(self) -> None:
        self._redis_held = False
        self._redis_client: Any | None = None
        self._local_cm: Any | None = None

    async def __aenter__(self) -> None:
        client = await _get_redis()
        if client is not None:
            # Always refresh TTL on acquire so sustained load cannot expire the key
            # while slots are still held (which would reset the counter).
            script = """
            local count = redis.call('INCR', KEYS[1])
            if count > tonumber(ARGV[2]) then
              redis.call('DECR', KEYS[1])
              return 0
            end
            redis.call('EXPIRE', KEYS[1], ARGV[1])
            return 1
            """
            allowed = await client.eval(script, 1, GLOBAL_SLOT_KEY, 600, MAX_CONCURRENT)
            if int(allowed) != 1:
                raise HTTPException(status_code=429, detail="Sandbox capacity exceeded")
            self._redis_held = True
            self._redis_client = client
            return
        self._local_cm = _local_semaphore
        await self._local_cm.acquire()

    async def __aexit__(self, *args: object) -> None:
        if self._redis_held and self._redis_client is not None:
            try:
                val = await self._redis_client.decr(GLOBAL_SLOT_KEY)
                if val < 0:
                    await self._redis_client.set(GLOBAL_SLOT_KEY, 0, ex=600)
            except Exception as exc:  # noqa: BLE001
                logger.warning("failed to release sandbox slot: %s", exc)
            return
        if self._local_cm is not None:
            self._local_cm.release()


class RunRequest(BaseModel):
    run_id: str
    source_code: str = Field(min_length=1, max_length=80_000)
    settings: dict[str, Any] = Field(default_factory=dict)
    capability: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int = Field(default=30, ge=1, le=120)
    image: str = DEFAULT_IMAGE
    proxy_base_url: str


class RunResponse(BaseModel):
    ok: bool
    value: Any = None
    error: str | None = None
    run_id: str


@app.get("/health")
async def health() -> dict[str, str]:
    if shutil.which(DOCKER_BIN) is None:
        raise HTTPException(status_code=503, detail=_docker_missing_message())
    return {"status": "ok"}


def _require_inbound_auth(authorization: str | None) -> None:
    if not INTERNAL_TOKEN:
        raise HTTPException(
            status_code=503,
            detail="SANDBOX_INTERNAL_TOKEN is not configured on sandbox-manager",
        )
    expected = f"Bearer {INTERNAL_TOKEN}"
    if not authorization or authorization != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


def _validate_run_request(body: RunRequest) -> None:
    if body.image not in ALLOWED_IMAGES:
        raise HTTPException(status_code=400, detail="Sandbox image is not allowlisted")
    if not any(body.proxy_base_url.startswith(prefix) for prefix in ALLOWED_PROXY_PREFIXES):
        raise HTTPException(status_code=400, detail="proxy_base_url is not allowlisted")


@app.post("/v1/runs", response_model=RunResponse)
async def create_run(
    body: RunRequest,
    authorization: str | None = Header(default=None),
) -> RunResponse:
    _require_inbound_auth(authorization)
    _validate_run_request(body)
    if shutil.which(DOCKER_BIN) is None:
        return RunResponse(
            ok=False, error=_docker_missing_message(), run_id=body.run_id
        )
    try:
        async with _Slot():
            try:
                return await _run_container(body)
            except TimeoutError:
                return RunResponse(
                    ok=False, error="Sandbox wall-clock timeout", run_id=body.run_id
                )
            except FileNotFoundError:
                return RunResponse(
                    ok=False, error=_docker_missing_message(), run_id=body.run_id
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("sandbox run failed run_id=%s", body.run_id)
                return RunResponse(ok=False, error=str(exc)[:500], run_id=body.run_id)
    except HTTPException as exc:
        return RunResponse(ok=False, error=str(exc.detail), run_id=body.run_id)


async def _force_remove_container(name: str) -> None:
    kill = await asyncio.create_subprocess_exec(
        DOCKER_BIN,
        "rm",
        "-f",
        name,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await kill.wait()


async def _run_container(body: RunRequest) -> RunResponse:
    # Stable name so timeout can docker rm -f even if the CLI client is dead.
    safe_id = "".join(ch if ch.isalnum() else "-" for ch in body.run_id)[:48]
    container_name = f"atlas-sbx-{safe_id}"
    await _force_remove_container(container_name)
    cmd = [
        DOCKER_BIN,
        "run",
        "--name",
        container_name,
        "--rm",
        "-i",
        "--network",
        "none",
        "--memory",
        "512m",
        "--cpus",
        "0.5",
        "--pids-limit",
        "64",
        "--read-only",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=64m,uid=10001,gid=10001",
        "--tmpfs",
        "/sandbox/work:rw,noexec,nosuid,size=32m,uid=10001,gid=10001",
        "--security-opt",
        "no-new-privileges",
        "--user",
        "10001:10001",
        body.image,
    ]
    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert process.stdin and process.stdout

    start = {
        "jsonrpc": "2.0",
        "method": "RunStart",
        "params": {
            "source_code": body.source_code,
            "settings": body.settings,
            "capability": body.capability,
            "arguments": body.arguments,
        },
    }
    process.stdin.write((json.dumps(start) + "\n").encode())
    await process.stdin.drain()

    try:
        return await asyncio.wait_for(
            _pump_ipc(process, body),
            timeout=body.timeout_seconds,
        )
    except TimeoutError:
        if process.returncode is None:
            process.kill()
            with contextlib.suppress(ProcessLookupError):
                await process.wait()
        await _force_remove_container(container_name)
        raise
    finally:
        if process.returncode is None:
            process.kill()
            with contextlib.suppress(ProcessLookupError):
                await process.wait()
        await _force_remove_container(container_name)


async def _pump_ipc(
    process: asyncio.subprocess.Process, body: RunRequest
) -> RunResponse:
    assert process.stdin and process.stdout
    async with httpx.AsyncClient(timeout=20) as http:
        while True:
            line = await process.stdout.readline()
            if not line:
                stderr = b""
                if process.stderr:
                    stderr = await process.stderr.read()
                detail = stderr.decode("utf-8", errors="replace")[-800:]
                return RunResponse(
                    ok=False,
                    error=f"Sandbox exited without RunResult. {detail}".strip(),
                    run_id=body.run_id,
                )
            try:
                message = json.loads(line.decode("utf-8"))
            except json.JSONDecodeError:
                continue
            method = message.get("method")
            msg_id = message.get("id")
            params = message.get("params") or {}
            if method == "HttpProxy":
                result = await _proxy(http, body.proxy_base_url, params)
                reply = {"jsonrpc": "2.0", "id": msg_id, "result": result}
                process.stdin.write((json.dumps(reply) + "\n").encode())
                await process.stdin.drain()
            elif method == "RunResult":
                reply = {"jsonrpc": "2.0", "id": msg_id, "result": {"ack": True}}
                process.stdin.write((json.dumps(reply) + "\n").encode())
                await process.stdin.drain()
                process.stdin.close()
                await process.wait()
                return RunResponse(
                    ok=bool(params.get("ok")),
                    value=params.get("value"),
                    error=params.get("error"),
                    run_id=body.run_id,
                )


async def _proxy(
    http: httpx.AsyncClient, proxy_base_url: str, params: dict[str, Any]
) -> dict[str, Any]:
    method = str(params.get("method", "GET")).upper()
    url = str(params.get("url", ""))
    query = params.get("params") or {}
    if query:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}{urlencode({str(k): str(v) for k, v in query.items()})}"
    headers = {"X-Sandbox-Internal-Token": os.environ.get("SANDBOX_INTERNAL_TOKEN", "")}
    try:
        response = await http.post(
            proxy_base_url,
            headers=headers,
            json={
                "method": method,
                "url": url,
                "headers": params.get("headers") or {},
                "json": params.get("json"),
                "form": params.get("form"),
            },
        )
        if response.status_code >= 400:
            return {
                "ok": False,
                "error": f"Proxy callback HTTP {response.status_code}",
                "status_code": response.status_code,
            }
        return response.json()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "status_code": 502}


@app.get("/v1/diagnose")
async def diagnose() -> dict[str, Any]:
    try:
        proc = await asyncio.create_subprocess_exec(
            DOCKER_BIN,
            "version",
            "--format",
            "{{.Server.Version}}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate()
        if proc.returncode != 0:
            raise HTTPException(
                status_code=503,
                detail=err.decode()[:300] or "docker unavailable",
            )
        return {
            "docker": True,
            "server_version": out.decode().strip(),
            "image": DEFAULT_IMAGE,
            "hint": f"Ensure image exists: docker build -t {shlex.quote(DEFAULT_IMAGE)} ...",
        }
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=_docker_missing_message()) from exc
