import hashlib
import json
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Select, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AgentConfig,
    AgentStatus,
    AgentToolBinding,
    AgentVersion,
    ApprovalBinding,
    ApprovalStatus,
    AuditEvent,
    ConversationSession,
    KnowledgeBase,
    KnowledgeChunk,
    KnowledgeSource,
    Membership,
    Role,
    ServiceAccount,
    TeamConfig,
    TeamMember,
    TeamVersion,
    Tenant,
    TenantCredential,
    ToolDefinition,
    ToolDefinitionVersion,
    PlatformPythonPackage,
    WorkflowAssignment,
    WorkflowConfig,
    WorkflowStep,
    WorkflowVersion,
)
from app.tenancy.context import TenantContext
from app.tenancy.ids import new_id, validate_slug


class TenantRepository:
    def __init__(self, session: AsyncSession, context: TenantContext) -> None:
        session_tenant = session.info.get("tenant_id")
        if session_tenant is not None and session_tenant != context.tenant_id:
            raise RuntimeError("Database session tenant does not match request tenant")
        self.session = session
        self.context = context

    def scoped(self, statement: Select[Any], model: type[Any]) -> Select[Any]:
        return statement.where(model.tenant_id == self.context.tenant_id)

    async def audit(
        self,
        *,
        action: str,
        resource_type: str,
        resource_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.session.add(
            AuditEvent(
                id=new_id(),
                tenant_id=self.context.tenant_id,
                actor_id=self.context.user_id,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                details=details or {},
            )
        )


class TenantAdminRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_slug(self, slug: str) -> Tenant | None:
        return await self.session.scalar(select(Tenant).where(Tenant.slug == slug))

    async def get_by_clerk_org(self, clerk_org_id: str) -> Tenant | None:
        return await self.session.scalar(select(Tenant).where(Tenant.clerk_org_id == clerk_org_id))

    async def ensure(
        self, *, clerk_org_id: str, slug: str, name: str, branding: dict[str, Any] | None = None
    ) -> Tenant:
        existing = await self.get_by_clerk_org(clerk_org_id)
        if existing:
            return existing
        tenant = Tenant(
            id=new_id(),
            clerk_org_id=clerk_org_id,
            slug=validate_slug(slug),
            name=name,
            branding=branding or {},
        )
        self.session.add(tenant)
        await self.session.flush()
        return tenant


class AgentRepository(TenantRepository):
    async def list_configs(self) -> Sequence[AgentConfig]:
        result = await self.session.scalars(
            self.scoped(select(AgentConfig).order_by(AgentConfig.created_at.desc()), AgentConfig)
        )
        return result.all()

    async def search_configs(
        self,
        *,
        q: str | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 25,
    ) -> tuple[Sequence[AgentConfig], int]:
        page = max(1, page)
        page_size = min(max(1, page_size), 100)
        filters = [AgentConfig.tenant_id == self.context.tenant_id]
        if q and q.strip():
            pattern = f"%{q.strip().lower()}%"
            filters.append(
                or_(
                    func.lower(AgentConfig.name).like(pattern),
                    func.lower(AgentConfig.slug).like(pattern),
                )
            )
        if status == "published":
            filters.append(AgentConfig.published_version_id.is_not(None))
        elif status == "draft":
            filters.append(AgentConfig.published_version_id.is_(None))
        total = await self.session.scalar(
            select(func.count()).select_from(AgentConfig).where(*filters)
        )
        rows = await self.session.scalars(
            select(AgentConfig)
            .where(*filters)
            .order_by(AgentConfig.updated_at.desc(), AgentConfig.name.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return rows.all(), int(total or 0)

    async def get_config(self, config_id: uuid.UUID) -> AgentConfig | None:
        return await self.session.scalar(
            self.scoped(select(AgentConfig).where(AgentConfig.id == config_id), AgentConfig)
        )

    async def get_config_by_slug(self, slug: str) -> AgentConfig | None:
        return await self.session.scalar(
            self.scoped(select(AgentConfig).where(AgentConfig.slug == slug), AgentConfig)
        )

    async def create_config(
        self, *, slug: str, name: str, description: str | None = None
    ) -> AgentConfig:
        config = AgentConfig(
            id=new_id(),
            tenant_id=self.context.tenant_id,
            slug=validate_slug(slug),
            name=name,
            description=description,
        )
        self.session.add(config)
        await self.session.flush()
        await self.audit(
            action="agent.create",
            resource_type="agent_config",
            resource_id=str(config.id),
        )
        return config

    async def update_config(
        self, config_id: uuid.UUID, *, name: str | None = None, description: str | None = None
    ) -> AgentConfig | None:
        config = await self.get_config(config_id)
        if config is None:
            return None
        if name is not None:
            config.name = name
        if description is not None:
            config.description = description
        await self.session.flush()
        return config

    async def get_version(
        self, version_id: uuid.UUID, *, allow_draft: bool = False
    ) -> AgentVersion | None:
        statement = select(AgentVersion).where(AgentVersion.id == version_id)
        if not allow_draft:
            statement = statement.where(AgentVersion.status == AgentStatus.published)
        return await self.session.scalar(self.scoped(statement, AgentVersion))

    async def get_latest_draft(self, config_id: uuid.UUID) -> AgentVersion | None:
        return await self.session.scalar(
            self.scoped(
                select(AgentVersion)
                .where(
                    AgentVersion.agent_config_id == config_id,
                    AgentVersion.status == AgentStatus.draft,
                )
                .order_by(AgentVersion.version.desc()),
                AgentVersion,
            )
        )

    async def list_versions(self, config_id: uuid.UUID) -> Sequence[AgentVersion]:
        rows = await self.session.scalars(
            self.scoped(
                select(AgentVersion)
                .where(AgentVersion.agent_config_id == config_id)
                .order_by(AgentVersion.version.desc()),
                AgentVersion,
            )
        )
        return rows.all()

    async def bindings(self, version_id: uuid.UUID) -> Sequence[AgentToolBinding]:
        rows = await self.session.scalars(
            self.scoped(
                select(AgentToolBinding).where(AgentToolBinding.agent_version_id == version_id),
                AgentToolBinding,
            )
        )
        return rows.all()

    async def create_draft(
        self,
        *,
        config_id: uuid.UUID,
        instructions: str,
        model_id: str,
        temperature: float,
        memory_mode: str = "session",
        tools: list[dict[str, Any]] | None = None,
        knowledge_base_id: uuid.UUID | None = None,
    ) -> AgentVersion:
        config = await self.get_config(config_id)
        if config is None:
            raise LookupError("Agent config not found")
        next_version = (
            await self.session.scalar(
                self.scoped(
                    select(func.coalesce(func.max(AgentVersion.version), 0)).where(
                        AgentVersion.agent_config_id == config_id
                    ),
                    AgentVersion,
                )
            )
            or 0
        ) + 1
        version = AgentVersion(
            id=new_id(),
            tenant_id=self.context.tenant_id,
            agent_config_id=config_id,
            version=next_version,
            status=AgentStatus.draft,
            instructions=instructions,
            model_id=model_id,
            temperature=temperature,
            memory_mode=memory_mode,
            team_config={"knowledge_base_id": str(knowledge_base_id)}
            if knowledge_base_id
            else None,
            created_by=self.context.user_id,
        )
        self.session.add(version)
        await self.session.flush()
        for tool in tools or []:
            credential_id = tool.get("credential_id")
            definition_id = tool.get("tool_definition_id")
            if (
                credential_id
                and await CredentialRepository(self.session, self.context).get(credential_id)
                is None
            ):
                raise LookupError("Tool credential not found for tenant")
            if definition_id:
                definition = await ToolDefinitionRepository(self.session, self.context).get(
                    definition_id
                )
                if definition is None or not definition.active:
                    raise LookupError("Active tool definition not found for tenant")
            self.session.add(
                AgentToolBinding(
                    id=new_id(),
                    tenant_id=self.context.tenant_id,
                    agent_version_id=version.id,
                    tool_key=tool.get("tool_key"),
                    tool_definition_id=definition_id,
                    config=tool.get("config") or {},
                    credential_id=credential_id,
                )
            )
        await self.session.flush()
        await self.audit(
            action="agent.draft",
            resource_type="agent_version",
            resource_id=str(version.id),
            details={"version": next_version},
        )
        return version

    async def publish(self, version_id: uuid.UUID) -> AgentVersion:
        version = await self.get_version(version_id, allow_draft=True)
        if version is None:
            raise LookupError("Agent version not found")
        if version.status != AgentStatus.draft:
            raise ValueError("Only draft versions can be published")
        version.status = AgentStatus.published
        await self.session.execute(
            update(AgentConfig)
            .where(
                AgentConfig.id == version.agent_config_id,
                AgentConfig.tenant_id == self.context.tenant_id,
            )
            .values(published_version_id=version.id)
        )
        await self.session.flush()
        await self.audit(
            action="agent.publish",
            resource_type="agent_version",
            resource_id=str(version.id),
        )
        return version


class TeamRepository(TenantRepository):
    async def list_configs(self) -> Sequence[TeamConfig]:
        rows = await self.session.scalars(
            self.scoped(select(TeamConfig).order_by(TeamConfig.created_at.desc()), TeamConfig)
        )
        return rows.all()

    async def search_configs(
        self,
        *,
        q: str | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 25,
    ) -> tuple[Sequence[TeamConfig], int]:
        page = max(1, page)
        page_size = min(max(1, page_size), 100)
        filters = [TeamConfig.tenant_id == self.context.tenant_id]
        if q and q.strip():
            pattern = f"%{q.strip().lower()}%"
            filters.append(
                or_(
                    func.lower(TeamConfig.name).like(pattern),
                    func.lower(TeamConfig.slug).like(pattern),
                )
            )
        if status == "published":
            filters.append(TeamConfig.published_version_id.is_not(None))
        elif status == "draft":
            filters.append(TeamConfig.published_version_id.is_(None))
        total = await self.session.scalar(
            select(func.count()).select_from(TeamConfig).where(*filters)
        )
        rows = await self.session.scalars(
            select(TeamConfig)
            .where(*filters)
            .order_by(TeamConfig.updated_at.desc(), TeamConfig.name.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return rows.all(), int(total or 0)

    async def get_config(self, config_id: uuid.UUID) -> TeamConfig | None:
        return await self.session.scalar(
            self.scoped(select(TeamConfig).where(TeamConfig.id == config_id), TeamConfig)
        )

    async def get_config_by_slug(self, slug: str) -> TeamConfig | None:
        return await self.session.scalar(
            self.scoped(select(TeamConfig).where(TeamConfig.slug == slug), TeamConfig)
        )

    async def create_config(
        self, *, slug: str, name: str, description: str | None = None
    ) -> TeamConfig:
        config = TeamConfig(
            id=new_id(),
            tenant_id=self.context.tenant_id,
            slug=validate_slug(slug),
            name=name,
            description=description,
        )
        self.session.add(config)
        await self.session.flush()
        await self.audit(
            action="team.create", resource_type="team_config", resource_id=str(config.id)
        )
        return config

    async def update_config(
        self, config_id: uuid.UUID, *, name: str | None = None, description: str | None = None
    ) -> TeamConfig | None:
        config = await self.get_config(config_id)
        if config is None:
            return None
        if name is not None:
            config.name = name
        if description is not None:
            config.description = description
        await self.session.flush()
        return config

    async def get_version(
        self, version_id: uuid.UUID, *, allow_draft: bool = False
    ) -> TeamVersion | None:
        statement = select(TeamVersion).where(TeamVersion.id == version_id)
        if not allow_draft:
            statement = statement.where(TeamVersion.status == AgentStatus.published)
        return await self.session.scalar(self.scoped(statement, TeamVersion))

    async def get_latest_draft(self, config_id: uuid.UUID) -> TeamVersion | None:
        return await self.session.scalar(
            self.scoped(
                select(TeamVersion)
                .where(
                    TeamVersion.team_config_id == config_id,
                    TeamVersion.status == AgentStatus.draft,
                )
                .order_by(TeamVersion.version.desc()),
                TeamVersion,
            )
        )

    async def members(self, version_id: uuid.UUID) -> Sequence[TeamMember]:
        rows = await self.session.scalars(
            self.scoped(
                select(TeamMember)
                .where(TeamMember.team_version_id == version_id)
                .order_by(TeamMember.position),
                TeamMember,
            )
        )
        return rows.all()

    async def create_draft(
        self,
        *,
        config_id: uuid.UUID,
        instructions: str,
        mode: str,
        model_id: str,
        temperature: float,
        member_config_ids: list[uuid.UUID],
    ) -> TeamVersion:
        if mode not in {"route", "coordinate"}:
            raise ValueError("Team mode must be route or coordinate")
        if len(set(member_config_ids)) != len(member_config_ids):
            raise ValueError("A team cannot contain the same agent more than once")
        config = await self.get_config(config_id)
        if config is None:
            raise LookupError("Team config not found")

        agent_repo = AgentRepository(self.session, self.context)
        resolved_members: list[tuple[uuid.UUID, uuid.UUID]] = []
        for member_config_id in member_config_ids:
            agent_config = await agent_repo.get_config(member_config_id)
            if agent_config is None:
                raise LookupError("Team member agent not found for tenant")
            draft = await agent_repo.get_latest_draft(member_config_id)
            version_id = draft.id if draft else agent_config.published_version_id
            if version_id is None:
                raise ValueError(f"Agent {agent_config.name} has no runnable version")
            resolved_members.append((agent_config.id, version_id))

        next_version = (
            await self.session.scalar(
                self.scoped(
                    select(func.coalesce(func.max(TeamVersion.version), 0)).where(
                        TeamVersion.team_config_id == config_id
                    ),
                    TeamVersion,
                )
            )
            or 0
        ) + 1
        version = TeamVersion(
            id=new_id(),
            tenant_id=self.context.tenant_id,
            team_config_id=config_id,
            version=next_version,
            status=AgentStatus.draft,
            instructions=instructions,
            mode=mode,
            model_id=model_id,
            temperature=temperature,
            created_by=self.context.user_id,
        )
        self.session.add(version)
        await self.session.flush()
        for position, (agent_config_id, agent_version_id) in enumerate(resolved_members):
            self.session.add(
                TeamMember(
                    id=new_id(),
                    tenant_id=self.context.tenant_id,
                    team_config_id=config_id,
                    team_version_id=version.id,
                    agent_config_id=agent_config_id,
                    agent_version_id=agent_version_id,
                    position=position,
                )
            )
        await self.session.flush()
        await self.audit(
            action="team.draft",
            resource_type="team_version",
            resource_id=str(version.id),
            details={"version": next_version, "members": len(resolved_members)},
        )
        return version

    async def publish(self, version_id: uuid.UUID) -> TeamVersion:
        version = await self.get_version(version_id, allow_draft=True)
        if version is None:
            raise LookupError("Team version not found")
        if version.status != AgentStatus.draft:
            raise ValueError("Only draft team versions can be published")
        members = list(await self.members(version.id))
        if len(members) < 2:
            raise ValueError("Published teams require at least two agents")

        agent_repo = AgentRepository(self.session, self.context)
        for member in members:
            config = await agent_repo.get_config(member.agent_config_id)
            if config is None or config.published_version_id is None:
                raise ValueError("Every team member must have a published agent version")
            published = await agent_repo.get_version(config.published_version_id)
            if published is None:
                raise ValueError("Every team member must have a published agent version")
            member.agent_version_id = published.id

        version.status = AgentStatus.published
        await self.session.execute(
            update(TeamConfig)
            .where(
                TeamConfig.id == version.team_config_id,
                TeamConfig.tenant_id == self.context.tenant_id,
            )
            .values(published_version_id=version.id)
        )
        await self.session.flush()
        await self.audit(
            action="team.publish",
            resource_type="team_version",
            resource_id=str(version.id),
        )
        return version


class MembershipRepository(TenantRepository):
    async def list_users(self) -> Sequence[Membership]:
        rows = await self.session.scalars(
            self.scoped(
                select(Membership).order_by(Membership.display_name, Membership.user_id),
                Membership,
            )
        )
        return rows.all()

    async def get(self, membership_id: uuid.UUID) -> Membership | None:
        return await self.session.scalar(
            self.scoped(
                select(Membership).where(Membership.id == membership_id),
                Membership,
            )
        )

    async def get_by_user_id(self, user_id: str) -> Membership | None:
        return await self.session.scalar(
            self.scoped(
                select(Membership).where(Membership.user_id == user_id),
                Membership,
            )
        )

    async def create(
        self,
        *,
        user_id: str,
        display_name: str,
        email: str | None,
        role: Role,
        is_active: bool = True,
    ) -> Membership:
        existing = await self.get_by_user_id(user_id)
        if existing is not None:
            raise ValueError("A user with this ID already exists in the tenant")
        if role not in {Role.tenant_admin, Role.end_user}:
            raise ValueError("Tenant users must be tenant_admin or end_user")
        membership = Membership(
            id=new_id(),
            tenant_id=self.context.tenant_id,
            user_id=user_id.strip(),
            display_name=display_name.strip(),
            email=(email or "").strip() or None,
            role=role,
            is_active=is_active,
        )
        self.session.add(membership)
        await self.session.flush()
        await self.audit(
            action="user.create",
            resource_type="membership",
            resource_id=str(membership.id),
            details={"user_id": membership.user_id, "role": membership.role.value},
        )
        return membership

    async def update(
        self,
        membership_id: uuid.UUID,
        *,
        display_name: str | None = None,
        email: str | None = None,
        role: Role | None = None,
        is_active: bool | None = None,
    ) -> Membership | None:
        membership = await self.get(membership_id)
        if membership is None:
            return None
        if display_name is not None:
            membership.display_name = display_name.strip()
        if email is not None:
            membership.email = email.strip() or None
        if role is not None:
            if role not in {Role.tenant_admin, Role.end_user}:
                raise ValueError("Tenant users must be tenant_admin or end_user")
            membership.role = role
        if is_active is not None:
            membership.is_active = is_active
        await self.session.flush()
        await self.audit(
            action="user.update",
            resource_type="membership",
            resource_id=str(membership.id),
            details={
                "user_id": membership.user_id,
                "role": membership.role.value,
                "is_active": membership.is_active,
            },
        )
        return membership

    async def delete(self, membership_id: uuid.UUID) -> Membership | None:
        membership = await self.get(membership_id)
        if membership is None:
            return None
        await self.session.execute(
            delete(WorkflowAssignment).where(
                WorkflowAssignment.tenant_id == self.context.tenant_id,
                WorkflowAssignment.user_id == membership.user_id,
            )
        )
        await self.session.delete(membership)
        await self.session.flush()
        await self.audit(
            action="user.delete",
            resource_type="membership",
            resource_id=str(membership_id),
            details={"user_id": membership.user_id},
        )
        return membership


class WorkflowRepository(TenantRepository):
    async def list_available_for_user(self, user_id: str) -> Sequence[WorkflowConfig]:
        rows = await self.session.scalars(
            select(WorkflowConfig)
            .join(
                WorkflowAssignment,
                WorkflowAssignment.workflow_config_id == WorkflowConfig.id,
            )
            .where(
                WorkflowConfig.tenant_id == self.context.tenant_id,
                WorkflowConfig.published_version_id.is_not(None),
                WorkflowAssignment.tenant_id == self.context.tenant_id,
                WorkflowAssignment.user_id == user_id,
            )
            .order_by(WorkflowConfig.name)
        )
        return rows.all()

    async def assigned_user_ids(self, config_id: uuid.UUID) -> list[str]:
        rows = await self.session.scalars(
            select(WorkflowAssignment.user_id)
            .where(
                WorkflowAssignment.tenant_id == self.context.tenant_id,
                WorkflowAssignment.workflow_config_id == config_id,
            )
            .order_by(WorkflowAssignment.user_id)
        )
        return list(rows.all())

    async def is_assigned(self, config_id: uuid.UUID, user_id: str) -> bool:
        return (
            await self.session.scalar(
                select(WorkflowAssignment.id).where(
                    WorkflowAssignment.tenant_id == self.context.tenant_id,
                    WorkflowAssignment.workflow_config_id == config_id,
                    WorkflowAssignment.user_id == user_id,
                )
            )
            is not None
        )

    async def replace_assignments(
        self, config_id: uuid.UUID, user_ids: Sequence[str]
    ) -> list[str]:
        if await self.get_config(config_id) is None:
            raise LookupError("Workflow configuration not found")
        normalized = sorted({value.strip() for value in user_ids if value.strip()})
        await self.session.execute(
            delete(WorkflowAssignment).where(
                WorkflowAssignment.tenant_id == self.context.tenant_id,
                WorkflowAssignment.workflow_config_id == config_id,
            )
        )
        self.session.add_all(
            WorkflowAssignment(
                id=new_id(),
                tenant_id=self.context.tenant_id,
                workflow_config_id=config_id,
                user_id=user_id,
                assigned_by=self.context.user_id,
            )
            for user_id in normalized
        )
        await self.session.flush()
        await self.audit(
            action="workflow.assignments.update",
            resource_type="workflow_config",
            resource_id=str(config_id),
            details={"user_ids": normalized},
        )
        return normalized

    async def assigned_workflow_ids(self, user_id: str) -> list[uuid.UUID]:
        rows = await self.session.scalars(
            select(WorkflowAssignment.workflow_config_id)
            .where(
                WorkflowAssignment.tenant_id == self.context.tenant_id,
                WorkflowAssignment.user_id == user_id,
            )
            .order_by(WorkflowAssignment.workflow_config_id)
        )
        return list(rows.all())

    async def replace_user_assignments(
        self, user_id: str, workflow_ids: Sequence[uuid.UUID]
    ) -> list[uuid.UUID]:
        normalized = sorted({value for value in workflow_ids})
        for workflow_id in normalized:
            config = await self.get_config(workflow_id)
            if config is None:
                raise LookupError(f"Workflow {workflow_id} not found")
            if config.published_version_id is None:
                raise ValueError(f"Workflow {config.slug} must be published before assignment")
        await self.session.execute(
            delete(WorkflowAssignment).where(
                WorkflowAssignment.tenant_id == self.context.tenant_id,
                WorkflowAssignment.user_id == user_id,
            )
        )
        self.session.add_all(
            WorkflowAssignment(
                id=new_id(),
                tenant_id=self.context.tenant_id,
                workflow_config_id=workflow_id,
                user_id=user_id,
                assigned_by=self.context.user_id,
            )
            for workflow_id in normalized
        )
        await self.session.flush()
        await self.audit(
            action="user.workflow_assignments.update",
            resource_type="membership",
            resource_id=user_id,
            details={"workflow_ids": [str(value) for value in normalized]},
        )
        return normalized

    async def list_configs(self) -> Sequence[WorkflowConfig]:
        rows = await self.session.scalars(
            self.scoped(
                select(WorkflowConfig).order_by(WorkflowConfig.created_at.desc()),
                WorkflowConfig,
            )
        )
        return rows.all()

    async def search_configs(
        self,
        *,
        q: str | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 25,
    ) -> tuple[Sequence[WorkflowConfig], int]:
        page = max(1, page)
        page_size = min(max(1, page_size), 100)
        filters = [WorkflowConfig.tenant_id == self.context.tenant_id]
        if q and q.strip():
            pattern = f"%{q.strip().lower()}%"
            filters.append(
                or_(
                    func.lower(WorkflowConfig.name).like(pattern),
                    func.lower(WorkflowConfig.slug).like(pattern),
                )
            )
        if status == "published":
            filters.append(WorkflowConfig.published_version_id.is_not(None))
        elif status == "draft":
            filters.append(WorkflowConfig.published_version_id.is_(None))
        total = await self.session.scalar(
            select(func.count()).select_from(WorkflowConfig).where(*filters)
        )
        rows = await self.session.scalars(
            select(WorkflowConfig)
            .where(*filters)
            .order_by(WorkflowConfig.updated_at.desc(), WorkflowConfig.name.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return rows.all(), int(total or 0)

    async def get_config(self, config_id: uuid.UUID) -> WorkflowConfig | None:
        return await self.session.scalar(
            self.scoped(
                select(WorkflowConfig).where(WorkflowConfig.id == config_id),
                WorkflowConfig,
            )
        )

    async def get_config_by_slug(self, slug: str) -> WorkflowConfig | None:
        return await self.session.scalar(
            self.scoped(
                select(WorkflowConfig).where(WorkflowConfig.slug == slug),
                WorkflowConfig,
            )
        )

    async def create_config(
        self, *, slug: str, name: str, description: str | None = None
    ) -> WorkflowConfig:
        config = WorkflowConfig(
            id=new_id(),
            tenant_id=self.context.tenant_id,
            slug=validate_slug(slug),
            name=name,
            description=description,
        )
        self.session.add(config)
        await self.session.flush()
        await self.audit(
            action="workflow.create",
            resource_type="workflow_config",
            resource_id=str(config.id),
        )
        return config

    async def update_config(
        self, config_id: uuid.UUID, *, name: str | None = None, description: str | None = None
    ) -> WorkflowConfig | None:
        config = await self.get_config(config_id)
        if config is None:
            return None
        if name is not None:
            config.name = name
        if description is not None:
            config.description = description
        await self.session.flush()
        return config

    async def get_version(
        self, version_id: uuid.UUID, *, allow_draft: bool = False
    ) -> WorkflowVersion | None:
        statement = select(WorkflowVersion).where(WorkflowVersion.id == version_id)
        if not allow_draft:
            statement = statement.where(WorkflowVersion.status == AgentStatus.published)
        return await self.session.scalar(self.scoped(statement, WorkflowVersion))

    async def get_latest_draft(self, config_id: uuid.UUID) -> WorkflowVersion | None:
        return await self.session.scalar(
            self.scoped(
                select(WorkflowVersion)
                .where(
                    WorkflowVersion.workflow_config_id == config_id,
                    WorkflowVersion.status == AgentStatus.draft,
                )
                .order_by(WorkflowVersion.version.desc()),
                WorkflowVersion,
            )
        )

    async def steps(self, version_id: uuid.UUID) -> Sequence[WorkflowStep]:
        rows = await self.session.scalars(
            self.scoped(
                select(WorkflowStep)
                .where(WorkflowStep.workflow_version_id == version_id)
                .order_by(WorkflowStep.position),
                WorkflowStep,
            )
        )
        return rows.all()

    async def create_draft(
        self,
        *,
        config_id: uuid.UUID,
        mode: str,
        steps: list[dict[str, Any]],
    ) -> WorkflowVersion:
        if mode not in {"sequential", "parallel"}:
            raise ValueError("Workflow mode must be sequential or parallel")
        if not steps:
            raise ValueError("A workflow requires at least one step")
        if any(step.get("condition_expression") for step in steps):
            raise ValueError("CEL conditions are unavailable in this deployment")
        config = await self.get_config(config_id)
        if config is None:
            raise LookupError("Workflow config not found")

        agent_repo = AgentRepository(self.session, self.context)
        team_repo = TeamRepository(self.session, self.context)
        resolved: list[dict[str, Any]] = []
        for step in steps:
            target_type = step["target_type"]
            target_config_id = step["target_config_id"]
            if target_type == "agent":
                agent_target = await agent_repo.get_config(target_config_id)
                if agent_target is None:
                    raise LookupError("Workflow agent step not found for tenant")
                agent_draft = await agent_repo.get_latest_draft(agent_target.id)
                version_id = (
                    agent_draft.id if agent_draft else agent_target.published_version_id
                )
                target_id = agent_target.id
                target_name = agent_target.name
            elif target_type == "team":
                team_target = await team_repo.get_config(target_config_id)
                if team_target is None:
                    raise LookupError("Workflow team step not found for tenant")
                team_draft = await team_repo.get_latest_draft(team_target.id)
                version_id = (
                    team_draft.id if team_draft else team_target.published_version_id
                )
                target_id = team_target.id
                target_name = team_target.name
            else:
                raise ValueError("Workflow step target must be agent or team")
            if version_id is None:
                raise ValueError(
                    f"{target_type.title()} {target_name} has no runnable version"
                )
            resolved.append(step | {"target_id": target_id, "version_id": version_id})

        next_version = (
            await self.session.scalar(
                self.scoped(
                    select(func.coalesce(func.max(WorkflowVersion.version), 0)).where(
                        WorkflowVersion.workflow_config_id == config_id
                    ),
                    WorkflowVersion,
                )
            )
            or 0
        ) + 1
        version = WorkflowVersion(
            id=new_id(),
            tenant_id=self.context.tenant_id,
            workflow_config_id=config_id,
            version=next_version,
            status=AgentStatus.draft,
            mode=mode,
            created_by=self.context.user_id,
        )
        self.session.add(version)
        await self.session.flush()
        for position, item in enumerate(resolved):
            is_agent = item["target_type"] == "agent"
            self.session.add(
                WorkflowStep(
                    id=new_id(),
                    tenant_id=self.context.tenant_id,
                    workflow_config_id=config_id,
                    workflow_version_id=version.id,
                    position=position,
                    name=item["name"],
                    target_type=item["target_type"],
                    agent_config_id=item["target_id"] if is_agent else None,
                    agent_version_id=item["version_id"] if is_agent else None,
                    team_config_id=item["target_id"] if not is_agent else None,
                    team_version_id=item["version_id"] if not is_agent else None,
                    condition_expression=item.get("condition_expression"),
                )
            )
        await self.session.flush()
        await self.audit(
            action="workflow.draft",
            resource_type="workflow_version",
            resource_id=str(version.id),
            details={"version": next_version, "mode": mode, "steps": len(resolved)},
        )
        return version

    async def publish(self, version_id: uuid.UUID) -> WorkflowVersion:
        version = await self.get_version(version_id, allow_draft=True)
        if version is None:
            raise LookupError("Workflow version not found")
        if version.status != AgentStatus.draft:
            raise ValueError("Only draft workflow versions can be published")
        steps = list(await self.steps(version.id))
        if not steps:
            raise ValueError("Published workflows require at least one step")
        agents = AgentRepository(self.session, self.context)
        teams = TeamRepository(self.session, self.context)
        for step in steps:
            if step.target_type == "agent":
                agent_config = await agents.get_config(
                    step.agent_config_id  # type: ignore[arg-type]
                )
                if agent_config is None or agent_config.published_version_id is None:
                    raise ValueError("Every workflow agent step must be published")
                step.agent_version_id = agent_config.published_version_id
            else:
                team_config = await teams.get_config(
                    step.team_config_id  # type: ignore[arg-type]
                )
                if team_config is None or team_config.published_version_id is None:
                    raise ValueError("Every workflow team step must be published")
                step.team_version_id = team_config.published_version_id
        version.status = AgentStatus.published
        await self.session.execute(
            update(WorkflowConfig)
            .where(
                WorkflowConfig.id == version.workflow_config_id,
                WorkflowConfig.tenant_id == self.context.tenant_id,
            )
            .values(published_version_id=version.id)
        )
        await self.session.flush()
        await self.audit(
            action="workflow.publish",
            resource_type="workflow_version",
            resource_id=str(version.id),
        )
        return version


class CredentialRepository(TenantRepository):
    async def list(self) -> Sequence[TenantCredential]:
        rows = await self.session.scalars(
            self.scoped(
                select(TenantCredential).order_by(TenantCredential.created_at.desc()),
                TenantCredential,
            )
        )
        return rows.all()

    async def get(self, credential_id: uuid.UUID) -> TenantCredential | None:
        return await self.session.scalar(
            self.scoped(
                select(TenantCredential).where(TenantCredential.id == credential_id),
                TenantCredential,
            )
        )

    async def get_for_provider(self, provider: str) -> TenantCredential | None:
        return await self.session.scalar(
            self.scoped(
                select(TenantCredential)
                .where(TenantCredential.provider == provider)
                .order_by(TenantCredential.created_at.desc()),
                TenantCredential,
            )
        )

    async def create(
        self, *, name: str, provider: str, encrypted_value: str, key_version: str
    ) -> TenantCredential:
        credential = TenantCredential(
            id=new_id(),
            tenant_id=self.context.tenant_id,
            name=name,
            provider=provider,
            encrypted_value=encrypted_value,
            key_version=key_version,
        )
        self.session.add(credential)
        await self.session.flush()
        await self.audit(
            action="credential.create",
            resource_type="tenant_credential",
            resource_id=str(credential.id),
            details={"provider": provider},
        )
        return credential


class ServiceAccountRepository(TenantRepository):
    async def list_accounts(self) -> Sequence[ServiceAccount]:
        rows = await self.session.scalars(
            self.scoped(
                select(ServiceAccount).order_by(ServiceAccount.created_at.desc()),
                ServiceAccount,
            )
        )
        return rows.all()

    async def create(
        self,
        *,
        name: str,
        token_prefix: str,
        token_hash: str,
        scopes: list[str],
        expires_at: datetime | None,
    ) -> ServiceAccount:
        account = ServiceAccount(
            id=new_id(),
            tenant_id=self.context.tenant_id,
            name=name,
            token_prefix=token_prefix,
            token_hash=token_hash,
            scopes=scopes,
            created_by=self.context.user_id,
            expires_at=expires_at,
        )
        self.session.add(account)
        await self.session.flush()
        await self.audit(
            action="service_account.create",
            resource_type="service_account",
            resource_id=str(account.id),
            details={
                "name": name,
                "scopes": scopes,
                "expires_at": expires_at.isoformat() if expires_at else None,
            },
        )
        return account

    async def revoke(self, account_id: uuid.UUID) -> ServiceAccount | None:
        account = await self.session.scalar(
            self.scoped(
                select(ServiceAccount).where(
                    ServiceAccount.id == account_id,
                    ServiceAccount.revoked_at.is_(None),
                ),
                ServiceAccount,
            )
        )
        if account is None:
            return None
        account.revoked_at = datetime.now(UTC)
        await self.session.flush()
        await self.audit(
            action="service_account.revoke",
            resource_type="service_account",
            resource_id=str(account.id),
        )
        return account


class ToolDefinitionRepository(TenantRepository):
    async def list(self) -> Sequence[ToolDefinition]:
        rows = await self.session.scalars(
            self.scoped(
                select(ToolDefinition).order_by(ToolDefinition.created_at.desc()),
                ToolDefinition,
            )
        )
        return rows.all()

    async def get(self, definition_id: uuid.UUID) -> ToolDefinition | None:
        return await self.session.scalar(
            self.scoped(
                select(ToolDefinition).where(ToolDefinition.id == definition_id),
                ToolDefinition,
            )
        )

    async def create(self, values: dict[str, Any]) -> ToolDefinition:
        credential_id = values.get("credential_id")
        if (
            credential_id
            and await CredentialRepository(self.session, self.context).get(credential_id) is None
        ):
            raise LookupError("Credential not found for tenant")
        definition = ToolDefinition(
            id=new_id(),
            tenant_id=self.context.tenant_id,
            **values,
        )
        self.session.add(definition)
        await self.session.flush()
        await self.audit(
            action="tool.create",
            resource_type="tool_definition",
            resource_id=str(definition.id),
            details={"slug": definition.slug, "kind": definition.kind},
        )
        return definition

    async def update(
        self, definition_id: uuid.UUID, values: dict[str, Any]
    ) -> ToolDefinition | None:
        definition = await self.get(definition_id)
        if definition is None:
            return None
        credential_id = values.get("credential_id")
        if (
            credential_id
            and await CredentialRepository(self.session, self.context).get(credential_id) is None
        ):
            raise LookupError("Credential not found for tenant")
        for key, value in values.items():
            setattr(definition, key, value)
        await self.session.flush()
        action = "tool.update"
        if "active" in values:
            action = "tool.enable" if values["active"] else "tool.disable"
        await self.audit(
            action=action,
            resource_type="tool_definition",
            resource_id=str(definition.id),
            details={"fields": sorted(values)},
        )
        return definition

    async def delete(self, definition_id: uuid.UUID) -> bool:
        definition = await self.get(definition_id)
        if definition is None:
            return False
        await self.audit(
            action="tool.delete",
            resource_type="tool_definition",
            resource_id=str(definition.id),
            details={"slug": definition.slug},
        )
        await self.session.execute(
            delete(ToolDefinition).where(
                ToolDefinition.id == definition_id,
                ToolDefinition.tenant_id == self.context.tenant_id,
            )
        )
        await self.session.flush()
        return True


class ApprovalRepository(TenantRepository):
    async def create_from_requirement(
        self,
        *,
        conversation: ConversationSession,
        run_id: str,
        requirement: dict[str, Any],
    ) -> ApprovalBinding:
        tool = requirement.get("tool_execution") or {}
        requirement_id = str(requirement.get("id") or tool.get("tool_call_id") or new_id())
        existing = await self.session.scalar(
            self.scoped(
                select(ApprovalBinding).where(
                    ApprovalBinding.run_id == run_id,
                    ApprovalBinding.requirement_id == requirement_id,
                ),
                ApprovalBinding,
            )
        )
        if existing:
            return existing
        arguments = tool.get("tool_args") if isinstance(tool, dict) else {}
        arguments = arguments if isinstance(arguments, dict) else {}
        request_hash = hashlib.sha256(
            json.dumps(arguments, sort_keys=True, default=str).encode()
        ).hexdigest()
        row = ApprovalBinding(
            id=new_id(),
            tenant_id=self.context.tenant_id,
            session_id=conversation.id,
            tool_name=str(tool.get("tool_name") or "unknown"),
            request_hash=request_hash,
            redacted_arguments=arguments,
            status=ApprovalStatus.pending,
            run_id=run_id,
            requirement_id=requirement_id,
            requirement=requirement,
            expires_at=datetime.now(UTC) + timedelta(hours=24),
        )
        self.session.add(row)
        await self.session.flush()
        await self.audit(
            action="approval.request",
            resource_type="approval",
            resource_id=str(row.id),
            details={"run_id": run_id, "tool_name": row.tool_name},
        )
        return row

    async def list_pending(self) -> Sequence[ApprovalBinding]:
        rows = await self.session.scalars(
            self.scoped(
                select(ApprovalBinding)
                .where(ApprovalBinding.status == ApprovalStatus.pending)
                .order_by(ApprovalBinding.created_at.asc()),
                ApprovalBinding,
            )
        )
        return rows.all()

    async def get(self, approval_id: uuid.UUID, *, lock: bool = False) -> ApprovalBinding | None:
        statement = select(ApprovalBinding).where(ApprovalBinding.id == approval_id)
        if lock:
            statement = statement.with_for_update()
        return await self.session.scalar(self.scoped(statement, ApprovalBinding))

    async def resolve(
        self, approval_id: uuid.UUID, approved: bool, reason: str | None = None
    ) -> ApprovalBinding | None:
        if not self.context.can_approve():
            raise PermissionError("Only tenant/platform admins can resolve approvals")
        approval = await self.get(approval_id, lock=True)
        if approval is None or approval.status != ApprovalStatus.pending:
            return None
        if approval.expires_at and approval.expires_at <= datetime.now(UTC):
            return None
        approval.status = ApprovalStatus.approved if approved else ApprovalStatus.rejected
        approval.resolved_by = self.context.user_id
        approval.decision_reason = reason
        await self.session.flush()
        await self.audit(
            action="approval.resolve",
            resource_type="approval",
            resource_id=str(approval.id),
            details={"approved": approved, "reason": reason},
        )
        return approval

    async def mark_continued(
        self, approval_id: uuid.UUID, *, error: str | None = None
    ) -> ApprovalBinding | None:
        approval = await self.get(approval_id, lock=True)
        if approval is None:
            return None
        approval.continued_at = datetime.now(UTC)
        approval.continuation_error = error
        await self.session.flush()
        return approval


class ToolDefinitionVersionRepository(TenantRepository):
    async def list_for_tool(
        self, tool_definition_id: uuid.UUID
    ) -> Sequence[ToolDefinitionVersion]:
        rows = await self.session.scalars(
            self.scoped(
                select(ToolDefinitionVersion)
                .where(ToolDefinitionVersion.tool_definition_id == tool_definition_id)
                .order_by(ToolDefinitionVersion.version.desc()),
                ToolDefinitionVersion,
            )
        )
        return rows.all()

    async def get(self, version_id: uuid.UUID) -> ToolDefinitionVersion | None:
        return await self.session.scalar(
            self.scoped(
                select(ToolDefinitionVersion).where(ToolDefinitionVersion.id == version_id),
                ToolDefinitionVersion,
            )
        )

    async def latest_draft(
        self, tool_definition_id: uuid.UUID
    ) -> ToolDefinitionVersion | None:
        return await self.session.scalar(
            self.scoped(
                select(ToolDefinitionVersion)
                .where(
                    ToolDefinitionVersion.tool_definition_id == tool_definition_id,
                    ToolDefinitionVersion.status.in_(("draft", "validated")),
                )
                .order_by(ToolDefinitionVersion.version.desc())
                .limit(1),
                ToolDefinitionVersion,
            )
        )

    async def next_version_number(self, tool_definition_id: uuid.UUID) -> int:
        current = await self.session.scalar(
            self.scoped(
                select(func.max(ToolDefinitionVersion.version)).where(
                    ToolDefinitionVersion.tool_definition_id == tool_definition_id
                ),
                ToolDefinitionVersion,
            )
        )
        return int(current or 0) + 1

    async def upsert_draft(
        self,
        *,
        tool_definition_id: uuid.UUID,
        source_code: str,
        dependencies: list[Any],
        capabilities: list[Any],
        settings: dict[str, Any],
        created_by: str,
    ) -> ToolDefinitionVersion:
        draft = await self.latest_draft(tool_definition_id)
        if draft is not None and draft.status == "draft":
            draft.source_code = source_code
            draft.dependencies = dependencies
            draft.capabilities = capabilities
            draft.settings = settings
            draft.updated_at = datetime.now(UTC)
            await self.session.flush()
            return draft
        row = ToolDefinitionVersion(
            id=new_id(),
            tenant_id=self.context.tenant_id,
            tool_definition_id=tool_definition_id,
            version=await self.next_version_number(tool_definition_id),
            status="draft",
            source_code=source_code,
            dependencies=dependencies,
            capabilities=capabilities,
            settings=settings,
            created_by=created_by,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def mark_validated(self, version_id: uuid.UUID) -> ToolDefinitionVersion | None:
        row = await self.get(version_id)
        if row is None:
            return None
        row.status = "validated"
        await self.session.flush()
        return row

    async def publish(
        self, version_id: uuid.UUID, tool: ToolDefinition
    ) -> ToolDefinitionVersion | None:
        row = await self.get(version_id)
        if row is None or row.tool_definition_id != tool.id:
            return None
        if row.status not in {"draft", "validated"}:
            return None
        row.status = "published"
        row.published_at = datetime.now(UTC)
        tool.published_version_id = row.id
        config = dict(tool.config or {})
        config["source_code"] = row.source_code
        config["dependencies"] = row.dependencies
        config["capabilities"] = row.capabilities
        config["settings"] = row.settings
        config["version_status"] = "published"
        tool.config = config
        if any(bool(item.get("mutating")) for item in row.capabilities if isinstance(item, dict)):
            tool.approval_required = True
        await self.session.flush()
        await self.audit(
            action="tool.publish",
            resource_type="tool_definition",
            resource_id=str(tool.id),
            details={"version_id": str(row.id), "version": row.version},
        )
        return row


class PlatformPythonPackageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list(self, *, active_only: bool = False) -> Sequence[PlatformPythonPackage]:
        statement = select(PlatformPythonPackage).order_by(
            PlatformPythonPackage.name, PlatformPythonPackage.version
        )
        if active_only:
            statement = statement.where(PlatformPythonPackage.active.is_(True))
        rows = await self.session.scalars(statement)
        return rows.all()

    async def allowlist_pairs(self) -> set[tuple[str, str]]:
        rows = await self.list(active_only=True)
        return {(row.name.lower(), row.version) for row in rows}

    async def get(self, package_id: uuid.UUID) -> PlatformPythonPackage | None:
        return await self.session.get(PlatformPythonPackage, package_id)

    async def create(self, values: dict[str, Any]) -> PlatformPythonPackage:
        row = PlatformPythonPackage(id=new_id(), **values)
        self.session.add(row)
        await self.session.flush()
        return row

    async def update(
        self, package_id: uuid.UUID, values: dict[str, Any]
    ) -> PlatformPythonPackage | None:
        row = await self.get(package_id)
        if row is None:
            return None
        for key, value in values.items():
            setattr(row, key, value)
        await self.session.flush()
        return row


class ApprovalRepository(TenantRepository):
    async def create_from_requirement(
        self,
        *,
        conversation: ConversationSession,
        run_id: str,
        requirement: dict[str, Any],
    ) -> ApprovalBinding:
        tool = requirement.get("tool_execution") or {}
        requirement_id = str(requirement.get("id") or tool.get("tool_call_id") or new_id())
        existing = await self.session.scalar(
            self.scoped(
                select(ApprovalBinding).where(
                    ApprovalBinding.run_id == run_id,
                    ApprovalBinding.requirement_id == requirement_id,
                ),
                ApprovalBinding,
            )
        )
        if existing:
            return existing
        arguments = tool.get("tool_args") if isinstance(tool, dict) else {}
        arguments = arguments if isinstance(arguments, dict) else {}
        request_hash = hashlib.sha256(
            json.dumps(arguments, sort_keys=True, default=str).encode()
        ).hexdigest()
        row = ApprovalBinding(
            id=new_id(),
            tenant_id=self.context.tenant_id,
            session_id=conversation.id,
            tool_name=str(tool.get("tool_name") or "unknown"),
            request_hash=request_hash,
            redacted_arguments=arguments,
            status=ApprovalStatus.pending,
            run_id=run_id,
            requirement_id=requirement_id,
            requirement=requirement,
            expires_at=datetime.now(UTC) + timedelta(hours=24),
        )
        self.session.add(row)
        await self.session.flush()
        await self.audit(
            action="approval.request",
            resource_type="approval",
            resource_id=str(row.id),
            details={"run_id": run_id, "tool_name": row.tool_name},
        )
        return row

    async def list_pending(self) -> Sequence[ApprovalBinding]:
        rows = await self.session.scalars(
            self.scoped(
                select(ApprovalBinding)
                .where(ApprovalBinding.status == ApprovalStatus.pending)
                .order_by(ApprovalBinding.created_at.asc()),
                ApprovalBinding,
            )
        )
        return rows.all()

    async def get(self, approval_id: uuid.UUID, *, lock: bool = False) -> ApprovalBinding | None:
        statement = select(ApprovalBinding).where(ApprovalBinding.id == approval_id)
        if lock:
            statement = statement.with_for_update()
        return await self.session.scalar(self.scoped(statement, ApprovalBinding))

    async def resolve(
        self, approval_id: uuid.UUID, approved: bool, reason: str | None = None
    ) -> ApprovalBinding | None:
        if not self.context.can_approve():
            raise PermissionError("Only tenant/platform admins can resolve approvals")
        approval = await self.get(approval_id, lock=True)
        if approval is None or approval.status != ApprovalStatus.pending:
            return None
        if approval.expires_at and approval.expires_at <= datetime.now(UTC):
            return None
        approval.status = ApprovalStatus.approved if approved else ApprovalStatus.rejected
        approval.resolved_by = self.context.user_id
        approval.decision_reason = reason
        await self.session.flush()
        await self.audit(
            action="approval.resolve",
            resource_type="approval",
            resource_id=str(approval.id),
            details={"approved": approved, "reason": reason},
        )
        return approval

    async def mark_continued(
        self, approval_id: uuid.UUID, *, error: str | None = None
    ) -> ApprovalBinding | None:
        approval = await self.get(approval_id, lock=True)
        if approval is None:
            return None
        approval.continued_at = datetime.now(UTC)
        approval.continuation_error = error
        await self.session.flush()
        return approval


class KnowledgeRepository(TenantRepository):
    async def list_all_sources(self) -> Sequence[KnowledgeSource]:
        rows = await self.session.scalars(
            self.scoped(
                select(KnowledgeSource).order_by(KnowledgeSource.created_at.desc()),
                KnowledgeSource,
            )
        )
        return rows.all()

    async def list_bases(self) -> Sequence[KnowledgeBase]:
        rows = await self.session.scalars(
            self.scoped(
                select(KnowledgeBase).order_by(KnowledgeBase.created_at.desc()), KnowledgeBase
            )
        )
        return rows.all()

    async def create_base(
        self, *, name: str, config: dict[str, Any] | None = None
    ) -> KnowledgeBase:
        base = KnowledgeBase(
            id=new_id(),
            tenant_id=self.context.tenant_id,
            name=name,
            config=config or {},
        )
        self.session.add(base)
        await self.session.flush()
        return base

    async def get_base(self, knowledge_base_id: uuid.UUID) -> KnowledgeBase | None:
        return await self.session.scalar(
            self.scoped(
                select(KnowledgeBase).where(KnowledgeBase.id == knowledge_base_id),
                KnowledgeBase,
            )
        )

    async def create_source(
        self,
        *,
        knowledge_base_id: uuid.UUID,
        kind: str,
        uri: str,
        metadata: dict[str, Any] | None = None,
    ) -> KnowledgeSource:
        base = await self.get_base(knowledge_base_id)
        if base is None:
            raise LookupError("Knowledge base not found")
        source = KnowledgeSource(
            id=new_id(),
            tenant_id=self.context.tenant_id,
            knowledge_base_id=knowledge_base_id,
            kind=kind,
            uri=uri,
            status="pending",
            metadata_=metadata or {},
        )
        self.session.add(source)
        await self.session.flush()
        return source

    async def list_sources(self, knowledge_base_id: uuid.UUID) -> Sequence[KnowledgeSource]:
        rows = await self.session.scalars(
            self.scoped(
                select(KnowledgeSource)
                .where(KnowledgeSource.knowledge_base_id == knowledge_base_id)
                .order_by(KnowledgeSource.created_at.desc()),
                KnowledgeSource,
            )
        )
        return rows.all()

    async def get_source(self, source_id: uuid.UUID) -> KnowledgeSource | None:
        return await self.session.scalar(
            self.scoped(
                select(KnowledgeSource).where(KnowledgeSource.id == source_id),
                KnowledgeSource,
            )
        )

    async def get_source_by_hash(
        self, knowledge_base_id: uuid.UUID, content_hash: str
    ) -> KnowledgeSource | None:
        return await self.session.scalar(
            self.scoped(
                select(KnowledgeSource).where(
                    KnowledgeSource.knowledge_base_id == knowledge_base_id,
                    KnowledgeSource.content_hash == content_hash,
                ),
                KnowledgeSource,
            )
        )

    async def delete_source(self, source_id: uuid.UUID) -> bool:
        source = await self.get_source(source_id)
        if source is None:
            return False
        await self.session.execute(
            delete(KnowledgeSource).where(
                KnowledgeSource.id == source_id,
                KnowledgeSource.tenant_id == self.context.tenant_id,
            )
        )
        await self.audit(
            action="knowledge.delete",
            resource_type="knowledge_source",
            resource_id=str(source_id),
        )
        return True

    async def delete_chunks(self, source_id: uuid.UUID) -> None:
        await self.session.execute(
            delete(KnowledgeChunk).where(
                KnowledgeChunk.tenant_id == self.context.tenant_id,
                KnowledgeChunk.source_id == source_id,
            )
        )


class SessionRepository(TenantRepository):
    async def get(self, session_id: uuid.UUID) -> ConversationSession | None:
        return await self.session.scalar(
            self.scoped(
                select(ConversationSession).where(ConversationSession.id == session_id),
                ConversationSession,
            )
        )

    async def pin(
        self,
        *,
        external_session_id: str,
        agent_config_id: uuid.UUID,
        agent_version_id: uuid.UUID,
        runtime_session_id: str,
        runtime_user_id: str,
        title: str | None = None,
    ) -> ConversationSession:
        existing = await self.session.scalar(
            self.scoped(
                select(ConversationSession).where(
                    ConversationSession.external_session_id == external_session_id
                ),
                ConversationSession,
            )
        )
        if existing:
            if existing.user_id != self.context.user_id:
                raise PermissionError("Session belongs to another user")
            if existing.target_type != "agent" or existing.agent_config_id != agent_config_id:
                raise ValueError("Session is pinned to a different target")
            return existing
        row = ConversationSession(
            id=new_id(),
            tenant_id=self.context.tenant_id,
            external_session_id=external_session_id,
            target_type="agent",
            agent_config_id=agent_config_id,
            agent_version_id=agent_version_id,
            user_id=self.context.user_id,
            title=title,
            runtime_session_id=runtime_session_id,
            runtime_user_id=runtime_user_id,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def pin_team(
        self,
        *,
        external_session_id: str,
        team_config_id: uuid.UUID,
        team_version_id: uuid.UUID,
        runtime_session_id: str,
        runtime_user_id: str,
        title: str | None = None,
    ) -> ConversationSession:
        existing = await self.get_by_external(external_session_id)
        if existing:
            if existing.user_id != self.context.user_id:
                raise PermissionError("Session belongs to another user")
            if existing.target_type != "team" or existing.team_config_id != team_config_id:
                raise ValueError("Session is pinned to a different target")
            return existing
        row = ConversationSession(
            id=new_id(),
            tenant_id=self.context.tenant_id,
            external_session_id=external_session_id,
            target_type="team",
            team_config_id=team_config_id,
            team_version_id=team_version_id,
            user_id=self.context.user_id,
            title=title,
            runtime_session_id=runtime_session_id,
            runtime_user_id=runtime_user_id,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def pin_workflow(
        self,
        *,
        external_session_id: str,
        workflow_config_id: uuid.UUID,
        workflow_version_id: uuid.UUID,
        runtime_session_id: str,
        runtime_user_id: str,
        title: str | None = None,
    ) -> ConversationSession:
        existing = await self.get_by_external(external_session_id)
        if existing:
            if existing.user_id != self.context.user_id:
                raise PermissionError("Session belongs to another user")
            if (
                existing.target_type != "workflow"
                or existing.workflow_config_id != workflow_config_id
            ):
                raise ValueError("Session is pinned to a different target")
            return existing
        row = ConversationSession(
            id=new_id(),
            tenant_id=self.context.tenant_id,
            external_session_id=external_session_id,
            target_type="workflow",
            workflow_config_id=workflow_config_id,
            workflow_version_id=workflow_version_id,
            user_id=self.context.user_id,
            title=title,
            runtime_session_id=runtime_session_id,
            runtime_user_id=runtime_user_id,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def get_by_external(self, external_session_id: str) -> ConversationSession | None:
        return await self.session.scalar(
            self.scoped(
                select(ConversationSession).where(
                    ConversationSession.external_session_id == external_session_id
                ),
                ConversationSession,
            )
        )

    async def list_for_user(
        self,
        *,
        target_type: str | None = None,
        target_id: uuid.UUID | None = None,
        include_all_users: bool = False,
    ) -> Sequence[ConversationSession]:
        statement = select(ConversationSession)
        if not include_all_users or not self.context.can_administer():
            statement = statement.where(ConversationSession.user_id == self.context.user_id)
        if target_type:
            statement = statement.where(ConversationSession.target_type == target_type)
        if target_id:
            column = (
                ConversationSession.agent_config_id
                if target_type == "agent"
                else (
                    ConversationSession.team_config_id
                    if target_type == "team"
                    else ConversationSession.workflow_config_id
                )
            )
            statement = statement.where(column == target_id)
        rows = await self.session.scalars(
            self.scoped(
                statement.order_by(ConversationSession.updated_at.desc()),
                ConversationSession,
            )
        )
        return rows.all()

    async def get_accessible(
        self, external_session_id: str, *, allow_admin: bool = True
    ) -> ConversationSession | None:
        row = await self.get_by_external(external_session_id)
        if row is None:
            return None
        if row.user_id != self.context.user_id and not (
            allow_admin and self.context.can_administer()
        ):
            return None
        return row

    async def touch_run(
        self,
        external_session_id: str,
        *,
        run_id: str | None,
        status: str,
        title: str | None = None,
    ) -> None:
        values: dict[str, Any] = {
            "last_run_id": run_id,
            "status": status,
            "updated_at": func.now(),
        }
        if title:
            values["title"] = title[:255]
        await self.session.execute(
            update(ConversationSession)
            .where(
                ConversationSession.tenant_id == self.context.tenant_id,
                ConversationSession.external_session_id == external_session_id,
                ConversationSession.user_id == self.context.user_id,
            )
            .values(**values)
        )

    async def delete(self, external_session_id: str, *, allow_admin: bool = True) -> bool:
        row = await self.get_accessible(external_session_id, allow_admin=allow_admin)
        if row is None:
            return False
        await self.session.delete(row)
        await self.audit(
            action="session.delete",
            resource_type="conversation_session",
            resource_id=str(row.id),
        )
        return True
