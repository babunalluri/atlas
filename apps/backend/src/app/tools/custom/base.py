from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel

from app.tools.registry import SafeRestClient


class CustomToolBuilder(Protocol):
    def __call__(self, context: CustomToolContext) -> list[Callable[..., Any]]: ...


@dataclass(frozen=True, slots=True)
class CustomCapability:
    name: str
    description: str
    input_schema: dict[str, Any]
    mutating: bool = False


@dataclass(slots=True)
class CustomToolContext:
    """Narrow runtime surface provided to source-controlled custom tools."""

    client: SafeRestClient
    settings: BaseModel
    credential_value: str | None


@dataclass(frozen=True, slots=True)
class CustomToolSpec:
    key: str
    label: str
    category: str
    description: str
    settings_model: type[BaseModel]
    capabilities: tuple[CustomCapability, ...]
    build: CustomToolBuilder
    credential_provider: str | None = None
    credential_label: str | None = None
    url_fields: tuple[str, ...] = ()

    def public_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "category": self.category,
            "description": self.description,
            "credential_provider": self.credential_provider,
            "credential_label": self.credential_label,
            "settings_schema": self.settings_model.model_json_schema(),
            "capabilities": [
                {
                    "name": capability.name,
                    "description": capability.description,
                    "input_schema": capability.input_schema,
                    "mutating": capability.mutating,
                }
                for capability in self.capabilities
            ],
        }
