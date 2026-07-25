import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.copy_helpers import copy_name, unique_copy_slug
from app.api.schemas import (
    WorkflowAssignmentsIn,
    WorkflowAssignmentsOut,
    WorkflowCatalogItemOut,
    WorkflowCatalogPageOut,
    WorkflowConfigOut,
    WorkflowCreateIn,
    WorkflowRestoreIn,
    WorkflowStepOut,
    WorkflowUpdateIn,
    WorkflowVersionOut,
    WorkflowVersionSummaryOut,
)
from app.auth.dependencies import require_roles, require_tenant
from app.db.models import Role, WorkflowConfig, WorkflowVersion
from app.db.repositories import AgentRepository, TeamRepository, WorkflowRepository
from app.db.session import tenant_session
from app.tenancy.context import TenantContext

router = APIRouter(prefix="/admin/workflows", tags=["admin-workflows"])


async def _version_out(
    repo: WorkflowRepository, version: WorkflowVersion
) -> WorkflowVersionOut:
    agents = AgentRepository(repo.session, repo.context)
    teams = TeamRepository(repo.session, repo.context)
    output: list[WorkflowStepOut] = []
    for step in await repo.steps(version.id):
        if step.target_type == "agent":
            agent_config = await agents.get_config(
                step.agent_config_id  # type: ignore[arg-type]
            )
            agent_version = await agents.get_version(
                step.agent_version_id, allow_draft=True  # type: ignore[arg-type]
            )
            config_id = step.agent_config_id
            version_id = step.agent_version_id
            target_name = agent_config.name if agent_config else ""
            target_slug = agent_config.slug if agent_config else ""
            target_number = agent_version.version if agent_version else 0
            target_status = agent_version.status.value if agent_version else ""
        else:
            team_config = await teams.get_config(
                step.team_config_id  # type: ignore[arg-type]
            )
            team_version = await teams.get_version(
                step.team_version_id, allow_draft=True  # type: ignore[arg-type]
            )
            config_id = step.team_config_id
            version_id = step.team_version_id
            target_name = team_config.name if team_config else ""
            target_slug = team_config.slug if team_config else ""
            target_number = team_version.version if team_version else 0
            target_status = team_version.status.value if team_version else ""
        if not target_name or config_id is None or version_id is None:
            continue
        output.append(
            WorkflowStepOut(
                id=step.id,
                position=step.position,
                name=step.name,
                target_type=step.target_type,
                target_config_id=config_id,
                target_version_id=version_id,
                target_name=target_name,
                target_slug=target_slug,
                target_version=target_number,
                target_status=target_status,
                condition_expression=step.condition_expression,
            )
        )
    return WorkflowVersionOut(
        id=version.id,
        version=version.version,
        status=version.status.value,
        mode=version.mode,
        steps=output,
        created_at=version.created_at,
    )


async def workflow_config_out(
    repo: WorkflowRepository, config: WorkflowConfig
) -> WorkflowConfigOut:
    draft = await repo.get_latest_draft(config.id)
    published = (
        await repo.get_version(config.published_version_id)
        if config.published_version_id
        else None
    )
    return WorkflowConfigOut(
        id=config.id,
        slug=config.slug,
        name=config.name,
        description=config.description,
        published_version_id=config.published_version_id,
        updated_at=config.updated_at,
        draft=await _version_out(repo, draft) if draft else None,
        published=await _version_out(repo, published) if published else None,
    )


@router.get("/catalog", response_model=WorkflowCatalogPageOut)
async def list_workflow_catalog(
    context: Annotated[TenantContext, Depends(require_tenant)],
    session: Annotated[AsyncSession, Depends(tenant_session)],
    q: str | None = None,
    status: str = "all",
    page: int = 1,
    page_size: int = 25,
) -> WorkflowCatalogPageOut:
    repo = WorkflowRepository(session, context)
    configs, total = await repo.search_configs(
        q=q,
        status=None if status == "all" else status,
        page=page,
        page_size=page_size,
    )
    items: list[WorkflowCatalogItemOut] = []
    for config in configs:
        draft = await repo.get_latest_draft(config.id)
        published = (
            await repo.get_version(config.published_version_id)
            if config.published_version_id
            else None
        )
        editable = draft or published
        step_count = len(await repo.steps(editable.id)) if editable else 0
        items.append(
            WorkflowCatalogItemOut(
                id=config.id,
                slug=config.slug,
                name=config.name,
                status="published" if published else "draft",
                mode=editable.mode if editable else "sequential",
                step_count=step_count,
                published_version=published.version if published else None,
                updated_at=config.updated_at,
            )
        )
    return WorkflowCatalogPageOut(
        items=items,
        total=total,
        page=max(1, page),
        page_size=min(max(1, page_size), 100),
    )


@router.get("", response_model=list[WorkflowConfigOut])
async def list_workflows(
    context: Annotated[TenantContext, Depends(require_tenant)],
    session: Annotated[AsyncSession, Depends(tenant_session)],
) -> list[WorkflowConfigOut]:
    repo = WorkflowRepository(session, context)
    return [await workflow_config_out(repo, config) for config in await repo.list_configs()]


@router.get(
    "/{workflow_id}/assignments",
    response_model=WorkflowAssignmentsOut,
)
async def get_workflow_assignments(
    workflow_id: uuid.UUID,
    context: Annotated[
        TenantContext, Depends(require_roles(Role.platform_admin, Role.tenant_admin))
    ],
    session: Annotated[AsyncSession, Depends(tenant_session)],
) -> WorkflowAssignmentsOut:
    repo = WorkflowRepository(session, context)
    if await repo.get_config(workflow_id) is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return WorkflowAssignmentsOut(
        workflow_id=workflow_id,
        user_ids=await repo.assigned_user_ids(workflow_id),
    )


@router.put(
    "/{workflow_id}/assignments",
    response_model=WorkflowAssignmentsOut,
)
async def replace_workflow_assignments(
    workflow_id: uuid.UUID,
    body: WorkflowAssignmentsIn,
    context: Annotated[
        TenantContext, Depends(require_roles(Role.platform_admin, Role.tenant_admin))
    ],
    session: Annotated[AsyncSession, Depends(tenant_session)],
) -> WorkflowAssignmentsOut:
    repo = WorkflowRepository(session, context)
    try:
        user_ids = await repo.replace_assignments(workflow_id, body.user_ids)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return WorkflowAssignmentsOut(workflow_id=workflow_id, user_ids=user_ids)


@router.post("", response_model=WorkflowConfigOut, status_code=201)
async def create_workflow(
    body: WorkflowCreateIn,
    context: Annotated[
        TenantContext, Depends(require_roles(Role.platform_admin, Role.tenant_admin))
    ],
    session: Annotated[AsyncSession, Depends(tenant_session)],
) -> WorkflowConfigOut:
    repo = WorkflowRepository(session, context)
    config = await repo.create_config(
        slug=body.slug, name=body.name, description=body.description
    )
    if body.steps:
        try:
            await repo.create_draft(
                config_id=config.id,
                mode=body.mode,
                steps=[step.model_dump() for step in body.steps],
            )
        except (LookupError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return await workflow_config_out(repo, config)


@router.get("/{workflow_id}", response_model=WorkflowConfigOut)
async def get_workflow(
    workflow_id: uuid.UUID,
    context: Annotated[TenantContext, Depends(require_tenant)],
    session: Annotated[AsyncSession, Depends(tenant_session)],
) -> WorkflowConfigOut:
    repo = WorkflowRepository(session, context)
    config = await repo.get_config(workflow_id)
    if config is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return await workflow_config_out(repo, config)


@router.post("/{workflow_id}/clone", response_model=WorkflowConfigOut, status_code=201)
async def clone_workflow(
    workflow_id: uuid.UUID,
    context: Annotated[
        TenantContext, Depends(require_roles(Role.platform_admin, Role.tenant_admin))
    ],
    session: Annotated[AsyncSession, Depends(tenant_session)],
) -> WorkflowConfigOut:
    repo = WorkflowRepository(session, context)
    source = await repo.get_config(workflow_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    draft = await repo.get_latest_draft(workflow_id)
    published = (
        await repo.get_version(source.published_version_id)
        if source.published_version_id
        else None
    )
    editable = draft or published

    async def slug_taken(slug: str) -> bool:
        return await repo.get_config_by_slug(slug) is not None

    try:
        new_slug = await unique_copy_slug(source.slug, slug_taken)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    config = await repo.create_config(
        slug=new_slug,
        name=copy_name(source.name),
        description=source.description,
    )
    if editable is not None:
        steps = await repo.steps(editable.id)
        if steps:
            try:
                await repo.create_draft(
                    config_id=config.id,
                    mode=editable.mode,
                    steps=[
                        {
                            "name": step.name,
                            "target_type": step.target_type,
                            "target_config_id": (
                                step.agent_config_id
                                if step.target_type == "agent"
                                else step.team_config_id
                            ),
                            "condition_expression": step.condition_expression,
                        }
                        for step in steps
                    ],
                )
            except (LookupError, ValueError) as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
    return await workflow_config_out(repo, config)


@router.patch("/{workflow_id}", response_model=WorkflowConfigOut)
async def update_workflow(
    workflow_id: uuid.UUID,
    body: WorkflowUpdateIn,
    context: Annotated[
        TenantContext, Depends(require_roles(Role.platform_admin, Role.tenant_admin))
    ],
    session: Annotated[AsyncSession, Depends(tenant_session)],
) -> WorkflowConfigOut:
    repo = WorkflowRepository(session, context)
    config = await repo.update_config(
        workflow_id, name=body.name, description=body.description
    )
    if config is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    if body.mode is not None or body.steps is not None:
        editable = await repo.get_latest_draft(workflow_id)
        if editable is None and config.published_version_id:
            editable = await repo.get_version(config.published_version_id)
        current_steps = []
        if editable:
            current_steps = [
                {
                    "name": step.name,
                    "target_type": step.target_type,
                    "target_config_id": (
                        step.agent_config_id
                        if step.target_type == "agent"
                        else step.team_config_id
                    ),
                    "condition_expression": step.condition_expression,
                }
                for step in await repo.steps(editable.id)
            ]
        try:
            await repo.create_draft(
                config_id=workflow_id,
                mode=body.mode or (editable.mode if editable else "sequential"),
                steps=(
                    [step.model_dump() for step in body.steps]
                    if body.steps is not None
                    else current_steps
                ),
            )
        except (LookupError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return await workflow_config_out(repo, config)


@router.post("/{workflow_id}/publish", response_model=WorkflowConfigOut)
async def publish_workflow(
    workflow_id: uuid.UUID,
    context: Annotated[
        TenantContext, Depends(require_roles(Role.platform_admin, Role.tenant_admin))
    ],
    session: Annotated[AsyncSession, Depends(tenant_session)],
) -> WorkflowConfigOut:
    repo = WorkflowRepository(session, context)
    draft = await repo.get_latest_draft(workflow_id)
    if draft is None:
        raise HTTPException(status_code=400, detail="No draft version to publish")
    try:
        await repo.publish(draft.id)
    except (LookupError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    config = await repo.get_config(workflow_id)
    assert config is not None
    return await workflow_config_out(repo, config)


@router.get("/{workflow_id}/versions", response_model=list[WorkflowVersionSummaryOut])
async def list_workflow_versions(
    workflow_id: uuid.UUID,
    context: Annotated[TenantContext, Depends(require_tenant)],
    session: Annotated[AsyncSession, Depends(tenant_session)],
) -> list[WorkflowVersionSummaryOut]:
    repo = WorkflowRepository(session, context)
    config = await repo.get_config(workflow_id)
    if config is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    summaries: list[WorkflowVersionSummaryOut] = []
    for version in await repo.list_versions(workflow_id):
        steps = await repo.steps(version.id)
        summaries.append(
            WorkflowVersionSummaryOut(
                id=version.id,
                version=version.version,
                status=version.status.value,
                mode=version.mode,
                step_count=len(steps),
                is_live=config.published_version_id == version.id,
                created_at=version.created_at,
            )
        )
    return summaries


@router.get("/{workflow_id}/versions/{version_id}", response_model=WorkflowVersionOut)
async def get_workflow_version(
    workflow_id: uuid.UUID,
    version_id: uuid.UUID,
    context: Annotated[TenantContext, Depends(require_tenant)],
    session: Annotated[AsyncSession, Depends(tenant_session)],
) -> WorkflowVersionOut:
    repo = WorkflowRepository(session, context)
    config = await repo.get_config(workflow_id)
    if config is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    version = await repo.get_version(version_id, allow_draft=True)
    if version is None or version.workflow_config_id != workflow_id:
        raise HTTPException(status_code=404, detail="Workflow version not found")
    return await _version_out(repo, version)


@router.post(
    "/{workflow_id}/versions/{version_id}/restore", response_model=WorkflowConfigOut
)
async def restore_workflow_version(
    workflow_id: uuid.UUID,
    version_id: uuid.UUID,
    body: WorkflowRestoreIn,
    context: Annotated[
        TenantContext, Depends(require_roles(Role.platform_admin, Role.tenant_admin))
    ],
    session: Annotated[AsyncSession, Depends(tenant_session)],
) -> WorkflowConfigOut:
    repo = WorkflowRepository(session, context)
    try:
        await repo.restore_version(workflow_id, version_id, as_draft=body.as_draft)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    config = await repo.get_config(workflow_id)
    assert config is not None
    return await workflow_config_out(repo, config)


@router.delete("/{workflow_id}", status_code=204)
async def delete_workflow(
    workflow_id: uuid.UUID,
    context: Annotated[
        TenantContext, Depends(require_roles(Role.platform_admin, Role.tenant_admin))
    ],
    session: Annotated[AsyncSession, Depends(tenant_session)],
) -> None:
    repo = WorkflowRepository(session, context)
    try:
        await repo.delete_config(workflow_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
