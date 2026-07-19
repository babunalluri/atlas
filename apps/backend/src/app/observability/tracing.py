import re
from collections.abc import Mapping
from typing import Any

SENSITIVE_KEYS = re.compile(
    r"(authorization|api[-_]?key|token|secret|password|credential|cookie)", re.IGNORECASE
)
SAFE_METRIC_KEYS = {
    "input_tokens",
    "output_tokens",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
}


def _sensitive_key(key: object) -> bool:
    value = str(key)
    return value.casefold() not in SAFE_METRIC_KEYS and SENSITIVE_KEYS.search(value) is not None


def redact(value: Any, *, depth: int = 0) -> Any:
    if depth > 8:
        return "[TRUNCATED]"
    if isinstance(value, Mapping):
        return {
            str(key): "[REDACTED]"
            if _sensitive_key(key)
            else redact(item, depth=depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item, depth=depth + 1) for item in value[:100]]
    if isinstance(value, str) and len(value) > 10_000:
        return value[:10_000] + "…"
    return value


def trace_metadata(
    *, tenant_id: str, agent_id: str, version_id: str, session_id: str
) -> dict[str, str]:
    return {
        "tenant_id": tenant_id,
        "agent_id": agent_id,
        "agent_version_id": version_id,
        "session_id": session_id,
    }
