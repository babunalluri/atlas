"""Guest runner: load tenant source and invoke one capability."""

from __future__ import annotations

import asyncio
import inspect
import json
import sys
import traceback
from pathlib import Path
from typing import Any, Callable

# atlas_sdk is on PYTHONPATH via /sandbox
from atlas_sdk import Context, emit_result  # type: ignore[import-not-found]

_TOOLKIT_BASE_NAMES = frozenset({"BaseToolkit", "Toolkit"})


def main() -> int:
    raw = sys.stdin.readline()
    if not raw:
        emit_result(ok=False, error="Missing RunStart payload")
        return 1
    start = json.loads(raw)
    if start.get("method") != "RunStart":
        # Allow bare params document for simpler managers.
        params = start.get("params", start)
    else:
        params = start["params"]

    source = str(params.get("source_code", ""))
    settings = dict(params.get("settings") or {})
    capability = str(params.get("capability", ""))
    arguments = dict(params.get("arguments") or {})

    module_path = Path("/sandbox/work/tool.py")
    module_path.write_text(source, encoding="utf-8")

    try:
        namespace: dict[str, Any] = {"__name__": "tool"}
        code = compile(source, str(module_path), "exec")
        exec(code, namespace)  # noqa: S102 - intentional guest load of validated source
        ctx = Context(settings)
        result = asyncio.run(_invoke_capability(namespace, capability, ctx, arguments, settings))
        emit_result(ok=True, value=result)
        return 0
    except Exception as exc:  # noqa: BLE001
        emit_result(ok=False, error=f"{type(exc).__name__}: {exc}\n{traceback.format_exc()[-1500:]}")
        return 1


async def _invoke_capability(
    namespace: dict[str, Any],
    capability: str,
    ctx: Context,
    arguments: dict[str, Any],
    settings: dict[str, Any],
) -> Any:
    func = namespace.get(capability)
    if _is_invokable_function(func):
        return await _call_maybe_async(func, ctx, arguments)

    method, toolkit_cls = _find_toolkit_method(namespace, capability)
    if method is None or toolkit_cls is None:
        raise RuntimeError(
            f"Capability not found: {capability}. "
            "Expected a top-level function or a public method on a BaseToolkit subclass."
        )

    try:
        instance = toolkit_cls()
    except TypeError as exc:
        raise RuntimeError(
            f"Could not instantiate {toolkit_cls.__name__} for capability "
            f"{capability!r}: BaseToolkit subclasses must be constructible with no "
            f"required args ({exc})"
        ) from exc

    # Ozonetel/supertools-style toolkits read settings from .pv; also set .settings.
    instance.pv = settings
    instance.settings = settings
    bound = getattr(instance, capability)
    return await _call_maybe_async(bound, None, arguments)


def _is_invokable_function(func: Any) -> bool:
    return callable(func) and not isinstance(func, type)


def _is_toolkit_class(cls: type) -> bool:
    return any(base.__name__ in _TOOLKIT_BASE_NAMES for base in cls.__mro__)


def _find_toolkit_method(
    namespace: dict[str, Any], capability: str
) -> tuple[Any, type | None]:
    if not capability or capability.startswith("_"):
        return None, None
    for obj in namespace.values():
        if not isinstance(obj, type) or not _is_toolkit_class(obj):
            continue
        # Prefer methods defined directly on the subclass.
        if capability in obj.__dict__:
            attr = obj.__dict__[capability]
            if isinstance(attr, (staticmethod, classmethod)):
                continue
            if callable(attr):
                return attr, obj
    return None, None


async def _call_maybe_async(
    func: Callable[..., Any],
    ctx: Context | None,
    arguments: dict[str, Any],
) -> Any:
    """Call async or sync capability; pass ctx only for top-level functions."""

    async def _run() -> Any:
        if ctx is not None:
            # Top-level capabilities receive Context as first arg.
            if inspect.iscoroutinefunction(func):
                return await func(ctx, **arguments)
            result = func(ctx, **arguments)
        else:
            # Bound toolkit methods: (self, **arguments) — self already bound.
            if inspect.iscoroutinefunction(func):
                return await func(**arguments)
            result = func(**arguments)
        if inspect.isawaitable(result):
            return await result
        return result

    return await _run()


if __name__ == "__main__":
    raise SystemExit(main())
