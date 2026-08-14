import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.copy_helpers import copy_name, unique_copy_slug
from app.api.schemas import (
    AgentCatalogItemOut,
    AgentCatalogPageOut,
    AgentConfigOut,
    AgentCreateIn,
    AgentRestoreIn,
    AgentUpdateIn,
    AgentVersionOut,
    AgentVersionSummaryOut,
    ToolBindingIn,
)
from app.auth.dependencies import require_roles
from app.db.models import Role
from app.db.repositories import AgentRepository
from app.db.session import tenant_session
from app.tenancy.context import TenantContext

router = APIRouter(prefix="/admin/agents", tags=["admin-agents"])


def _version_out(version) -> AgentVersionOut:  # type: ignore[no-untyped-def]
    adapter = "agno"
    if version.team_config and version.team_config.get("framework_adapter"):
        adapter = str(version.team_config.get("framework_adapter"))
    return AgentVersionOut(
        id=version.id,
        version=version.version,
        status=version.status.value if hasattr(version.status, "value") else str(version.status),
        instructions=version.instructions,
        model_id=version.model_id,
        temperature=version.temperature,
        memory_mode=version.memory_mode,
        framework_adapter=adapter,  # type: ignore[arg-type]
        created_at=version.created_at,
    )


def _framework_adapter(team_config: dict | None) -> str:  # type: ignore[type-arg]
    raw = (team_config or {}).get("framework_adapter") if team_config else None
    value = str(raw or "agno").strip() or "agno"
    return value


def _guardrails_out(team_config: dict | None) -> dict[str, bool]:  # type: ignore[type-arg]
    raw = (team_config or {}).get("guardrails") if team_config else None
    raw = raw if isinstance(raw, dict) else {}
    return {
        "prompt_injection": bool(raw.get("prompt_injection")),
        "pii_detection": bool(raw.get("pii_detection")),
        "openai_moderation": bool(raw.get("openai_moderation")),
    }


async def _config_out(repo: AgentRepository, config) -> AgentConfigOut:  # type: ignore[no-untyped-def]
    draft = await repo.get_latest_draft(config.id)
    published = None
    if config.published_version_id:
        published = await repo.get_version(config.published_version_id, allow_draft=False)
    editable = draft or published
    bindings = await repo.bindings(editable.id) if editable else []
    knowledge_base_id = None
    if editable and editable.team_config:
        knowledge_base_id = editable.team_config.get("knowledge_base_id")
    return AgentConfigOut(
        id=config.id,
        slug=config.slug,
        name=config.name,
        description=config.description,
        published_version_id=config.published_version_id,
        updated_at=config.updated_at,
        tools=[
            ToolBindingIn(
                tool_key=binding.tool_key,
                tool_definition_id=binding.tool_definition_id,
                config=binding.config,
                credential_id=binding.credential_id,
            )
            for binding in bindings
        ],
        knowledge_base_id=knowledge_base_id,
        framework_adapter=_framework_adapter(editable.team_config if editable else None),  # type: ignore[arg-type]
        guardrails=_guardrails_out(editable.team_config if editable else None),
        draft=_version_out(draft) if draft else None,
        published=_version_out(published) if published else None,
    )


@router.get("/catalog", response_model=AgentCatalogPageOut)
async def list_agent_catalog(
    context: Annotated[
        TenantContext, Depends(require_roles(Role.platform_admin, Role.tenant_admin))
    ],
    session: Annotated[AsyncSession, Depends(tenant_session)],
    q: str | None = None,
    status: str = "all",
    page: int = 1,
    page_size: int = 25,
) -> AgentCatalogPageOut:
    repo = AgentRepository(session, context)
    configs, total = await repo.search_configs(
        q=q,
        status=None if status == "all" else status,
        page=page,
        page_size=page_size,
    )
    items: list[AgentCatalogItemOut] = []
    for config in configs:
        draft = await repo.get_latest_draft(config.id)
        published = (
            await repo.get_version(config.published_version_id, allow_draft=False)
            if config.published_version_id
            else None
        )
        editable = draft or published
        items.append(
            AgentCatalogItemOut(
                id=config.id,
                slug=config.slug,
                name=config.name,
                status="published" if published else "draft",
                model_id=editable.model_id if editable else "openai:gpt-4.1-mini",
                domain=config.domain or "generic",
                published_version=published.version if published else None,
                updated_at=config.updated_at,
            )
        )
    return AgentCatalogPageOut(
        items=items,
        total=total,
        page=max(1, page),
        page_size=min(max(1, page_size), 100),
    )


@router.get("", response_model=list[AgentConfigOut])
async def list_agents(
    context: Annotated[
        TenantContext, Depends(require_roles(Role.platform_admin, Role.tenant_admin))
    ],
    session: Annotated[AsyncSession, Depends(tenant_session)],
) -> list[AgentConfigOut]:
    repo = AgentRepository(session, context)
    configs = await repo.list_configs()
    return [await _config_out(repo, config) for config in configs]


@router.post("", response_model=AgentConfigOut, status_code=201)
async def create_agent(
    body: AgentCreateIn,
    context: Annotated[
        TenantContext, Depends(require_roles(Role.platform_admin, Role.tenant_admin))
    ],
    session: Annotated[AsyncSession, Depends(tenant_session)],
) -> AgentConfigOut:
    repo = AgentRepository(session, context)
    config = await repo.create_config(slug=body.slug, name=body.name, description=body.description)
    await repo.create_draft(
        config_id=config.id,
        instructions=body.instructions,
        model_id=body.model_id,
        temperature=body.temperature,
        memory_mode=body.memory_mode,
        tools=[tool.model_dump() for tool in body.tools],
        knowledge_base_id=body.knowledge_base_id,
        framework_adapter=body.framework_adapter,
        guardrails=body.guardrails,
    )
    refreshed = await repo.get_config(config.id)
    assert refreshed is not None
    return await _config_out(repo, refreshed)


@router.get("/{agent_id}", response_model=AgentConfigOut)
async def get_agent(
    agent_id: uuid.UUID,
    context: Annotated[
        TenantContext, Depends(require_roles(Role.platform_admin, Role.tenant_admin))
    ],
    session: Annotated[AsyncSession, Depends(tenant_session)],
) -> AgentConfigOut:
    repo = AgentRepository(session, context)
    config = await repo.get_config(agent_id)
    if config is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return await _config_out(repo, config)


@router.post("/{agent_id}/clone", response_model=AgentConfigOut, status_code=201)
async def clone_agent(
    agent_id: uuid.UUID,
    context: Annotated[
        TenantContext, Depends(require_roles(Role.platform_admin, Role.tenant_admin))
    ],
    session: Annotated[AsyncSession, Depends(tenant_session)],
) -> AgentConfigOut:
    repo = AgentRepository(session, context)
    source = await repo.get_config(agent_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    draft = await repo.get_latest_draft(agent_id)
    published = (
        await repo.get_version(source.published_version_id, allow_draft=False)
        if source.published_version_id
        else None
    )
    editable = draft or published
    if editable is None:
        raise HTTPException(status_code=400, detail="Agent has no version to clone")

    async def slug_taken(slug: str) -> bool:
        return await repo.get_config_by_slug(slug) is not None

    try:
        new_slug = await unique_copy_slug(source.slug, slug_taken)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    bindings = await repo.bindings(editable.id)
    knowledge_base_id = None
    if editable.team_config and editable.team_config.get("knowledge_base_id"):
        knowledge_base_id = uuid.UUID(str(editable.team_config["knowledge_base_id"]))
    guardrails = None
    if editable.team_config and isinstance(editable.team_config.get("guardrails"), dict):
        guardrails = editable.team_config["guardrails"]
    framework_adapter = _framework_adapter(editable.team_config)

    config = await repo.create_config(
        slug=new_slug,
        name=copy_name(source.name),
        description=source.description,
        domain=source.domain,
    )
    try:
        await repo.create_draft(
            config_id=config.id,
            instructions=editable.instructions,
            model_id=editable.model_id,
            temperature=editable.temperature,
            memory_mode=editable.memory_mode,
            tools=[
                {
                    "tool_key": binding.tool_key,
                    "tool_definition_id": binding.tool_definition_id,
                    "config": binding.config or {},
                    "credential_id": binding.credential_id,
                }
                for binding in bindings
            ],
            knowledge_base_id=knowledge_base_id,
            guardrails=guardrails,
            framework_adapter=framework_adapter,
        )
    except LookupError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    refreshed = await repo.get_config(config.id)
    assert refreshed is not None
    return await _config_out(repo, refreshed)


@router.patch("/{agent_id}", response_model=AgentConfigOut)
async def update_agent(
    agent_id: uuid.UUID,
    body: AgentUpdateIn,
    context: Annotated[
        TenantContext, Depends(require_roles(Role.platform_admin, Role.tenant_admin))
    ],
    session: Annotated[AsyncSession, Depends(tenant_session)],
) -> AgentConfigOut:
    repo = AgentRepository(session, context)
    config = await repo.update_config(agent_id, name=body.name, description=body.description)
    if config is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    if any(
        value is not None
        for value in (
            body.instructions,
            body.model_id,
            body.temperature,
            body.memory_mode,
            body.tools,
            body.knowledge_base_id,
            body.guardrails,
            body.framework_adapter,
        )
    ):
        draft = await repo.get_latest_draft(agent_id)
        source = draft
        if source is None and config.published_version_id:
            source = await repo.get_version(config.published_version_id, allow_draft=False)
        existing_guardrails = None
        existing_kb = None
        existing_adapter = "agno"
        if source and source.team_config:
            if isinstance(source.team_config.get("guardrails"), dict):
                existing_guardrails = source.team_config["guardrails"]
            kb_raw = source.team_config.get("knowledge_base_id")
            if kb_raw:
                existing_kb = uuid.UUID(str(kb_raw))
            existing_adapter = _framework_adapter(source.team_config)
        existing_tools = None
        if body.tools is None and source is not None:
            existing_tools = [
                {
                    "tool_key": binding.tool_key,
                    "tool_definition_id": binding.tool_definition_id,
                    "config": binding.config or {},
                    "credential_id": binding.credential_id,
                }
                for binding in await repo.bindings(source.id)
            ]
        await repo.create_draft(
            config_id=agent_id,
            instructions=body.instructions
            or (draft.instructions if draft else "You are a helpful assistant."),
            model_id=body.model_id or (draft.model_id if draft else "openai:gpt-4.1-mini"),
            temperature=body.temperature
            if body.temperature is not None
            else (draft.temperature if draft else 0.2),
            memory_mode=body.memory_mode or (draft.memory_mode if draft else "session"),
            tools=[tool.model_dump() for tool in body.tools]
            if body.tools is not None
            else existing_tools,
            knowledge_base_id=body.knowledge_base_id
            if body.knowledge_base_id is not None
            else existing_kb,
            guardrails=body.guardrails
            if body.guardrails is not None
            else existing_guardrails,
            framework_adapter=body.framework_adapter
            if body.framework_adapter is not None
            else existing_adapter,
        )
    refreshed = await repo.get_config(agent_id)
    assert refreshed is not None
    return await _config_out(repo, refreshed)


@router.post("/{agent_id}/publish", response_model=AgentConfigOut)
async def publish_agent(
    agent_id: uuid.UUID,
    context: Annotated[
        TenantContext, Depends(require_roles(Role.platform_admin, Role.tenant_admin))
    ],
    session: Annotated[AsyncSession, Depends(tenant_session)],
) -> AgentConfigOut:
    repo = AgentRepository(session, context)
    draft = await repo.get_latest_draft(agent_id)
    if draft is None:
        raise HTTPException(status_code=400, detail="No draft version to publish")
    await repo.publish(draft.id)
    config = await repo.get_config(agent_id)
    assert config is not None
    return await _config_out(repo, config)


@router.get("/{agent_id}/versions", response_model=list[AgentVersionSummaryOut])
async def list_agent_versions(
    agent_id: uuid.UUID,
    context: Annotated[
        TenantContext, Depends(require_roles(Role.platform_admin, Role.tenant_admin))
    ],
    session: Annotated[AsyncSession, Depends(tenant_session)],
) -> list[AgentVersionSummaryOut]:
    repo = AgentRepository(session, context)
    config = await repo.get_config(agent_id)
    if config is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return [
        AgentVersionSummaryOut(
            id=version.id,
            version=version.version,
            status=version.status.value,
            model_id=version.model_id,
            is_live=config.published_version_id == version.id,
            created_at=version.created_at,
        )
        for version in await repo.list_versions(agent_id)
    ]


@router.get("/{agent_id}/versions/{version_id}", response_model=AgentVersionOut)
async def get_agent_version(
    agent_id: uuid.UUID,
    version_id: uuid.UUID,
    context: Annotated[
        TenantContext, Depends(require_roles(Role.platform_admin, Role.tenant_admin))
    ],
    session: Annotated[AsyncSession, Depends(tenant_session)],
) -> AgentVersionOut:
    repo = AgentRepository(session, context)
    config = await repo.get_config(agent_id)
    if config is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    version = await repo.get_version(version_id, allow_draft=True)
    if version is None or version.agent_config_id != agent_id:
        raise HTTPException(status_code=404, detail="Agent version not found")
    return _version_out(version)


@router.post("/{agent_id}/versions/{version_id}/restore", response_model=AgentConfigOut)
async def restore_agent_version(
    agent_id: uuid.UUID,
    version_id: uuid.UUID,
    body: AgentRestoreIn,
    context: Annotated[
        TenantContext, Depends(require_roles(Role.platform_admin, Role.tenant_admin))
    ],
    session: Annotated[AsyncSession, Depends(tenant_session)],
) -> AgentConfigOut:
    repo = AgentRepository(session, context)
    try:
        await repo.restore_version(agent_id, version_id, as_draft=body.as_draft)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    config = await repo.get_config(agent_id)
    assert config is not None
    return await _config_out(repo, config)


@router.delete("/{agent_id}", status_code=204)
async def delete_agent(
    agent_id: uuid.UUID,
    context: Annotated[
        TenantContext, Depends(require_roles(Role.platform_admin, Role.tenant_admin))
    ],
    session: Annotated[AsyncSession, Depends(tenant_session)],
) -> None:
    repo = AgentRepository(session, context)
    try:
        await repo.delete_config(agent_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except IntegrityError as exc:
        raise HTTPException(
            status_code=409,
            detail="Agent is referenced by sessions or schedules and cannot be deleted",
        ) from exc
