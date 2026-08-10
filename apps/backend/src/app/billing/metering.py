from __future__ import annotations

import math
from typing import Any


def extract_token_metrics(payload: dict[str, Any]) -> tuple[int, int]:
    """Return (input_tokens, output_tokens) from a run event or trace output."""
    metrics = payload.get("metrics")
    if not isinstance(metrics, dict):
        output = payload.get("output")
        if isinstance(output, dict):
            metrics = output.get("metrics")
    metrics = metrics if isinstance(metrics, dict) else {}
    input_tokens = int(metrics.get("input_tokens") or metrics.get("prompt_tokens") or 0)
    output_tokens = int(metrics.get("output_tokens") or metrics.get("completion_tokens") or 0)
    return input_tokens, output_tokens


def credits_for_tokens(
    *,
    input_tokens: int,
    output_tokens: int,
    credits_per_1k_input: int,
    credits_per_1k_output: int,
) -> int:
    raw = (input_tokens * credits_per_1k_input + output_tokens * credits_per_1k_output) / 1000
    return max(1, int(math.ceil(raw))) if (input_tokens or output_tokens) else 1
