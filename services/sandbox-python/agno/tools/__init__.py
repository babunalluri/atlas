"""Tiny Agno tools shim — not a full Agent toolkit runtime."""

from __future__ import annotations

from typing import Any


class BaseToolkit:
    """Store a tools list; runner injects ``.pv`` / ``.settings`` on invoke."""

    def __init__(
        self,
        *args: Any,
        name: str | None = None,
        tools: list[Any] | None = None,
        **kwargs: Any,
    ) -> None:
        self.name = name or self.__class__.__name__
        self.tools: list[Any] = list(tools or [])
        self.pv: dict[str, Any] = {}
        self.settings: dict[str, Any] = {}
        self._extra = {"args": args, "kwargs": kwargs}

    def register(self, func: Any) -> Any:
        self.tools.append(func)
        return func


Toolkit = BaseToolkit

__all__ = ["BaseToolkit", "Toolkit"]
