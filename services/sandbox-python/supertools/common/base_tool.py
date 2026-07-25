"""Minimal ``supertools.common.base_tool.BaseToolkit`` for Ozonetel-style toolkits."""

from __future__ import annotations

from typing import Any


class BaseToolkit:
    """Store tools list and settings (``.pv`` / ``.settings`` injected by runner)."""

    def __init__(
        self,
        name: str | None = None,
        tools: list[Any] | None = None,
        **kwargs: Any,
    ) -> None:
        self.name = name or self.__class__.__name__
        self.tools: list[Any] = list(tools or [])
        self.pv: dict[str, Any] = {}
        self.settings: dict[str, Any] = {}
        self._extra = kwargs

    def register(self, func: Any) -> Any:
        self.tools.append(func)
        return func


__all__ = ["BaseToolkit"]
