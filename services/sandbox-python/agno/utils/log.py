"""Minimal ``agno.utils.log`` shim backed by stdlib logging."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("agno.sandbox")
if not logger.handlers:
    # Guest stdout is captured for diagnostics; keep noise low.
    logger.addHandler(logging.NullHandler())
logger.setLevel(logging.INFO)


def log_info(*args: Any, **kwargs: Any) -> None:
    logger.info(*args, **kwargs)


def log_error(*args: Any, **kwargs: Any) -> None:
    logger.error(*args, **kwargs)


def log_warning(*args: Any, **kwargs: Any) -> None:
    logger.warning(*args, **kwargs)


def log_debug(*args: Any, **kwargs: Any) -> None:
    logger.debug(*args, **kwargs)


__all__ = [
    "log_debug",
    "log_error",
    "log_info",
    "log_warning",
    "logger",
]
