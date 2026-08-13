"""Load broker snapshots through the desk team → agent → assigned tool path."""

from __future__ import annotations

import inspect
import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.factory import AgentFactoryService
from app.db.models import AgentToolBinding, TeamToolBinding
from app.db.repositories import AgentRepository, TeamRepository, ToolDefinitionRepository
from app.tenancy.context import TenantContext

DESK_TEAM_SLUGS = ("live-trading", "paper-trading")
READ_CAPABILITIES = {
    "get_holdings": ("Holdings", "desk_holdings"),
    "get_positions": ("Positions", "desk_positions"),
    "get_user_margin": ("Margin", "desk_margin"),
    "get_margins": ("Margin", "desk_margin"),
    "get_user_margins": ("Margin", "desk_margin"),
    "get_funds": ("Margin", "desk_margin"),
    "list_orders": ("Orders", "desk_orders"),
    "get_orders": ("Orders", "desk_orders"),
    "get_account_health": ("Account health", "desk_health"),
}
MUTATING_MARKERS = (
    "place_",
    "cancel_",
    "modify_",
    "create_",
    "update_",
    "delete_",
    "arm",
    "disarm",
    "publish",
    "approve",
)


class DeskSnapshotService:
    def __init__(self, session: AsyncSession, context: TenantContext) -> None:
        self.session = session
        self.context = context
        self.teams = TeamRepository(session, context)
        self.agents = AgentRepository(session, context)
        self.tools = ToolDefinitionRepository(session, context)

    async def assigned_tools(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        for team, version, binding, source in await self._iter_desk_bindings():
            definition_id = binding.tool_definition_id
            key = str(definition_id) if definition_id else f"key:{binding.tool_key}"
            if key in seen:
                continue
            seen.add(key)
            definition = (
                await self.tools.get(definition_id) if definition_id else None
            )
            rows.append(
                {
                    "id": key,
                    "slug": definition.slug if definition else (binding.tool_key or "tool"),
                    "name": definition.name if definition else (binding.tool_key or "Tool"),
                    "kind": definition.kind if definition else "builtin",
                    "active": bool(definition.active) if definition else True,
                    "published": bool(definition.published_version_id) if definition else True,
                    "connection_status": (
                        definition.connection_status if definition else "bound"
                    ),
                    "via_team": team.slug,
                    "via_team_name": team.name,
                    "via_agent": source,
                }
            )
        return rows

    async def snapshot(self) -> dict[str, Any]:
        assigned = await self.assigned_tools()
        team_meta = None
        for slug in DESK_TEAM_SLUGS:
            config = await self.teams.get_config_by_slug(slug)
            if config and config.published_version_id:
                team_meta = {
                    "id": str(config.id),
                    "slug": config.slug,
                    "name": config.name,
                }
                break
        if not assigned:
            return {
                "team": team_meta,
                "tools": [],
                "widgets": [
                    {
                        "id": "desk_broker",
                        "label": "Desk broker tools",
                        "value": "None",
                        "hint": "Assign any broker toolkit on Live trading, then refresh",
                        "group": "brokers",
                    }
                ],
                "error": None,
            }

        factory = AgentFactoryService(self.session, self.context)
        widgets: list[dict[str, Any]] = []
        errors: list[str] = []
        seen_widgets: set[str] = set()
        for team, _version, binding, source in await self._iter_desk_bindings():
            if binding.tool_definition_id is None:
                continue
            definition = await self.tools.get(binding.tool_definition_id)
            via = f"{team.name} → {definition.name if definition else 'tool'}"
            if source:
                via = f"{team.name} / {source} → {definition.name if definition else 'tool'}"
            try:
                built = await factory._build_tool(binding)
            except Exception as exc:  # noqa: BLE001 — surface tool errors on the desk
                errors.append(f"{via}: {exc}")
                continue
            callables = built if isinstance(built, list) else [built]
            for fn in callables:
                name = getattr(fn, "__name__", "")
                if name not in READ_CAPABILITIES:
                    continue
                if any(marker in name for marker in MUTATING_MARKERS):
                    continue
                if not self._is_zero_arg(fn):
                    continue
                label, widget_id = READ_CAPABILITIES[name]
                row_id = f"{widget_id}_{binding.tool_definition_id}"
                if row_id in seen_widgets:
                    continue
                seen_widgets.add(row_id)
                try:
                    result = await fn()
                    widgets.append(
                        {
                            "id": row_id,
                            "label": f"{label} ({definition.name if definition else name})",
                            "value": self._summarize(result),
                            "hint": f"Via {via}",
                            "group": "brokers",
                        }
                    )
                except Exception as exc:  # noqa: BLE001
                    widgets.append(
                        {
                            "id": row_id,
                            "label": f"{label} ({definition.name if definition else name})",
                            "value": "Error",
                            "hint": f"Via {via}: {exc}"[:160],
                            "group": "brokers",
                        }
                    )

        if not widgets:
            widgets.append(
                {
                    "id": "desk_broker",
                    "label": "Desk broker tools",
                    "value": ", ".join(row["name"] for row in assigned),
                    "hint": "Tools are assigned. Refresh after the toolkit exposes holdings/positions reads.",
                    "group": "brokers",
                }
            )
        return {
            "team": team_meta,
            "tools": assigned,
            "widgets": widgets,
            "error": "; ".join(errors) if errors else None,
        }

    async def _iter_desk_bindings(
        self,
    ) -> list[tuple[Any, Any, AgentToolBinding | TeamToolBinding, str | None]]:
        found: list[tuple[Any, Any, AgentToolBinding | TeamToolBinding, str | None]] = []
        for slug in DESK_TEAM_SLUGS:
            config = await self.teams.get_config_by_slug(slug)
            if config is None or config.published_version_id is None:
                continue
            version = await self.teams.get_version(config.published_version_id)
            if version is None:
                continue
            for binding in await self.teams.bindings(version.id):
                found.append((config, version, binding, None))
            for member in await self.teams.members(version.id):
                agent_config = await self.agents.get_config(member.agent_config_id)
                if agent_config is None or agent_config.published_version_id is None:
                    continue
                agent_version = await self.agents.get_version(agent_config.published_version_id)
                if agent_version is None:
                    continue
                for binding in await self.agents.bindings(agent_version.id):
                    found.append((config, version, binding, agent_config.slug))
        return found

    @staticmethod
    def _is_zero_arg(fn: Any) -> bool:
        try:
            signature = inspect.signature(fn)
        except (TypeError, ValueError):
            return True
        for param in signature.parameters.values():
            if param.kind in (
                inspect.Parameter.VAR_POSITIONAL,
                inspect.Parameter.VAR_KEYWORD,
            ):
                continue
            if param.name in {"self", "ctx", "context"}:
                continue
            if param.default is inspect.Parameter.empty:
                return False
        return True

    @staticmethod
    def _summarize(value: Any) -> str:
        if value is None:
            return "—"
        if isinstance(value, (int, float, bool)):
            return str(value)
        if isinstance(value, str):
            return value[:80] or "—"
        if isinstance(value, list):
            return str(len(value))
        if isinstance(value, dict):
            if value.get("ok") is False:
                return str(value.get("error") or "Error")[:80]
            for key in (
                "count",
                "total",
                "available_cash",
                "net",
                "equity",
                "status",
            ):
                if key in value and value[key] is not None:
                    return str(value[key])
            for key in ("data", "holdings", "positions", "orders", "result"):
                nested = value.get(key)
                if isinstance(nested, list):
                    return str(len(nested))
            try:
                return json.dumps(value, default=str)[:80]
            except TypeError:
                return str(value)[:80]
        return str(value)[:80]
