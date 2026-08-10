import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.copy_helpers import copy_name, unique_copy_slug
from app.api.schemas import (
    TeamCatalogItemOut,
    TeamCatalogPageOut,
    TeamConfigOut,
    TeamCreateIn,
    TeamMemberOut,
    TeamRestoreIn,
    TeamUpdateIn,
    TeamVersionOut,
    TeamVersionSummaryOut,
    ToolBindingIn,
)
from app.auth.dependencies import require_roles
from app.db.models import Role, TeamConfig, TeamVersion
from app.db.repositories import AgentRepository, TeamRepository
from app.db.session import tenant_session
from app.tenancy.context import TenantContext

router = APIRouter(prefix="/admin/teams", tags=["admin-teams"])


async def _version_out(
    team_repo: TeamRepository, agent_repo: AgentRepository, version: TeamVersion
) -> TeamVersionOut:
    members: list[TeamMemberOut] = []
    for member in await team_repo.members(version.id):
        config = await agent_repo.get_config(member.agent_config_id)
        agent_version = await agent_repo.get_version(member.agent_version_id, allow_draft=True)
        if config is None or agent_version is None:
            continue
        members.append(
            TeamMemberOut(
                agent_config_id=config.id,
                agent_version_id=agent_version.id,
                position=member.position,
                name=config.name,
                slug=config.slug,
                version=agent_version.version,
                status=agent_version.status.value,
            )
        )
    return TeamVersionOut(
        id=version.id,
        version=version.version,
        status=version.status.value,
        instructions=version.instructions,
        mode=version.mode,
        model_id=version.model_id,
        temperature=version.temperature,
        members=members,
        created_at=version.created_at,
    )


async def team_config_out(repo: TeamRepository, config: TeamConfig) -> TeamConfigOut:
    agent_repo = AgentRepository(repo.session, repo.context)
    draft = await repo.get_latest_draft(config.id)
    published = (
        await repo.get_version(config.published_version_id) if config.published_version_id else None
    )
    editable = draft or published
    bindings = await repo.bindings(editable.id) if editable else []
    return TeamConfigOut(
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
        draft=await _version_out(repo, agent_repo, draft) if draft else None,
        published=await _version_out(repo, agent_repo, published) if published else None,
    )


@router.get("/catalog", response_model=TeamCatalogPageOut)
async def list_team_catalog(
    context: Annotated[
        TenantContext, Depends(require_roles(Role.platform_admin, Role.tenant_admin))
    ],
    session: Annotated[AsyncSession, Depends(tenant_session)],
    q: str | None = None,
    status: str = "all",
    page: int = 1,
    page_size: int = 25,
) -> TeamCatalogPageOut:
    repo = TeamRepository(session, context)
    configs, total = await repo.search_configs(
        q=q,
        status=None if status == "all" else status,
        page=page,
        page_size=page_size,
    )
    items: list[TeamCatalogItemOut] = []
    for config in configs:
        draft = await repo.get_latest_draft(config.id)
        published = (
            await repo.get_version(config.published_version_id)
            if config.published_version_id
            else None
        )
        editable = draft or published
        member_count = len(await repo.members(editable.id)) if editable else 0
        items.append(
            TeamCatalogItemOut(
                id=config.id,
                slug=config.slug,
                name=config.name,
                status="published" if published else "draft",
                mode=editable.mode if editable else "coordinate",
                member_count=member_count,
                published_version=published.version if published else None,
                updated_at=config.updated_at,
            )
        )
    return TeamCatalogPageOut(
        items=items,
        total=total,
        page=max(1, page),
        page_size=min(max(1, page_size), 100),
    )


@router.get("", response_model=list[TeamConfigOut])
async def list_teams(
    context: Annotated[
        TenantContext, Depends(require_roles(Role.platform_admin, Role.tenant_admin))
    ],
    session: Annotated[AsyncSession, Depends(tenant_session)],
) -> list[TeamConfigOut]:
    repo = TeamRepository(session, context)
    return [await team_config_out(repo, config) for config in await repo.list_configs()]


@router.post("", response_model=TeamConfigOut, status_code=201)
async def create_team(
    body: TeamCreateIn,
    context: Annotated[
        TenantContext, Depends(require_roles(Role.platform_admin, Role.tenant_admin))
    ],
    session: Annotated[AsyncSession, Depends(tenant_session)],
) -> TeamConfigOut:
    repo = TeamRepository(session, context)
    config = await repo.create_config(slug=body.slug, name=body.name, description=body.description)
    try:
        await repo.create_draft(
            config_id=config.id,
            instructions=body.instructions,
            mode=body.mode,
            model_id=body.model_id,
            temperature=body.temperature,
            member_config_ids=body.member_config_ids,
            tools=[tool.model_dump() for tool in body.tools],
        )
    except (LookupError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return await team_config_out(repo, config)


@router.get("/{team_id}", response_model=TeamConfigOut)
async def get_team(
    team_id: uuid.UUID,
    context: Annotated[
        TenantContext, Depends(require_roles(Role.platform_admin, Role.tenant_admin))
    ],
    session: Annotated[AsyncSession, Depends(tenant_session)],
) -> TeamConfigOut:
    repo = TeamRepository(session, context)
    config = await repo.get_config(team_id)
    if config is None:
        raise HTTPException(status_code=404, detail="Team not found")
    return await team_config_out(repo, config)


@router.post("/{team_id}/clone", response_model=TeamConfigOut, status_code=201)
async def clone_team(
    team_id: uuid.UUID,
    context: Annotated[
        TenantContext, Depends(require_roles(Role.platform_admin, Role.tenant_admin))
    ],
    session: Annotated[AsyncSession, Depends(tenant_session)],
) -> TeamConfigOut:
    repo = TeamRepository(session, context)
    source = await repo.get_config(team_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Team not found")
    draft = await repo.get_latest_draft(team_id)
    published = (
        await repo.get_version(source.published_version_id)
        if source.published_version_id
        else None
    )
    editable = draft or published
    if editable is None:
        raise HTTPException(status_code=400, detail="Team has no version to clone")

    async def slug_taken(slug: str) -> bool:
        return await repo.get_config_by_slug(slug) is not None

    try:
        new_slug = await unique_copy_slug(source.slug, slug_taken)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    members = await repo.members(editable.id)
    tools = [
        {
            "tool_key": binding.tool_key,
            "tool_definition_id": binding.tool_definition_id,
            "config": binding.config or {},
            "credential_id": binding.credential_id,
        }
        for binding in await repo.bindings(editable.id)
    ]
    config = await repo.create_config(
        slug=new_slug,
        name=copy_name(source.name),
        description=source.description,
    )
    try:
        await repo.create_draft(
            config_id=config.id,
            instructions=editable.instructions,
            mode=editable.mode,
            model_id=editable.model_id,
            temperature=editable.temperature,
            member_config_ids=[member.agent_config_id for member in members],
            tools=tools,
        )
    except (LookupError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return await team_config_out(repo, config)


@router.patch("/{team_id}", response_model=TeamConfigOut)
async def update_team(
    team_id: uuid.UUID,
    body: TeamUpdateIn,
    context: Annotated[
        TenantContext, Depends(require_roles(Role.platform_admin, Role.tenant_admin))
    ],
    session: Annotated[AsyncSession, Depends(tenant_session)],
) -> TeamConfigOut:
    repo = TeamRepository(session, context)
    config = await repo.update_config(team_id, name=body.name, description=body.description)
    if config is None:
        raise HTTPException(status_code=404, detail="Team not found")

    version_fields = (
        body.instructions,
        body.mode,
        body.model_id,
        body.temperature,
        body.member_config_ids,
        body.tools,
    )
    if any(value is not None for value in version_fields):
        editable = await repo.get_latest_draft(team_id)
        if editable is None and config.published_version_id:
            editable = await repo.get_version(config.published_version_id)
        existing_members = (
            [member.agent_config_id for member in await repo.members(editable.id)]
            if editable
            else []
        )
        existing_tools = (
            [
                {
                    "tool_key": binding.tool_key,
                    "tool_definition_id": binding.tool_definition_id,
                    "config": binding.config or {},
                    "credential_id": binding.credential_id,
                }
                for binding in await repo.bindings(editable.id)
            ]
            if editable
            else []
        )
        try:
            await repo.create_draft(
                config_id=team_id,
                instructions=body.instructions
                or (
                    editable.instructions
                    if editable
                    else "Coordinate the team specialists and return one clear answer."
                ),
                mode=body.mode or (editable.mode if editable else "coordinate"),
                model_id=body.model_id
                or (editable.model_id if editable else "openai:gpt-4.1-mini"),
                temperature=body.temperature
                if body.temperature is not None
                else (editable.temperature if editable else 0.2),
                member_config_ids=body.member_config_ids
                if body.member_config_ids is not None
                else existing_members,
                tools=[tool.model_dump() for tool in body.tools]
                if body.tools is not None
                else existing_tools,
            )
        except (LookupError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return await team_config_out(repo, config)


@router.post("/{team_id}/publish", response_model=TeamConfigOut)
async def publish_team(
    team_id: uuid.UUID,
    context: Annotated[
        TenantContext, Depends(require_roles(Role.platform_admin, Role.tenant_admin))
    ],
    session: Annotated[AsyncSession, Depends(tenant_session)],
) -> TeamConfigOut:
    repo = TeamRepository(session, context)
    draft = await repo.get_latest_draft(team_id)
    if draft is None:
        raise HTTPException(status_code=400, detail="No draft version to publish")
    try:
        await repo.publish(draft.id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    config = await repo.get_config(team_id)
    assert config is not None
    return await team_config_out(repo, config)


@router.get("/{team_id}/versions", response_model=list[TeamVersionSummaryOut])
async def list_team_versions(
    team_id: uuid.UUID,
    context: Annotated[
        TenantContext, Depends(require_roles(Role.platform_admin, Role.tenant_admin))
    ],
    session: Annotated[AsyncSession, Depends(tenant_session)],
) -> list[TeamVersionSummaryOut]:
    repo = TeamRepository(session, context)
    config = await repo.get_config(team_id)
    if config is None:
        raise HTTPException(status_code=404, detail="Team not found")
    summaries: list[TeamVersionSummaryOut] = []
    for version in await repo.list_versions(team_id):
        members = await repo.members(version.id)
        summaries.append(
            TeamVersionSummaryOut(
                id=version.id,
                version=version.version,
                status=version.status.value,
                mode=version.mode,
                member_count=len(members),
                is_live=config.published_version_id == version.id,
                created_at=version.created_at,
            )
        )
    return summaries


@router.get("/{team_id}/versions/{version_id}", response_model=TeamVersionOut)
async def get_team_version(
    team_id: uuid.UUID,
    version_id: uuid.UUID,
    context: Annotated[
        TenantContext, Depends(require_roles(Role.platform_admin, Role.tenant_admin))
    ],
    session: Annotated[AsyncSession, Depends(tenant_session)],
) -> TeamVersionOut:
    repo = TeamRepository(session, context)
    config = await repo.get_config(team_id)
    if config is None:
        raise HTTPException(status_code=404, detail="Team not found")
    version = await repo.get_version(version_id, allow_draft=True)
    if version is None or version.team_config_id != team_id:
        raise HTTPException(status_code=404, detail="Team version not found")
    agent_repo = AgentRepository(session, context)
    return await _version_out(repo, agent_repo, version)


@router.post("/{team_id}/versions/{version_id}/restore", response_model=TeamConfigOut)
async def restore_team_version(
    team_id: uuid.UUID,
    version_id: uuid.UUID,
    body: TeamRestoreIn,
    context: Annotated[
        TenantContext, Depends(require_roles(Role.platform_admin, Role.tenant_admin))
    ],
    session: Annotated[AsyncSession, Depends(tenant_session)],
) -> TeamConfigOut:
    repo = TeamRepository(session, context)
    try:
        await repo.restore_version(team_id, version_id, as_draft=body.as_draft)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    config = await repo.get_config(team_id)
    assert config is not None
    return await team_config_out(repo, config)


@router.delete("/{team_id}", status_code=204)
async def delete_team(
    team_id: uuid.UUID,
    context: Annotated[
        TenantContext, Depends(require_roles(Role.platform_admin, Role.tenant_admin))
    ],
    session: Annotated[AsyncSession, Depends(tenant_session)],
) -> None:
    repo = TeamRepository(session, context)
    try:
        await repo.delete_config(team_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except IntegrityError as exc:
        raise HTTPException(
            status_code=409,
            detail="Team is referenced by sessions or schedules and cannot be deleted",
        ) from exc
