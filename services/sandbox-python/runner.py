"""Guest runner: load tenant source and invoke one capability."""

from __future__ import annotations

import asyncio
import json
import sys
import traceback
from pathlib import Path
from typing import Any

# atlas_sdk is on PYTHONPATH via /sandbox
from atlas_sdk import Context, emit_result  # type: ignore[import-not-found]


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
        func = namespace.get(capability)
        if not callable(func):
            emit_result(ok=False, error=f"Capability not found: {capability}")
            return 1
        ctx = Context(settings)
        result = asyncio.run(func(ctx, **arguments))
        emit_result(ok=True, value=result)
        return 0
    except Exception as exc:  # noqa: BLE001
        emit_result(ok=False, error=f"{type(exc).__name__}: {exc}\n{traceback.format_exc()[-1500:]}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
