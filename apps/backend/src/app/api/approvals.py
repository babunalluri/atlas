import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.factory import (
    AgentFactoryService,
    RuntimeRequest,
    TeamFactoryService,
    TeamRuntimeRequest,
    WorkflowFactoryService,
    WorkflowRuntimeRequest,
)
from app.api.schemas import ApprovalOut, ApprovalResolveIn
from app.auth.dependencies import require_roles
from app.db.models import Role
from app.db.repositories import ApprovalRepository, SessionRepository
from app.db.session import tenant_session
from app.observability.tracing import redact
from app.tenancy.context import TenantContext

router = APIRouter(prefix="/admin/approvals", tags=["admin-approvals"])


def _out(row: object, external_session_id: str | None = None) -> ApprovalOut:
    return ApprovalOut(
        id=row.id,  # type: ignore[attr-defined]
        tool_name=row.tool_name,  # type: ignore[attr-defined]
        status=row.status.value,  # type: ignore[attr-defined]
        redacted_arguments=redact(row.redacted_arguments),  # type: ignore[attr-defined]
        resolved_by=row.resolved_by,  # type: ignore[attr-defined]
        decision_reason=row.decision_reason,  # type: ignore[attr-defined]
        session_id=external_session_id,
        run_id=row.run_id,  # type: ignore[attr-defined]
        continuation_error=row.continuation_error,  # type: ignore[attr-defined]
        expires_at=row.expires_at,  # type: ignore[attr-defined]
        created_at=row.created_at,  # type: ignore[attr-defined]
    )


def _find_requirement(run_output: Any, requirement_id: str) -> tuple[Any | None, str]:
    """Locate an agent/team requirement or workflow step/executor requirement."""
    for item in list(getattr(run_output, "requirements", None) or []):
        if str(getattr(item, "id", "")) == requirement_id:
            return item, "agent"
    for step in list(getattr(run_output, "step_requirements", None) or []):
        if str(getattr(step, "id", "")) == requirement_id:
            return step, "workflow"
        for nested in list(getattr(step, "executor_requirements", None) or []):
            if str(getattr(nested, "id", "")) == requirement_id:
                return nested, "workflow"
    return None, ""


@router.get("", response_model=list[ApprovalOut])
async def list_approvals(
    context: Annotated[
        TenantContext, Depends(require_roles(Role.platform_admin, Role.tenant_admin))
    ],
    session: Annotated[AsyncSession, Depends(tenant_session)],
) -> list[ApprovalOut]:
    repo = ApprovalRepository(session, context)
    rows = await repo.list_pending()
    sessions = SessionRepository(session, context)
    output: list[ApprovalOut] = []
    for row in rows:
        conversation = await sessions.get(row.session_id)
        output.append(_out(row, conversation.external_session_id if conversation else None))
    return output


@router.post("/{approval_id}/resolve", response_model=ApprovalOut)
async def resolve_approval(
    approval_id: uuid.UUID,
    body: ApprovalResolveIn,
    context: Annotated[
        TenantContext, Depends(require_roles(Role.platform_admin, Role.tenant_admin))
    ],
    session: Annotated[AsyncSession, Depends(tenant_session)],
) -> ApprovalOut:
    repo = ApprovalRepository(session, context)
    row = await repo.get(approval_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Pending approval not found")
    if row.status.value != "pending":
        raise HTTPException(status_code=409, detail="Approval is already resolved")

    sessions = SessionRepository(session, context)
    conversation = await sessions.get(row.session_id)
    if conversation is None:
        raise HTTPException(status_code=409, detail="Paused session no longer exists")

    # Rebuild the immutable pinned component, load the exact native run, apply
    # the decision to its persisted requirement, and continue it.
    runtime_context = TenantContext(
        tenant_id=context.tenant_id,
        user_id=conversation.user_id,
        role=context.role,
        clerk_org_id=context.clerk_org_id,
        scopes=context.scopes,
    )
    agent_factory = AgentFactoryService(session, runtime_context)
    if conversation.target_type == "team":
        if conversation.team_version_id is None:
            raise HTTPException(status_code=409, detail="Pinned team version is missing")
        component = await TeamFactoryService(agent_factory).create(
            TeamRuntimeRequest(
                version_id=conversation.team_version_id,
                session_id=conversation.external_session_id,
            )
        )
    elif conversation.target_type == "workflow":
        if conversation.workflow_version_id is None:
            raise HTTPException(status_code=409, detail="Pinned workflow version is missing")
        component = await WorkflowFactoryService(agent_factory).create(
            WorkflowRuntimeRequest(
                version_id=conversation.workflow_version_id,
                session_id=conversation.external_session_id,
            )
        )
    else:
        if conversation.agent_version_id is None:
            raise HTTPException(status_code=409, detail="Pinned agent version is missing")
        component = await agent_factory.create(
            RuntimeRequest(
                version_id=conversation.agent_version_id,
                session_id=conversation.external_session_id,
                pin_session=False,
            )
        )

    run_output = await component.aget_run_output(
        run_id=row.run_id,
        session_id=conversation.runtime_session_id,
        user_id=conversation.runtime_user_id,
    )
    if run_output is None:
        raise HTTPException(status_code=409, detail="Paused run was not found or expired")
    requirement, kind = _find_requirement(run_output, row.requirement_id)
    if requirement is None:
        raise HTTPException(status_code=409, detail="Approval requirement is stale")
    try:
        resolved = await repo.resolve(approval_id, body.approved, body.reason)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if resolved is None:
        raise HTTPException(status_code=409, detail="Approval is stale, expired, or resolved")
    row = resolved
    try:
        if body.approved:
            if hasattr(requirement, "confirm"):
                requirement.confirm()
        else:
            if hasattr(requirement, "reject"):
                requirement.reject(body.reason or "Rejected by tenant administrator")
        if kind == "workflow" or conversation.target_type == "workflow":
            await component.acontinue_run(
                run_response=run_output,
                step_requirements=list(getattr(run_output, "step_requirements", None) or []),
                stream=False,
                session_id=conversation.runtime_session_id,
            )
        else:
            await component.acontinue_run(
                run_response=run_output,
                requirements=list(getattr(run_output, "requirements", None) or []),
                stream=False,
                session_id=conversation.runtime_session_id,
                user_id=conversation.runtime_user_id,
            )
        await repo.mark_continued(approval_id)
        conversation.status = "completed"
    except Exception:
        await repo.mark_continued(approval_id, error="Provider continuation failed")
        conversation.status = "error"
    return _out(row, conversation.external_session_id)
