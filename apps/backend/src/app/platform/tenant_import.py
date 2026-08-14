"""Cross-tenant import of teams/workflows for platform admins.

Copies the dependency graph (agents, tools, knowledge metadata) into the
destination as drafts. Credential secret values are never copied.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.copy_helpers import unique_copy_slug
from app.db.models import Tenant
from app.db.repositories import (
    AgentRepository,
    KnowledgeRepository,
    TeamRepository,
    ToolDefinitionRepository,
    ToolDefinitionVersionRepository,
    WorkflowRepository,
)
from app.domains.catalog_groups import resolve_catalog_domain
from app.tenancy.context import TenantContext


@dataclass
class ImportCatalogItem:
    id: uuid.UUID
    name: str
    slug: str
    kind: str  # team | workflow
    status: str  # draft | published
    domain: str = "generic"


@dataclass
class ImportResult:
    agents: dict[str, str] = field(default_factory=dict)
    teams: dict[str, str] = field(default_factory=dict)
    workflows: dict[str, str] = field(default_factory=dict)
    tools: dict[str, str] = field(default_factory=dict)
    knowledge_bases: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "agents": self.agents,
            "teams": self.teams,
            "workflows": self.workflows,
            "tools": self.tools,
            "knowledge_bases": self.knowledge_bases,
            "warnings": self.warnings,
            "counts": {
                "agents": len(self.agents),
                "teams": len(self.teams),
                "workflows": len(self.workflows),
                "tools": len(self.tools),
                "knowledge_bases": len(self.knowledge_bases),
            },
        }


@dataclass
class _AgentSnap:
    config_id: uuid.UUID
    slug: str
    name: str
    description: str | None
    domain: str
    instructions: str
    model_id: str
    temperature: float
    memory_mode: str
    knowledge_base_id: uuid.UUID | None
    tools: list[dict[str, Any]]


@dataclass
class _TeamSnap:
    config_id: uuid.UUID
    slug: str
    name: str
    description: str | None
    domain: str
    instructions: str
    mode: str
    model_id: str
    temperature: float
    member_config_ids: list[uuid.UUID]
    tools: list[dict[str, Any]]


@dataclass
class _WorkflowSnap:
    config_id: uuid.UUID
    slug: str
    name: str
    description: str | None
    domain: str
    mode: str
    steps: list[dict[str, Any]]


@dataclass
class _ToolSnap:
    definition_id: uuid.UUID
    values: dict[str, Any]
    version: dict[str, Any] | None  # tenant_python published/draft payload


@dataclass
class _KnowledgeSnap:
    base_id: uuid.UUID
    name: str
    config: dict[str, Any]
    sources: list[dict[str, Any]]


@dataclass
class ImportBundle:
    agents: dict[uuid.UUID, _AgentSnap] = field(default_factory=dict)
    teams: dict[uuid.UUID, _TeamSnap] = field(default_factory=dict)
    workflows: dict[uuid.UUID, _WorkflowSnap] = field(default_factory=dict)
    tools: dict[uuid.UUID, _ToolSnap] = field(default_factory=dict)
    knowledge_bases: dict[uuid.UUID, _KnowledgeSnap] = field(default_factory=dict)
    source_domain: str = "generic"
    had_credentials: bool = False


def _resource_domain(config: Any, bundle: ImportBundle) -> str:
    return resolve_catalog_domain(
        slug=config.slug,
        stored_domain=getattr(config, "domain", None),
        tenant_domain=bundle.source_domain,
    )


def _binding_dict(binding: Any) -> dict[str, Any]:
    return {
        "tool_key": binding.tool_key,
        "tool_definition_id": binding.tool_definition_id,
        "config": dict(binding.config or {}),
        "credential_id": binding.credential_id,
    }


async def list_tenant_catalog(
    session: AsyncSession, context: TenantContext
) -> list[ImportCatalogItem]:
    """List teams and workflows available to pick for import."""
    tenant = await session.get(Tenant, context.tenant_id)
    tenant_domain = tenant.domain if tenant else "generic"
    items: list[ImportCatalogItem] = []
    for config in await TeamRepository(session, context).list_configs():
        items.append(
            ImportCatalogItem(
                id=config.id,
                name=config.name,
                slug=config.slug,
                kind="team",
                status="published" if config.published_version_id else "draft",
                domain=resolve_catalog_domain(
                    slug=config.slug,
                    stored_domain=getattr(config, "domain", None),
                    tenant_domain=tenant_domain,
                ),
            )
        )
    for config in await WorkflowRepository(session, context).list_configs():
        items.append(
            ImportCatalogItem(
                id=config.id,
                name=config.name,
                slug=config.slug,
                kind="workflow",
                status="published" if config.published_version_id else "draft",
                domain=resolve_catalog_domain(
                    slug=config.slug,
                    stored_domain=getattr(config, "domain", None),
                    tenant_domain=tenant_domain,
                ),
            )
        )
    items.sort(key=lambda item: (item.domain, item.kind, item.name.lower()))
    return items


async def _allocate_slug(
    preferred: str, is_taken: Any
) -> str:
    if not await is_taken(preferred):
        return preferred
    return await unique_copy_slug(preferred, is_taken)


async def _collect_tool(
    session: AsyncSession, context: TenantContext, bundle: ImportBundle, tool_id: uuid.UUID
) -> None:
    if tool_id in bundle.tools:
        return
    row = await ToolDefinitionRepository(session, context).get(tool_id)
    if row is None:
        return
    if row.credential_id is not None:
        bundle.had_credentials = True
    values = {
        "name": row.name,
        "slug": row.slug,
        "description": row.description,
        "kind": row.kind,
        "http_method": row.http_method,
        "base_url": row.base_url,
        "path": row.path,
        "request_schema": dict(row.request_schema or {}),
        "response_description": row.response_description,
        "response_schema": row.response_schema,
        "headers": dict(row.headers or {}),
        "config": dict(row.config or {}),
        "credential_id": None,
        "approval_required": row.approval_required,
        "active": row.active,
    }
    version_payload: dict[str, Any] | None = None
    if row.kind == "tenant_python":
        versions = ToolDefinitionVersionRepository(session, context)
        source_version = None
        if row.published_version_id:
            source_version = await versions.get(row.published_version_id)
        if source_version is None:
            source_version = await versions.latest_draft(row.id)
        if source_version is not None:
            version_payload = {
                "source_code": source_version.source_code,
                "dependencies": list(source_version.dependencies or []),
                "capabilities": list(source_version.capabilities or []),
                "settings": dict(source_version.settings or {}),
            }
        else:
            config = dict(row.config or {})
            version_payload = {
                "source_code": str(config.get("source_code") or ""),
                "dependencies": list(config.get("dependencies") or []),
                "capabilities": list(config.get("capabilities") or []),
                "settings": dict(config.get("settings") or {}),
            }
        values["config"] = {
            **dict(values["config"]),
            **version_payload,
            "version_status": "draft",
        }
    bundle.tools[tool_id] = _ToolSnap(
        definition_id=tool_id, values=values, version=version_payload
    )


async def _collect_knowledge(
    session: AsyncSession, context: TenantContext, bundle: ImportBundle, base_id: uuid.UUID
) -> None:
    if base_id in bundle.knowledge_bases:
        return
    knowledge = KnowledgeRepository(session, context)
    base = await knowledge.get_base(base_id)
    if base is None:
        return
    sources = [
        {
            "kind": source.kind,
            "uri": source.uri,
            "metadata": dict(source.metadata_ or {}),
        }
        for source in await knowledge.list_sources(base_id)
    ]
    bundle.knowledge_bases[base_id] = _KnowledgeSnap(
        base_id=base_id,
        name=base.name,
        config=dict(base.config or {}),
        sources=sources,
    )


async def _collect_agent(
    session: AsyncSession, context: TenantContext, bundle: ImportBundle, agent_id: uuid.UUID
) -> None:
    if agent_id in bundle.agents:
        return
    repo = AgentRepository(session, context)
    config = await repo.get_config(agent_id)
    if config is None:
        raise LookupError(f"Agent not found: {agent_id}")
    draft = await repo.get_latest_draft(agent_id)
    published = (
        await repo.get_version(config.published_version_id, allow_draft=False)
        if config.published_version_id
        else None
    )
    editable = draft or published
    if editable is None:
        raise LookupError(f"Agent has no version to import: {config.slug}")

    knowledge_base_id = None
    if editable.team_config and editable.team_config.get("knowledge_base_id"):
        knowledge_base_id = uuid.UUID(str(editable.team_config["knowledge_base_id"]))
        await _collect_knowledge(session, context, bundle, knowledge_base_id)

    tools = [_binding_dict(binding) for binding in await repo.bindings(editable.id)]
    for tool in tools:
        if tool.get("credential_id"):
            bundle.had_credentials = True
        definition_id = tool.get("tool_definition_id")
        if definition_id:
            await _collect_tool(session, context, bundle, definition_id)

    bundle.agents[agent_id] = _AgentSnap(
        config_id=agent_id,
        slug=config.slug,
        name=config.name,
        description=config.description,
        domain=_resource_domain(config, bundle),
        instructions=editable.instructions,
        model_id=editable.model_id,
        temperature=editable.temperature,
        memory_mode=editable.memory_mode,
        knowledge_base_id=knowledge_base_id,
        tools=tools,
    )


async def _collect_team(
    session: AsyncSession, context: TenantContext, bundle: ImportBundle, team_id: uuid.UUID
) -> None:
    if team_id in bundle.teams:
        return
    repo = TeamRepository(session, context)
    config = await repo.get_config(team_id)
    if config is None:
        raise LookupError(f"Team not found: {team_id}")
    draft = await repo.get_latest_draft(team_id)
    published = (
        await repo.get_version(config.published_version_id)
        if config.published_version_id
        else None
    )
    editable = draft or published
    if editable is None:
        raise LookupError(f"Team has no version to import: {config.slug}")

    members = await repo.members(editable.id)
    member_ids = [member.agent_config_id for member in members]
    for member_id in member_ids:
        await _collect_agent(session, context, bundle, member_id)

    tools = [_binding_dict(binding) for binding in await repo.bindings(editable.id)]
    for tool in tools:
        if tool.get("credential_id"):
            bundle.had_credentials = True
        definition_id = tool.get("tool_definition_id")
        if definition_id:
            await _collect_tool(session, context, bundle, definition_id)

    bundle.teams[team_id] = _TeamSnap(
        config_id=team_id,
        slug=config.slug,
        name=config.name,
        description=config.description,
        domain=_resource_domain(config, bundle),
        instructions=editable.instructions,
        mode=editable.mode,
        model_id=editable.model_id,
        temperature=editable.temperature,
        member_config_ids=member_ids,
        tools=tools,
    )


async def _collect_workflow(
    session: AsyncSession, context: TenantContext, bundle: ImportBundle, workflow_id: uuid.UUID
) -> None:
    if workflow_id in bundle.workflows:
        return
    repo = WorkflowRepository(session, context)
    config = await repo.get_config(workflow_id)
    if config is None:
        raise LookupError(f"Workflow not found: {workflow_id}")
    draft = await repo.get_latest_draft(workflow_id)
    published = (
        await repo.get_version(config.published_version_id)
        if config.published_version_id
        else None
    )
    editable = draft or published
    if editable is None:
        raise LookupError(f"Workflow has no version to import: {config.slug}")

    steps_out: list[dict[str, Any]] = []
    for step in await repo.steps(editable.id):
        target_id = (
            step.agent_config_id if step.target_type == "agent" else step.team_config_id
        )
        if target_id is None:
            continue
        if step.target_type == "agent":
            await _collect_agent(session, context, bundle, target_id)
        else:
            await _collect_team(session, context, bundle, target_id)
        steps_out.append(
            {
                "name": step.name,
                "target_type": step.target_type,
                "target_config_id": target_id,
                "condition_expression": step.condition_expression,
            }
        )

    bundle.workflows[workflow_id] = _WorkflowSnap(
        config_id=workflow_id,
        slug=config.slug,
        name=config.name,
        description=config.description,
        domain=_resource_domain(config, bundle),
        mode=editable.mode,
        steps=steps_out,
    )


async def collect_import_bundle(
    session: AsyncSession,
    context: TenantContext,
    *,
    team_ids: list[uuid.UUID],
    workflow_ids: list[uuid.UUID],
) -> ImportBundle:
    if not team_ids and not workflow_ids:
        raise ValueError("Select at least one team or workflow to import")
    bundle = ImportBundle()
    tenant = await session.get(Tenant, context.tenant_id)
    bundle.source_domain = tenant.domain if tenant else "generic"
    for team_id in team_ids:
        await _collect_team(session, context, bundle, team_id)
    for workflow_id in workflow_ids:
        await _collect_workflow(session, context, bundle, workflow_id)
    return bundle


def _remap_tools(
    tools: list[dict[str, Any]],
    tool_map: dict[uuid.UUID, uuid.UUID],
    warnings: list[str],
) -> list[dict[str, Any]]:
    remapped: list[dict[str, Any]] = []
    for tool in tools:
        item = {
            "tool_key": tool.get("tool_key"),
            "tool_definition_id": None,
            "config": dict(tool.get("config") or {}),
            "credential_id": None,
        }
        if tool.get("credential_id"):
            warnings.append("Stripped credential bindings from imported tools")
        definition_id = tool.get("tool_definition_id")
        if definition_id:
            mapped = tool_map.get(definition_id)
            if mapped is None:
                warnings.append(
                    f"Skipped tool definition binding {definition_id} (not imported)"
                )
                continue
            item["tool_definition_id"] = mapped
        remapped.append(item)
    # Deduplicate warning noise
    return remapped


async def materialize_import_bundle(
    session: AsyncSession,
    context: TenantContext,
    bundle: ImportBundle,
) -> ImportResult:
    result = ImportResult()
    if bundle.had_credentials:
        result.warnings.append(
            "Credential references were stripped; re-attach credentials in the destination tenant."
        )

    tool_map: dict[uuid.UUID, uuid.UUID] = {}
    tools_repo = ToolDefinitionRepository(session, context)
    versions_repo = ToolDefinitionVersionRepository(session, context)
    for old_id, snap in bundle.tools.items():
        values = dict(snap.values)
        values["credential_id"] = None

        async def slug_taken(slug: str, *, _repo=tools_repo) -> bool:
            return await _repo.get_by_slug(slug) is not None

        values["slug"] = await _allocate_slug(snap.values["slug"], slug_taken)
        if values["kind"] == "tenant_python":
            values["config"] = {
                **dict(values.get("config") or {}),
                "version_status": "draft",
            }
        created = await tools_repo.create(values)
        if values["kind"] == "tenant_python" and snap.version is not None:
            draft = await versions_repo.upsert_draft(
                tool_definition_id=created.id,
                source_code=str(snap.version.get("source_code") or ""),
                dependencies=list(snap.version.get("dependencies") or []),
                capabilities=list(snap.version.get("capabilities") or []),
                settings=dict(snap.version.get("settings") or {}),
                created_by=context.user_id,
            )
            published = await versions_repo.publish(draft.id, created)
            if published is None:
                result.warnings.append(
                    f"Could not publish imported Python tool '{created.slug}'"
                )
        tool_map[old_id] = created.id
        result.tools[str(old_id)] = str(created.id)

    kb_map: dict[uuid.UUID, uuid.UUID] = {}
    knowledge = KnowledgeRepository(session, context)
    for old_id, snap in bundle.knowledge_bases.items():
        base = await knowledge.create_base(name=snap.name, config=snap.config)
        for source in snap.sources:
            await knowledge.create_source(
                knowledge_base_id=base.id,
                kind=source["kind"],
                uri=source["uri"],
                metadata={
                    **dict(source.get("metadata") or {}),
                    "imported": True,
                    "note": "Metadata only — re-upload documents to index in this tenant.",
                },
            )
        if snap.sources:
            result.warnings.append(
                "Knowledge source metadata was copied without document bytes or embeddings; re-upload to index."
            )
        kb_map[old_id] = base.id
        result.knowledge_bases[str(old_id)] = str(base.id)

    # Deduplicate knowledge warning
    if any("Knowledge source metadata" in w for w in result.warnings):
        result.warnings = [
            w
            for i, w in enumerate(result.warnings)
            if "Knowledge source metadata" not in w
            or i == next(
                j
                for j, x in enumerate(result.warnings)
                if "Knowledge source metadata" in x
            )
        ]

    agent_map: dict[uuid.UUID, uuid.UUID] = {}
    agents = AgentRepository(session, context)
    for old_id, snap in bundle.agents.items():

        async def agent_slug_taken(slug: str, *, _repo=agents) -> bool:
            return await _repo.get_config_by_slug(slug) is not None

        config = await agents.create_config(
            slug=await _allocate_slug(snap.slug, agent_slug_taken),
            name=snap.name,
            description=snap.description,
            domain=snap.domain,
        )
        tools = _remap_tools(snap.tools, tool_map, result.warnings)
        kb_id = kb_map.get(snap.knowledge_base_id) if snap.knowledge_base_id else None
        await agents.create_draft(
            config_id=config.id,
            instructions=snap.instructions,
            model_id=snap.model_id,
            temperature=snap.temperature,
            memory_mode=snap.memory_mode,
            tools=tools,
            knowledge_base_id=kb_id,
        )
        agent_map[old_id] = config.id
        result.agents[str(old_id)] = str(config.id)

    team_map: dict[uuid.UUID, uuid.UUID] = {}
    teams = TeamRepository(session, context)
    for old_id, snap in bundle.teams.items():

        async def team_slug_taken(slug: str, *, _repo=teams) -> bool:
            return await _repo.get_config_by_slug(slug) is not None

        config = await teams.create_config(
            slug=await _allocate_slug(snap.slug, team_slug_taken),
            name=snap.name,
            description=snap.description,
            domain=snap.domain,
        )
        member_ids = []
        for member_id in snap.member_config_ids:
            mapped = agent_map.get(member_id)
            if mapped is None:
                raise LookupError(f"Missing imported agent for team member {member_id}")
            member_ids.append(mapped)
        tools = _remap_tools(snap.tools, tool_map, result.warnings)
        await teams.create_draft(
            config_id=config.id,
            instructions=snap.instructions,
            mode=snap.mode,
            model_id=snap.model_id,
            temperature=snap.temperature,
            member_config_ids=member_ids,
            tools=tools,
        )
        team_map[old_id] = config.id
        result.teams[str(old_id)] = str(config.id)

    workflows = WorkflowRepository(session, context)
    for old_id, snap in bundle.workflows.items():

        async def workflow_slug_taken(slug: str, *, _repo=workflows) -> bool:
            return await _repo.get_config_by_slug(slug) is not None

        config = await workflows.create_config(
            slug=await _allocate_slug(snap.slug, workflow_slug_taken),
            name=snap.name,
            description=snap.description,
            domain=snap.domain,
        )
        steps = []
        for step in snap.steps:
            old_target = step["target_config_id"]
            if step["target_type"] == "agent":
                mapped = agent_map.get(old_target)
            else:
                mapped = team_map.get(old_target)
            if mapped is None:
                raise LookupError(f"Missing imported target for workflow step {step['name']}")
            steps.append(
                {
                    "name": step["name"],
                    "target_type": step["target_type"],
                    "target_config_id": mapped,
                    "condition_expression": step.get("condition_expression"),
                }
            )
        if steps:
            await workflows.create_draft(
                config_id=config.id,
                mode=snap.mode,
                steps=steps,
            )
        result.workflows[str(old_id)] = str(config.id)

    # Collapse duplicate credential strip warnings
    seen: set[str] = set()
    unique_warnings: list[str] = []
    for warning in result.warnings:
        if warning in seen:
            continue
        seen.add(warning)
        unique_warnings.append(warning)
    result.warnings = unique_warnings
    return result
