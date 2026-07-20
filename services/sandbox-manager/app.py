"""Local sandbox-manager sidecar — owns Docker socket; backend never mounts it."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger("sandbox-manager")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Atlas Sandbox Manager", version="1.0.0")

DOCKER_BIN = os.environ.get("DOCKER_BIN", "docker")
DEFAULT_IMAGE = os.environ.get("SANDBOX_PYTHON_IMAGE", "atlas-sandbox-python:local")
MAX_CONCURRENT = int(os.environ.get("SANDBOX_MAX_CONCURRENT", "8"))
_semaphore = asyncio.Semaphore(MAX_CONCURRENT)


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
    return {"status": "ok"}


@app.post("/v1/runs", response_model=RunResponse)
async def create_run(body: RunRequest) -> RunResponse:
    async with _semaphore:
        try:
            return await _run_container(body)
        except asyncio.TimeoutError:
            return RunResponse(
                ok=False, error="Sandbox wall-clock timeout", run_id=body.run_id
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("sandbox run failed run_id=%s", body.run_id)
            return RunResponse(ok=False, error=str(exc)[:500], run_id=body.run_id)


async def _run_container(body: RunRequest) -> RunResponse:
    cmd = [
        DOCKER_BIN,
        "run",
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
        "/tmp:rw,noexec,nosuid,size=64m",
        "--tmpfs",
        "/sandbox/work:rw,noexec,nosuid,size=32m",
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
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


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
    try:
        response = await http.post(
            proxy_base_url,
            json={
                "method": method,
                "url": url,
                "headers": params.get("headers") or {},
                "json": params.get("json"),
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
        raise HTTPException(status_code=503, detail="docker binary not found") from exc
