"""CC PBX-style starter template for editable sandboxed Python tools.

No secrets — bind a tenant credential and set base URLs in settings.
"""

CC_PBX_STARTER_SOURCE = '''\
"""Contact-center PBX style toolkit (sandbox starter).

Configure settings.base_url to your HTTPS API host (must be allowlisted).
Attach a tenant credential; the host injects it on proxied requests.
"""

from __future__ import annotations

from typing import Any


async def list_campaigns(ctx, limit: int = 20) -> Any:
    """List outbound campaigns."""
    base = str(ctx.settings.get("base_url", "")).rstrip("/")
    return await ctx.http.get(f"{base}/campaigns", params={"limit": limit})


async def get_campaign(ctx, campaign_id: str) -> Any:
    """Fetch a single campaign by id."""
    base = str(ctx.settings.get("base_url", "")).rstrip("/")
    return await ctx.http.get(f"{base}/campaigns/{campaign_id}")


async def create_campaign(ctx, name: str, dial_prefix: str = "") -> Any:
    """Create a campaign (requires approval)."""
    base = str(ctx.settings.get("base_url", "")).rstrip("/")
    payload = {"name": name, "dial_prefix": dial_prefix}
    return await ctx.http.post(f"{base}/campaigns", json=payload)


async def list_agents(ctx, page: int = 1, page_size: int = 25) -> Any:
    """List PBX agents."""
    base = str(ctx.settings.get("base_url", "")).rstrip("/")
    return await ctx.http.get(
        f"{base}/agents",
        params={"page": page, "page_size": page_size},
    )
'''

CC_PBX_DEFAULT_CAPABILITIES = [
    {
        "name": "list_campaigns",
        "description": "List outbound campaigns",
        "mutating": False,
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 20, "minimum": 1, "maximum": 100}
            },
        },
    },
    {
        "name": "get_campaign",
        "description": "Fetch a campaign by id",
        "mutating": False,
        "input_schema": {
            "type": "object",
            "properties": {"campaign_id": {"type": "string"}},
            "required": ["campaign_id"],
        },
    },
    {
        "name": "create_campaign",
        "description": "Create a campaign",
        "mutating": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "dial_prefix": {"type": "string", "default": ""},
            },
            "required": ["name"],
        },
    },
    {
        "name": "list_agents",
        "description": "List PBX agents",
        "mutating": False,
        "input_schema": {
            "type": "object",
            "properties": {
                "page": {"type": "integer", "default": 1},
                "page_size": {"type": "integer", "default": 25},
            },
        },
    },
]

CC_PBX_DEFAULT_SETTINGS = {
    "base_url": "https://api.example.com/v1",
}
