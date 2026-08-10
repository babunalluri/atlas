"""Atlas wrappers for Agno background runs, SSE resume, and cancel.

Keeps native AgentOS /agents/.../resume routes blocked; product traffic uses
/v1/... and /public/... paths with tenant-scoped factories.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

EventHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


def _event_tools():
    from app.agent_runtime.agent_os import _event_payload, _normalize_event_name

    return _event_payload, _normalize_event_name


def _parse_agno_sse_chunk(chunk: str) -> dict[str, Any] | None:
    """Extract JSON payload from an Agno `event:/data:` SSE frame."""
    _, normalize = _event_tools()
    data_lines: list[str] = []
    for line in chunk.splitlines():
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    if not data_lines:
        return None
    raw = "\n".join(data_lines).strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"event": "RunContent", "content": raw}
    if isinstance(parsed, dict):
        return normalize(parsed)
    return {"event": "RunContent", "content": str(parsed)}


def _format_atlas_sse(payload: dict[str, Any]) -> bytes:
    event_index = payload.get("event_index")
    body = json.dumps(payload, default=str)
    if event_index is not None:
        return f"id: {event_index}\ndata: {body}\n\n".encode()
    return f"data: {body}\n\n".encode()


async def iter_component_sse(
    component: Any,
    message: str,
    *,
    user_id: str,
    session_id: str,
    background: bool = False,
    run_id: str | None = None,
    event_handler: EventHandler | None = None,
    wall_seconds: int | None = None,
    session_state: dict[str, Any] | None = None,
) -> AsyncIterator[bytes]:
    """Stream a component run as Atlas SSE frames (optional Agno background)."""
    import asyncio
    import inspect

    from app.core.settings import get_settings

    event_payload, _ = _event_tools()
    run_kwargs: dict[str, Any] = {
        "stream": True,
        "stream_events": True,
        "user_id": user_id,
        "session_id": session_id,
        "background": background,
    }
    if run_id:
        run_kwargs["run_id"] = run_id
    if session_state is not None:
        run_kwargs["session_state"] = session_state

    limit = wall_seconds if wall_seconds is not None else get_settings().agent_run_wall_seconds
    if not hasattr(component, "arun"):
        raise RuntimeError("Runtime component does not expose arun")

    arun = component.arun
    try:
        signature = inspect.signature(arun)
        if not any(p.kind == inspect.Parameter.VAR_KEYWORD for p in signature.parameters.values()):
            run_kwargs = {
                key: value for key, value in run_kwargs.items() if key in signature.parameters
            }
    except (TypeError, ValueError):
        pass

    result = arun(message, **run_kwargs)
    if not hasattr(result, "__aiter__"):
        if hasattr(result, "__await__"):
            output = await result
            payload = event_payload(output)
            if event_handler:
                payload = await event_handler(payload)
            yield _format_atlas_sse(payload)
            return
        raise RuntimeError("arun did not return an async iterator")

    terminal = False
    try:
        if background:
            async for event in result:
                if isinstance(event, (bytes, bytearray)):
                    event = event.decode()
                if isinstance(event, str):
                    payload = _parse_agno_sse_chunk(event)
                    if payload is None:
                        continue
                else:
                    payload = event_payload(event)
                terminal = terminal or payload.get("event") in {
                    "RunCompleted",
                    "RunError",
                    "RunCancelled",
                    "RunPaused",
                }
                if event_handler:
                    payload = await event_handler(payload)
                yield _format_atlas_sse(payload)
        else:
            async with asyncio.timeout(limit):
                async for event in result:
                    if isinstance(event, (bytes, bytearray)):
                        event = event.decode()
                    if isinstance(event, str):
                        payload = _parse_agno_sse_chunk(event)
                        if payload is None:
                            continue
                    else:
                        payload = event_payload(event)
                    terminal = terminal or payload.get("event") in {
                        "RunCompleted",
                        "RunError",
                        "RunCancelled",
                        "RunPaused",
                    }
                    if event_handler:
                        payload = await event_handler(payload)
                    yield _format_atlas_sse(payload)
    except TimeoutError:
        payload = {
            "event": "RunError",
            "error": f"Agent run exceeded {limit}s wall clock",
        }
        if event_handler:
            payload = await event_handler(payload)
        yield _format_atlas_sse(payload)
        return

    if not terminal:
        payload = {"event": "RunCompleted"}
        if event_handler:
            payload = await event_handler(payload)
        yield _format_atlas_sse(payload)


async def iter_resume_sse(
    component: Any,
    *,
    run_id: str,
    session_id: str,
    user_id: str,
    last_event_index: int | None,
    event_handler: EventHandler | None = None,
) -> AsyncIterator[bytes]:
    """Replay/catch-up a background run using Agno's in-process event buffer."""
    from agno.os.managers import event_buffer, sse_subscriber_manager
    from agno.run.base import RunStatus

    event_payload, _ = _event_tools()
    buffer_status = event_buffer.get_run_status(run_id)

    async def _emit(payload: dict[str, Any]) -> AsyncIterator[bytes]:
        if event_handler:
            payload = await event_handler(payload)
        yield _format_atlas_sse(payload)

    if buffer_status is None:
        run_output = None
        if hasattr(component, "aget_run_output"):
            try:
                run_output = await component.aget_run_output(
                    run_id=run_id, session_id=session_id, user_id=user_id
                )
            except Exception as exc:  # noqa: BLE001
                async for frame in _emit(
                    {"event": "RunError", "error": f"Failed to fetch run: {exc}"}
                ):
                    yield frame
                return
        if run_output and getattr(run_output, "events", None):
            async for frame in _emit(
                {
                    "event": "replay",
                    "run_id": run_id,
                    "status": getattr(
                        getattr(run_output, "status", None), "value", "unknown"
                    ),
                    "total_events": len(run_output.events),
                }
            ):
                yield frame
            for idx, event in enumerate(run_output.events):
                payload = event_payload(event)
                payload["event_index"] = idx
                payload.setdefault("run_id", run_id)
                async for frame in _emit(payload):
                    yield frame
            return
        async for frame in _emit(
            {
                "event": "RunError",
                "error": f"Run {run_id} not found in buffer or database",
            }
        ):
            yield frame
        return

    finished = buffer_status in (
        RunStatus.completed,
        RunStatus.error,
        RunStatus.cancelled,
        RunStatus.paused,
    )
    if finished:
        missed = event_buffer.get_events(run_id, last_event_index=last_event_index)
        async for frame in _emit(
            {
                "event": "replay",
                "run_id": run_id,
                "status": buffer_status.value,
                "total_events": len(missed),
            }
        ):
            yield frame
        for ev_index, buffered in missed:
            payload = event_payload(buffered)
            payload["event_index"] = ev_index
            payload.setdefault("run_id", run_id)
            async for frame in _emit(payload):
                yield frame
        return

    queue = sse_subscriber_manager.subscribe(run_id)
    try:
        missed = event_buffer.get_events(run_id, last_event_index)
        last_replayed = last_event_index if last_event_index is not None else -1
        if missed:
            async for frame in _emit(
                {
                    "event": "catch_up",
                    "run_id": run_id,
                    "total_events": len(missed),
                }
            ):
                yield frame
            for ev_index, buffered in missed:
                payload = event_payload(buffered)
                payload["event_index"] = ev_index
                payload.setdefault("run_id", run_id)
                last_replayed = max(last_replayed, ev_index)
                async for frame in _emit(payload):
                    yield frame
        else:
            async for frame in _emit({"event": "subscribed", "run_id": run_id}):
                yield frame

        while True:
            item = await queue.get()
            if item is None:
                break
            if isinstance(item, tuple) and len(item) == 2:
                ev_index, buffered = item
                if ev_index is not None and ev_index <= last_replayed:
                    continue
                payload = event_payload(buffered)
                if ev_index is not None:
                    payload["event_index"] = ev_index
                    last_replayed = max(last_replayed, ev_index)
            elif isinstance(item, str):
                payload = _parse_agno_sse_chunk(item) or {
                    "event": "RunContent",
                    "content": item,
                }
            else:
                payload = event_payload(item)
            payload.setdefault("run_id", run_id)
            async for frame in _emit(payload):
                yield frame
            if payload.get("event") in {
                "RunCompleted",
                "RunError",
                "RunCancelled",
                "RunPaused",
            }:
                break
    finally:
        sse_subscriber_manager.unsubscribe(run_id, queue)


async def cancel_component_run(component: Any, run_id: str) -> bool:
    """Cancel a run via Agno's cancellation API."""
    if hasattr(component, "acancel_run"):
        return bool(await component.acancel_run(run_id=run_id))
    try:
        from agno.run.cancel import acancel_run

        await acancel_run(run_id)
        return True
    except Exception:  # noqa: BLE001
        return False


def new_run_id() -> str:
    return str(uuid.uuid4())
