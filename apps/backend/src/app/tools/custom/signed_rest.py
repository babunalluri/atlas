from __future__ import annotations

from typing import Any
from urllib.parse import quote

from pydantic import BaseModel, Field, field_validator

from app.tools.custom.base import CustomCapability, CustomToolContext, CustomToolSpec


class SignedRestSettings(BaseModel):
    base_url: str = Field(max_length=2048)
    read_path: str = Field(default="/resources/{resource_id}", max_length=500)
    create_path: str = Field(default="/resources", max_length=500)
    credential_header: str = Field(default="Authorization", pattern=r"^[A-Za-z0-9-]{1,100}$")
    credential_prefix: str = Field(default="Bearer ", max_length=100)

    @field_validator("base_url")
    @classmethod
    def normalize_base_url(cls, value: str) -> str:
        if not value.startswith("https://"):
            raise ValueError("base_url must use HTTPS")
        return value.rstrip("/")

    @field_validator("read_path", "create_path")
    @classmethod
    def safe_path(cls, value: str) -> str:
        if "://" in value or value.startswith("//") or ".." in value.split("/"):
            raise ValueError("API paths must be relative and cannot traverse")
        return "/" + value.lstrip("/")


def build_signed_rest_tools(context: CustomToolContext) -> list[Any]:
    settings = SignedRestSettings.model_validate(context.settings)
    if not context.credential_value:
        raise ValueError("Signed REST API requires a tenant credential")
    headers = {
        settings.credential_header: settings.credential_prefix + context.credential_value
    }

    async def get_resource(resource_id: str) -> dict[str, Any]:
        """Read one resource from the configured API."""

        path = settings.read_path.replace(
            "{resource_id}", quote(resource_id, safe="")
        )
        return await context.client.request(
            "GET",
            f"{settings.base_url}{path}",
            headers=headers,
        )

    async def create_resource(payload: dict[str, Any]) -> dict[str, Any]:
        """Create a resource through the configured API."""

        return await context.client.request(
            "POST",
            f"{settings.base_url}{settings.create_path}",
            headers=headers,
            json_body=payload,
            allowed_methods=("POST",),
        )

    return [get_resource, create_resource]


SIGNED_REST_SPEC = CustomToolSpec(
    key="signed_rest_api",
    label="Signed REST API",
    category="API",
    description=(
        "Source-controlled example for an authenticated API with reviewed read "
        "and create operations."
    ),
    settings_model=SignedRestSettings,
    credential_provider="rest_api",
    credential_label="API token",
    url_fields=("base_url",),
    capabilities=(
        CustomCapability(
            name="get_resource",
            description="Read one resource from the configured API.",
            input_schema={
                "type": "object",
                "properties": {"resource_id": {"type": "string"}},
                "required": ["resource_id"],
            },
        ),
        CustomCapability(
            name="create_resource",
            description="Create a resource through the configured API.",
            input_schema={
                "type": "object",
                "properties": {"payload": {"type": "object"}},
                "required": ["payload"],
            },
            mutating=True,
        ),
    ),
    build=build_signed_rest_tools,
)
