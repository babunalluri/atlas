import hashlib
import json
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Select, String, cast, delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AgentConfig,
    AgentStatus,
    AgentToolBinding,
    AgentVersion,
    ApprovalBinding,
    ApprovalStatus,
    AuditEvent,
    ChannelBinding,
    ConversationSession,
    EndUser,
    EndUserSessionBind,
    KnowledgeBase,
    KnowledgeChunk,
    KnowledgeSource,
    Membership,
    PlatformPythonPackage,
    Role,
    ServiceAccount,
    TeamAssignment,
    TeamConfig,
    TeamMember,
    TeamToolBinding,
    TeamVersion,
    Tenant,
    TenantCredential,
    ToolDefinition,
    ToolDefinitionVersion,
    UserNotification,
    UserVaultEntry,
    VerificationChallenge,
    WorkflowAssignment,
    WorkflowConfig,
    WorkflowStep,
    WorkflowVersion,
)
from app.tenancy.context import TenantContext
from app.tenancy.ids import new_id, validate_slug


async def _stored_config_domain(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    domain: str | None,
) -> str:
    from app.domains.catalog_groups import stored_config_domain

    return await stored_config_domain(session, tenant_id, domain)


def _validate_cel_condition_expression(expression: str) -> None:
    """Reject invalid CEL; fail closed when the evaluator is unavailable."""
    try:
        from agno.workflow.cel import CEL_AVAILABLE, validate_cel_expression
    except ImportError:
        raise ValueError("Invalid CEL condition expression")
    if not CEL_AVAILABLE:
        raise ValueError("Invalid CEL condition expression")
    if not validate_cel_expression(expression):
        raise ValueError("Invalid CEL condition expression")


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

    async def get_by_auth_org(self, auth_org_id: str) -> Tenant | None:
        return await self.session.scalar(select(Tenant).where(Tenant.auth_org_id == auth_org_id))

    async def ensure(
        self, *, auth_org_id: str, slug: str, name: str, branding: dict[str, Any] | None = None
    ) -> Tenant:
        existing = await self.get_by_auth_org(auth_org_id)
        if existing:
            return existing
        tenant = Tenant(
            id=new_id(),
            auth_org_id=auth_org_id,
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
        self,
        *,
        slug: str,
        name: str,
        description: str | None = None,
        domain: str | None = None,
    ) -> AgentConfig:
        config = AgentConfig(
            id=new_id(),
            tenant_id=self.context.tenant_id,
            slug=validate_slug(slug),
            name=name,
            description=description,
            domain=await _stored_config_domain(
                self.session, self.context.tenant_id, domain
            ),
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

    async def _archive_drafts(
        self, config_id: uuid.UUID, *, except_version_id: uuid.UUID | None = None
    ) -> None:
        """Supersede leftover drafts so editors never reopen an older draft than live."""
        statement = (
            update(AgentVersion)
            .where(
                AgentVersion.agent_config_id == config_id,
                AgentVersion.tenant_id == self.context.tenant_id,
                AgentVersion.status == AgentStatus.draft,
            )
            .values(status=AgentStatus.archived)
        )
        if except_version_id is not None:
            statement = statement.where(AgentVersion.id != except_version_id)
        await self.session.execute(statement)

    async def get_latest_draft(self, config_id: uuid.UUID) -> AgentVersion | None:
        """Return the newest draft newer than the live published version, if any.

        Older drafts left behind after save→save→publish are ignored so the
        editor does not reopen stale tool bindings (e.g. draft v13 vs live v14).
        """
        config = await self.get_config(config_id)
        min_version = 0
        if config and config.published_version_id:
            published = await self.get_version(
                config.published_version_id, allow_draft=False
            )
            if published is not None:
                min_version = published.version
        return await self.session.scalar(
            self.scoped(
                select(AgentVersion)
                .where(
                    AgentVersion.agent_config_id == config_id,
                    AgentVersion.status == AgentStatus.draft,
                    AgentVersion.version > min_version,
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
        guardrails: dict[str, Any] | None = None,
        framework_adapter: str = "agno",
    ) -> AgentVersion:
        config = await self.get_config(config_id)
        if config is None:
            raise LookupError("Agent config not found")
        await self._archive_drafts(config_id)
        team_config: dict[str, Any] = {}
        if knowledge_base_id:
            team_config["knowledge_base_id"] = str(knowledge_base_id)
        if guardrails:
            cleaned = {
                "prompt_injection": bool(guardrails.get("prompt_injection")),
                "pii_detection": bool(guardrails.get("pii_detection")),
                "openai_moderation": bool(guardrails.get("openai_moderation")),
            }
            if any(cleaned.values()):
                team_config["guardrails"] = cleaned
        adapter = (framework_adapter or "agno").strip() or "agno"
        if adapter != "agno":
            team_config["framework_adapter"] = adapter
        version: AgentVersion | None = None
        for _attempt in range(5):
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
            candidate = AgentVersion(
                id=new_id(),
                tenant_id=self.context.tenant_id,
                agent_config_id=config_id,
                version=next_version,
                status=AgentStatus.draft,
                instructions=instructions,
                model_id=model_id,
                temperature=temperature,
                memory_mode=memory_mode,
                team_config=team_config or None,
                created_by=self.context.user_id,
            )
            try:
                async with self.session.begin_nested():
                    self.session.add(candidate)
                    await self.session.flush()
                version = candidate
                break
            except IntegrityError:
                continue
        if version is None:
            raise RuntimeError("Could not allocate a unique agent version number")
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
                if (
                    definition.kind == "tenant_python"
                    and definition.published_version_id is None
                ):
                    raise LookupError(
                        "Editable Python tool must be published before attaching"
                    )
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
        await self._archive_drafts(version.agent_config_id, except_version_id=version.id)
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

    async def restore_version(
        self, config_id: uuid.UUID, version_id: uuid.UUID, *, as_draft: bool = False
    ) -> AgentVersion:
        """Restore a historical agent version (Atlas-owned snapshots)."""
        config = await self.get_config(config_id)
        if config is None:
            raise LookupError("Agent not found")
        version = await self.get_version(version_id, allow_draft=True)
        if version is None or version.agent_config_id != config_id:
            raise LookupError("Agent version not found")

        if as_draft:
            kb_raw = (version.team_config or {}).get("knowledge_base_id")
            knowledge_base_id = uuid.UUID(str(kb_raw)) if kb_raw else None
            guardrails = (version.team_config or {}).get("guardrails")
            framework_adapter = str(
                (version.team_config or {}).get("framework_adapter") or "agno"
            )
            tools = [
                {
                    "tool_key": binding.tool_key,
                    "tool_definition_id": binding.tool_definition_id,
                    "config": binding.config or {},
                    "credential_id": binding.credential_id,
                }
                for binding in await self.bindings(version.id)
            ]
            draft = await self.create_draft(
                config_id=config_id,
                instructions=version.instructions,
                model_id=version.model_id,
                temperature=version.temperature,
                memory_mode=version.memory_mode,
                tools=tools,
                knowledge_base_id=knowledge_base_id,
                guardrails=guardrails if isinstance(guardrails, dict) else None,
                framework_adapter=framework_adapter,
            )
            await self.audit(
                action="agent.restore_draft",
                resource_type="agent_version",
                resource_id=str(draft.id),
                details={
                    "source_version_id": str(version.id),
                    "source_version": version.version,
                    "draft_version": draft.version,
                },
            )
            return draft

        if version.status == AgentStatus.draft:
            return await self.publish(version.id)

        await self.session.execute(
            update(AgentConfig)
            .where(
                AgentConfig.id == config_id,
                AgentConfig.tenant_id == self.context.tenant_id,
            )
            .values(published_version_id=version.id)
        )
        await self.session.flush()
        await self.audit(
            action="agent.restore",
            resource_type="agent_version",
            resource_id=str(version.id),
            details={"version": version.version},
        )
        return version

    async def delete_config(self, config_id: uuid.UUID) -> None:
        config = await self.get_config(config_id)
        if config is None:
            raise LookupError("Agent not found")
        team_refs = await self.session.scalar(
            select(func.count())
            .select_from(TeamMember)
            .where(
                TeamMember.tenant_id == self.context.tenant_id,
                TeamMember.agent_config_id == config_id,
            )
        )
        if int(team_refs or 0) > 0:
            raise ValueError("Agent is used by a team — remove it from teams first")
        workflow_refs = await self.session.scalar(
            select(func.count())
            .select_from(WorkflowStep)
            .where(
                WorkflowStep.tenant_id == self.context.tenant_id,
                WorkflowStep.agent_config_id == config_id,
            )
        )
        if int(workflow_refs or 0) > 0:
            raise ValueError(
                "Agent is used by a workflow — remove it from workflows first"
            )
        config.published_version_id = None
        await self.session.flush()
        await self.session.delete(config)
        await self.session.flush()
        await self.audit(
            action="agent.delete",
            resource_type="agent_config",
            resource_id=str(config_id),
        )


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
        self,
        *,
        slug: str,
        name: str,
        description: str | None = None,
        domain: str | None = None,
    ) -> TeamConfig:
        config = TeamConfig(
            id=new_id(),
            tenant_id=self.context.tenant_id,
            slug=validate_slug(slug),
            name=name,
            description=description,
            domain=await _stored_config_domain(
                self.session, self.context.tenant_id, domain
            ),
        )
        self.session.add(config)
        await self.session.flush()
        await self.audit(
            action="team.create", resource_type="team_config", resource_id=str(config.id)
        )
        return config

    async def update_config(
        self,
        config_id: uuid.UUID,
        *,
        slug: str | None = None,
        name: str | None = None,
        description: str | None = None,
    ) -> TeamConfig | None:
        config = await self.get_config(config_id)
        if config is None:
            return None
        if slug is not None:
            normalized = validate_slug(slug)
            if normalized != config.slug:
                existing = await self.get_config_by_slug(normalized)
                if existing is not None and existing.id != config.id:
                    raise ValueError(f"Team slug '{normalized}' is already in use")
                config.slug = normalized
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

    async def _archive_drafts(
        self, config_id: uuid.UUID, *, except_version_id: uuid.UUID | None = None
    ) -> None:
        statement = (
            update(TeamVersion)
            .where(
                TeamVersion.team_config_id == config_id,
                TeamVersion.tenant_id == self.context.tenant_id,
                TeamVersion.status == AgentStatus.draft,
            )
            .values(status=AgentStatus.archived)
        )
        if except_version_id is not None:
            statement = statement.where(TeamVersion.id != except_version_id)
        await self.session.execute(statement)

    async def get_latest_draft(self, config_id: uuid.UUID) -> TeamVersion | None:
        config = await self.get_config(config_id)
        min_version = 0
        if config and config.published_version_id:
            published = await self.get_version(config.published_version_id, allow_draft=False)
            if published is not None:
                min_version = published.version
        return await self.session.scalar(
            self.scoped(
                select(TeamVersion)
                .where(
                    TeamVersion.team_config_id == config_id,
                    TeamVersion.status == AgentStatus.draft,
                    TeamVersion.version > min_version,
                )
                .order_by(TeamVersion.version.desc()),
                TeamVersion,
            )
        )

    async def list_versions(self, config_id: uuid.UUID) -> Sequence[TeamVersion]:
        rows = await self.session.scalars(
            self.scoped(
                select(TeamVersion)
                .where(TeamVersion.team_config_id == config_id)
                .order_by(TeamVersion.version.desc()),
                TeamVersion,
            )
        )
        return rows.all()

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

    async def bindings(self, version_id: uuid.UUID) -> Sequence[TeamToolBinding]:
        rows = await self.session.scalars(
            self.scoped(
                select(TeamToolBinding).where(TeamToolBinding.team_version_id == version_id),
                TeamToolBinding,
            )
        )
        return rows.all()

    async def restore_version(
        self, config_id: uuid.UUID, version_id: uuid.UUID, *, as_draft: bool = False
    ) -> TeamVersion:
        """Restore a historical team version.

        When ``as_draft`` is False (default), sets ``published_version_id`` to the
        selected immutable snapshot so live traffic uses that version again.
        When ``as_draft`` is True, clones the snapshot into a new editable draft.
        """
        config = await self.get_config(config_id)
        if config is None:
            raise LookupError("Team not found")
        version = await self.get_version(version_id, allow_draft=True)
        if version is None or version.team_config_id != config_id:
            raise LookupError("Team version not found")

        if as_draft:
            member_ids = [member.agent_config_id for member in await self.members(version.id)]
            tools = [
                {
                    "tool_key": binding.tool_key,
                    "tool_definition_id": binding.tool_definition_id,
                    "config": binding.config or {},
                    "credential_id": binding.credential_id,
                }
                for binding in await self.bindings(version.id)
            ]
            draft = await self.create_draft(
                config_id=config_id,
                instructions=version.instructions,
                mode=version.mode,
                model_id=version.model_id,
                temperature=version.temperature,
                member_config_ids=member_ids,
                tools=tools,
            )
            await self.audit(
                action="team.restore_draft",
                resource_type="team_version",
                resource_id=str(draft.id),
                details={
                    "source_version_id": str(version.id),
                    "source_version": version.version,
                    "draft_version": draft.version,
                },
            )
            return draft

        if version.status == AgentStatus.draft:
            # Promote the draft via normal publish (pins members, sets pointer).
            return await self.publish(version.id)

        await self.session.execute(
            update(TeamConfig)
            .where(
                TeamConfig.id == config_id,
                TeamConfig.tenant_id == self.context.tenant_id,
            )
            .values(published_version_id=version.id)
        )
        await self.session.flush()
        await self.audit(
            action="team.restore",
            resource_type="team_version",
            resource_id=str(version.id),
            details={"version": version.version},
        )
        return version

    async def create_draft(
        self,
        *,
        config_id: uuid.UUID,
        instructions: str,
        mode: str,
        model_id: str,
        temperature: float,
        member_config_ids: list[uuid.UUID],
        tools: list[dict[str, Any]] | None = None,
    ) -> TeamVersion:
        if mode not in {"route", "coordinate"}:
            raise ValueError("Team mode must be route or coordinate")
        if len(set(member_config_ids)) != len(member_config_ids):
            raise ValueError("A team cannot contain the same agent more than once")
        config = await self.get_config(config_id)
        if config is None:
            raise LookupError("Team config not found")

        await self._archive_drafts(config_id)
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

        version: TeamVersion | None = None
        next_version = 0
        for _attempt in range(5):
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
            candidate = TeamVersion(
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
            try:
                async with self.session.begin_nested():
                    self.session.add(candidate)
                    await self.session.flush()
                version = candidate
                break
            except IntegrityError:
                continue
        if version is None:
            raise RuntimeError("Could not allocate a unique team version number")
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
                if (
                    definition.kind == "tenant_python"
                    and definition.published_version_id is None
                ):
                    raise LookupError(
                        "Editable Python tool must be published before attaching"
                    )
            self.session.add(
                TeamToolBinding(
                    id=new_id(),
                    tenant_id=self.context.tenant_id,
                    team_version_id=version.id,
                    tool_key=tool.get("tool_key"),
                    tool_definition_id=definition_id,
                    config=tool.get("config") or {},
                    credential_id=credential_id,
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
        # Members are optional: a team may run as leader-only with Team.tools.
        members = list(await self.members(version.id))

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
        await self._archive_drafts(version.team_config_id, except_version_id=version.id)
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

    async def delete_config(self, config_id: uuid.UUID) -> None:
        config = await self.get_config(config_id)
        if config is None:
            raise LookupError("Team not found")
        workflow_refs = await self.session.scalar(
            select(func.count())
            .select_from(WorkflowStep)
            .where(
                WorkflowStep.tenant_id == self.context.tenant_id,
                WorkflowStep.team_config_id == config_id,
            )
        )
        if int(workflow_refs or 0) > 0:
            raise ValueError(
                "Team is used by a workflow — remove it from workflows first"
            )
        await self.session.execute(
            delete(TeamAssignment).where(
                TeamAssignment.tenant_id == self.context.tenant_id,
                TeamAssignment.team_config_id == config_id,
            )
        )
        config.published_version_id = None
        await self.session.flush()
        await self.session.delete(config)
        await self.session.flush()
        await self.audit(
            action="team.delete",
            resource_type="team_config",
            resource_id=str(config_id),
        )

    async def list_available_for_user(self, user_id: str) -> Sequence[TeamConfig]:
        rows = await self.session.scalars(
            select(TeamConfig)
            .join(
                TeamAssignment,
                TeamAssignment.team_config_id == TeamConfig.id,
            )
            .where(
                TeamConfig.tenant_id == self.context.tenant_id,
                TeamConfig.published_version_id.is_not(None),
                TeamAssignment.tenant_id == self.context.tenant_id,
                TeamAssignment.user_id == user_id,
            )
            .order_by(TeamConfig.name)
        )
        return rows.all()

    async def is_assigned(self, config_id: uuid.UUID, user_id: str) -> bool:
        return (
            await self.session.scalar(
                select(TeamAssignment.id).where(
                    TeamAssignment.tenant_id == self.context.tenant_id,
                    TeamAssignment.team_config_id == config_id,
                    TeamAssignment.user_id == user_id,
                )
            )
            is not None
        )

    async def assigned_team_ids(self, user_id: str) -> list[uuid.UUID]:
        rows = await self.session.scalars(
            select(TeamAssignment.team_config_id)
            .where(
                TeamAssignment.tenant_id == self.context.tenant_id,
                TeamAssignment.user_id == user_id,
            )
            .order_by(TeamAssignment.team_config_id)
        )
        return list(rows.all())

    async def replace_user_assignments(
        self, user_id: str, team_ids: Sequence[uuid.UUID]
    ) -> list[uuid.UUID]:
        normalized = sorted({value for value in team_ids})
        for team_id in normalized:
            config = await self.get_config(team_id)
            if config is None:
                raise LookupError(f"Team {team_id} not found")
            if config.published_version_id is None:
                raise ValueError(f"Team {config.slug} must be published before assignment")
        await self.session.execute(
            delete(TeamAssignment).where(
                TeamAssignment.tenant_id == self.context.tenant_id,
                TeamAssignment.user_id == user_id,
            )
        )
        self.session.add_all(
            TeamAssignment(
                id=new_id(),
                tenant_id=self.context.tenant_id,
                team_config_id=team_id,
                user_id=user_id,
                assigned_by=self.context.user_id,
            )
            for team_id in normalized
        )
        await self.session.flush()
        await self.audit(
            action="user.team_assignments.update",
            resource_type="membership",
            resource_id=user_id,
            details={"team_ids": [str(value) for value in normalized]},
        )
        return normalized

    async def ensure_user_assignments(
        self, user_id: str, team_ids: Sequence[uuid.UUID]
    ) -> list[uuid.UUID]:
        """Add missing published-team assignments without removing existing ones."""
        existing = set(await self.assigned_team_ids(user_id))
        added: list[uuid.UUID] = []
        for team_id in team_ids:
            if team_id in existing:
                continue
            config = await self.get_config(team_id)
            if config is None or config.published_version_id is None:
                continue
            self.session.add(
                TeamAssignment(
                    id=new_id(),
                    tenant_id=self.context.tenant_id,
                    team_config_id=team_id,
                    user_id=user_id,
                    assigned_by=self.context.user_id,
                )
            )
            added.append(team_id)
        if added:
            await self.session.flush()
            await self.audit(
                action="user.team_assignments.ensure",
                resource_type="membership",
                resource_id=user_id,
                details={"team_ids": [str(value) for value in added]},
            )
        return await self.assigned_team_ids(user_id)


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

    async def get_by_email(self, email: str) -> Membership | None:
        normalized = email.strip().lower()
        if not normalized:
            return None
        return await self.session.scalar(
            self.scoped(
                select(Membership).where(Membership.email == normalized),
                Membership,
            )
        )

    async def claim_pending_by_email(
        self, *, email: str, user_id: str
    ) -> Membership | None:
        """Bind a pending/orphan membership to the real sign-in account id."""
        membership = await self.get_by_email(email)
        if membership is None or not membership.is_active:
            return None
        if membership.user_id == user_id:
            return membership
        # Only rewrite placeholders / manual orphans — never steal a real account link.
        if membership.user_id.startswith("user_"):
            return None
        conflict = await self.get_by_user_id(user_id)
        if conflict is not None and conflict.id != membership.id:
            raise ValueError("Sign-in account is already linked to another user")
        old_user_id = membership.user_id
        await self.session.execute(
            update(WorkflowAssignment)
            .where(
                WorkflowAssignment.tenant_id == self.context.tenant_id,
                WorkflowAssignment.user_id == old_user_id,
            )
            .values(user_id=user_id)
        )
        await self.session.execute(
            update(TeamAssignment)
            .where(
                TeamAssignment.tenant_id == self.context.tenant_id,
                TeamAssignment.user_id == old_user_id,
            )
            .values(user_id=user_id)
        )
        await self.session.execute(
            update(ConversationSession)
            .where(
                ConversationSession.tenant_id == self.context.tenant_id,
                ConversationSession.user_id == old_user_id,
            )
            .values(user_id=user_id)
        )
        membership.user_id = user_id
        await self.session.flush()
        await self.audit(
            action="user.claim",
            resource_type="membership",
            resource_id=str(membership.id),
            details={"user_id": user_id, "from": old_user_id},
        )
        return membership

    async def rebind_user_id(
        self, membership_id: uuid.UUID, *, new_user_id: str
    ) -> Membership | None:
        membership = await self.get(membership_id)
        if membership is None:
            return None
        new_user_id = new_user_id.strip()
        if not new_user_id:
            raise ValueError("user_id is required")
        if membership.user_id == new_user_id:
            return membership
        conflict = await self.get_by_user_id(new_user_id)
        if conflict is not None and conflict.id != membership.id:
            raise ValueError("Sign-in account is already linked to another user")
        old_user_id = membership.user_id
        await self.session.execute(
            update(WorkflowAssignment)
            .where(
                WorkflowAssignment.tenant_id == self.context.tenant_id,
                WorkflowAssignment.user_id == old_user_id,
            )
            .values(user_id=new_user_id)
        )
        await self.session.execute(
            update(TeamAssignment)
            .where(
                TeamAssignment.tenant_id == self.context.tenant_id,
                TeamAssignment.user_id == old_user_id,
            )
            .values(user_id=new_user_id)
        )
        await self.session.execute(
            update(ConversationSession)
            .where(
                ConversationSession.tenant_id == self.context.tenant_id,
                ConversationSession.user_id == old_user_id,
            )
            .values(user_id=new_user_id)
        )
        membership.user_id = new_user_id
        await self.session.flush()
        await self.audit(
            action="user.rebind",
            resource_type="membership",
            resource_id=str(membership.id),
            details={"user_id": new_user_id, "from": old_user_id},
        )
        return membership

    async def create(
        self,
        *,
        user_id: str,
        display_name: str,
        email: str | None,
        role: Role,
        is_active: bool = True,
        phone: str | None = None,
        timezone: str = "UTC",
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
            email=(email or "").strip().lower() or None,
            phone=(phone or "").strip() or None,
            timezone=timezone,
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
        phone: str | None = None,
        role: Role | None = None,
        is_active: bool | None = None,
        timezone: str | None = None,
        clear_phone: bool = False,
    ) -> Membership | None:
        membership = await self.get(membership_id)
        if membership is None:
            return None
        if display_name is not None:
            membership.display_name = display_name.strip()
        if email is not None:
            membership.email = email.strip().lower() or None
        if clear_phone:
            membership.phone = None
        elif phone is not None:
            membership.phone = phone.strip() or None
        if timezone is not None:
            membership.timezone = timezone
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
        await self.session.execute(
            delete(TeamAssignment).where(
                TeamAssignment.tenant_id == self.context.tenant_id,
                TeamAssignment.user_id == membership.user_id,
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
        self,
        *,
        slug: str,
        name: str,
        description: str | None = None,
        domain: str | None = None,
    ) -> WorkflowConfig:
        config = WorkflowConfig(
            id=new_id(),
            tenant_id=self.context.tenant_id,
            slug=validate_slug(slug),
            name=name,
            description=description,
            domain=await _stored_config_domain(
                self.session, self.context.tenant_id, domain
            ),
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

    async def _archive_drafts(
        self, config_id: uuid.UUID, *, except_version_id: uuid.UUID | None = None
    ) -> None:
        statement = (
            update(WorkflowVersion)
            .where(
                WorkflowVersion.workflow_config_id == config_id,
                WorkflowVersion.tenant_id == self.context.tenant_id,
                WorkflowVersion.status == AgentStatus.draft,
            )
            .values(status=AgentStatus.archived)
        )
        if except_version_id is not None:
            statement = statement.where(WorkflowVersion.id != except_version_id)
        await self.session.execute(statement)

    async def get_latest_draft(self, config_id: uuid.UUID) -> WorkflowVersion | None:
        config = await self.get_config(config_id)
        min_version = 0
        if config and config.published_version_id:
            published = await self.get_version(config.published_version_id, allow_draft=False)
            if published is not None:
                min_version = published.version
        return await self.session.scalar(
            self.scoped(
                select(WorkflowVersion)
                .where(
                    WorkflowVersion.workflow_config_id == config_id,
                    WorkflowVersion.status == AgentStatus.draft,
                    WorkflowVersion.version > min_version,
                )
                .order_by(WorkflowVersion.version.desc()),
                WorkflowVersion,
            )
        )

    async def list_versions(self, config_id: uuid.UUID) -> Sequence[WorkflowVersion]:
        rows = await self.session.scalars(
            self.scoped(
                select(WorkflowVersion)
                .where(WorkflowVersion.workflow_config_id == config_id)
                .order_by(WorkflowVersion.version.desc()),
                WorkflowVersion,
            )
        )
        return rows.all()

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

    async def restore_version(
        self, config_id: uuid.UUID, version_id: uuid.UUID, *, as_draft: bool = False
    ) -> WorkflowVersion:
        """Restore a historical workflow version (Atlas-owned snapshots)."""
        config = await self.get_config(config_id)
        if config is None:
            raise LookupError("Workflow not found")
        version = await self.get_version(version_id, allow_draft=True)
        if version is None or version.workflow_config_id != config_id:
            raise LookupError("Workflow version not found")

        if as_draft:
            steps = [
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
                for step in await self.steps(version.id)
            ]
            draft = await self.create_draft(
                config_id=config_id,
                mode=version.mode,
                steps=steps,
            )
            await self.audit(
                action="workflow.restore_draft",
                resource_type="workflow_version",
                resource_id=str(draft.id),
                details={
                    "source_version_id": str(version.id),
                    "source_version": version.version,
                    "draft_version": draft.version,
                },
            )
            return draft

        if version.status == AgentStatus.draft:
            return await self.publish(version.id)

        await self.session.execute(
            update(WorkflowConfig)
            .where(
                WorkflowConfig.id == config_id,
                WorkflowConfig.tenant_id == self.context.tenant_id,
            )
            .values(published_version_id=version.id)
        )
        await self.session.flush()
        await self.audit(
            action="workflow.restore",
            resource_type="workflow_version",
            resource_id=str(version.id),
            details={"version": version.version},
        )
        return version

    async def resolve_published_team_step(
        self,
        workflow_id: uuid.UUID,
        team_id: uuid.UUID,
    ) -> uuid.UUID:
        """Return pinned team_version_id if team is a step on the published workflow."""
        config = await self.get_config(workflow_id)
        if config is None or config.published_version_id is None:
            raise LookupError("Published workflow not found")
        for step in await self.steps(config.published_version_id):
            if step.target_type == "team" and step.team_config_id == team_id:
                if step.team_version_id is None:
                    raise LookupError("Workflow team step has no pinned version")
                return step.team_version_id
        raise LookupError("Team is not a step in this published workflow")

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
        for step in steps:
            expression = step.get("condition_expression")
            if expression:
                _validate_cel_condition_expression(str(expression))
        config = await self.get_config(config_id)
        if config is None:
            raise LookupError("Workflow config not found")

        await self._archive_drafts(config_id)
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

        version: WorkflowVersion | None = None
        next_version = 0
        for _attempt in range(5):
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
            candidate = WorkflowVersion(
                id=new_id(),
                tenant_id=self.context.tenant_id,
                workflow_config_id=config_id,
                version=next_version,
                status=AgentStatus.draft,
                mode=mode,
                created_by=self.context.user_id,
            )
            try:
                async with self.session.begin_nested():
                    self.session.add(candidate)
                    await self.session.flush()
                version = candidate
                break
            except IntegrityError:
                continue
        if version is None:
            raise RuntimeError("Could not allocate a unique workflow version number")
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
        await self._archive_drafts(
            version.workflow_config_id, except_version_id=version.id
        )
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

    async def delete_config(self, config_id: uuid.UUID) -> None:
        config = await self.get_config(config_id)
        if config is None:
            raise LookupError("Workflow not found")
        config.published_version_id = None
        await self.session.flush()
        await self.session.execute(
            delete(WorkflowAssignment).where(
                WorkflowAssignment.tenant_id == self.context.tenant_id,
                WorkflowAssignment.workflow_config_id == config_id,
            )
        )
        await self.session.delete(config)
        await self.session.flush()
        await self.audit(
            action="workflow.delete",
            resource_type="workflow_config",
            resource_id=str(config_id),
        )


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

    async def delete(self, credential_id: uuid.UUID) -> bool:
        credential = await self.get(credential_id)
        if credential is None:
            return False

        tool_refs = await self.session.scalar(
            select(func.count())
            .select_from(ToolDefinition)
            .where(
                ToolDefinition.tenant_id == self.context.tenant_id,
                ToolDefinition.credential_id == credential_id,
            )
        )
        if int(tool_refs or 0) > 0:
            raise ValueError(
                "Credential is attached to a tool and cannot be deleted; detach it first"
            )

        agent_refs = await self.session.scalar(
            select(func.count())
            .select_from(AgentToolBinding)
            .where(
                AgentToolBinding.tenant_id == self.context.tenant_id,
                AgentToolBinding.credential_id == credential_id,
            )
        )
        if int(agent_refs or 0) > 0:
            raise ValueError(
                "Credential is attached to an agent tool and cannot be deleted; "
                "detach it first"
            )

        team_refs = await self.session.scalar(
            select(func.count())
            .select_from(TeamToolBinding)
            .where(
                TeamToolBinding.tenant_id == self.context.tenant_id,
                TeamToolBinding.credential_id == credential_id,
            )
        )
        if int(team_refs or 0) > 0:
            raise ValueError(
                "Credential is attached to a team tool and cannot be deleted; "
                "detach it first"
            )

        await self.audit(
            action="credential.delete",
            resource_type="tenant_credential",
            resource_id=str(credential.id),
            details={"provider": credential.provider, "name": credential.name},
        )
        await self.session.delete(credential)
        await self.session.flush()
        return True


class ChannelBindingRepository(TenantRepository):
    async def list(self) -> Sequence[ChannelBinding]:
        rows = await self.session.scalars(
            self.scoped(
                select(ChannelBinding).order_by(ChannelBinding.created_at.desc()),
                ChannelBinding,
            )
        )
        return rows.all()

    async def get(self, binding_id: uuid.UUID) -> ChannelBinding | None:
        return await self.session.scalar(
            self.scoped(
                select(ChannelBinding).where(ChannelBinding.id == binding_id),
                ChannelBinding,
            )
        )

    async def list_by_provider(
        self, provider: str, *, active_only: bool = True
    ) -> Sequence[ChannelBinding]:
        statement = select(ChannelBinding).where(ChannelBinding.provider == provider)
        if active_only:
            statement = statement.where(ChannelBinding.active.is_(True))
        rows = await self.session.scalars(
            self.scoped(statement.order_by(ChannelBinding.created_at.desc()), ChannelBinding)
        )
        return rows.all()

    async def create(
        self,
        *,
        provider: str,
        credential_id: uuid.UUID,
        target_type: str,
        target_config_id: uuid.UUID,
        external_config: dict[str, Any] | None = None,
        active: bool = True,
    ) -> ChannelBinding:
        if await CredentialRepository(self.session, self.context).get(credential_id) is None:
            raise LookupError("Credential not found for tenant")
        row = ChannelBinding(
            id=new_id(),
            tenant_id=self.context.tenant_id,
            provider=provider,
            credential_id=credential_id,
            target_type=target_type,
            target_config_id=target_config_id,
            external_config=external_config or {},
            active=active,
        )
        self.session.add(row)
        await self.session.flush()
        await self.audit(
            action="channel_binding.create",
            resource_type="channel_binding",
            resource_id=str(row.id),
            details={"provider": provider, "target_type": target_type},
        )
        return row

    async def update(
        self,
        binding_id: uuid.UUID,
        *,
        credential_id: uuid.UUID | None = None,
        target_type: str | None = None,
        target_config_id: uuid.UUID | None = None,
        external_config: dict[str, Any] | None = None,
        active: bool | None = None,
    ) -> ChannelBinding | None:
        row = await self.get(binding_id)
        if row is None:
            return None
        if credential_id is not None:
            if await CredentialRepository(self.session, self.context).get(credential_id) is None:
                raise LookupError("Credential not found for tenant")
            row.credential_id = credential_id
        if target_type is not None:
            row.target_type = target_type
        if target_config_id is not None:
            row.target_config_id = target_config_id
        if external_config is not None:
            row.external_config = external_config
        if active is not None:
            row.active = active
        await self.session.flush()
        return row

    async def delete(self, binding_id: uuid.UUID) -> bool:
        row = await self.get(binding_id)
        if row is None:
            return False
        await self.session.delete(row)
        await self.session.flush()
        await self.audit(
            action="channel_binding.delete",
            resource_type="channel_binding",
            resource_id=str(binding_id),
        )
        return True


class UserVaultRepository(TenantRepository):
    """Per-user secrets/variables scoped to the caller's Clerk subject."""

    async def list_for_user(self, user_id: str) -> Sequence[UserVaultEntry]:
        rows = await self.session.scalars(
            self.scoped(
                select(UserVaultEntry)
                .where(UserVaultEntry.user_id == user_id)
                .order_by(UserVaultEntry.name.asc()),
                UserVaultEntry,
            )
        )
        return rows.all()

    async def get_for_user(self, user_id: str, name: str) -> UserVaultEntry | None:
        return await self.session.scalar(
            self.scoped(
                select(UserVaultEntry).where(
                    UserVaultEntry.user_id == user_id,
                    UserVaultEntry.name == name,
                ),
                UserVaultEntry,
            )
        )

    async def upsert(
        self,
        *,
        user_id: str,
        name: str,
        kind: str,
        encrypted_value: str,
        key_version: str,
    ) -> UserVaultEntry:
        existing = await self.get_for_user(user_id, name)
        if existing is not None:
            existing.kind = kind
            existing.encrypted_value = encrypted_value
            existing.key_version = key_version
            await self.session.flush()
            await self.session.refresh(existing)
            await self.audit(
                action="user_vault.update",
                resource_type="user_vault_entry",
                resource_id=str(existing.id),
                details={"name": name, "kind": kind},
            )
            return existing
        row = UserVaultEntry(
            id=new_id(),
            tenant_id=self.context.tenant_id,
            user_id=user_id,
            name=name,
            kind=kind,
            encrypted_value=encrypted_value,
            key_version=key_version,
        )
        self.session.add(row)
        await self.session.flush()
        await self.session.refresh(row)
        await self.audit(
            action="user_vault.create",
            resource_type="user_vault_entry",
            resource_id=str(row.id),
            details={"name": name, "kind": kind},
        )
        return row

    async def delete_for_user(self, user_id: str, name: str) -> bool:
        row = await self.get_for_user(user_id, name)
        if row is None:
            return False
        await self.session.delete(row)
        await self.session.flush()
        await self.audit(
            action="user_vault.delete",
            resource_type="user_vault_entry",
            resource_id=str(row.id),
            details={"name": name},
        )
        return True


class UserNotificationRepository(TenantRepository):
    """Per-user in-app notifications (fan-out rows share a batch_id)."""

    MAX_FANOUT = 500

    async def create_batch(
        self,
        *,
        title: str,
        body: str,
        created_by: str,
        audience: str,
        recipient_user_ids: Sequence[str],
    ) -> tuple[uuid.UUID, list[UserNotification]]:
        recipients = [
            uid.strip()
            for uid in recipient_user_ids
            if uid and uid.strip() and not uid.strip().startswith("invite:")
        ]
        # Preserve order, drop dupes.
        seen: set[str] = set()
        unique: list[str] = []
        for uid in recipients:
            if uid in seen:
                continue
            seen.add(uid)
            unique.append(uid)
        if not unique:
            raise ValueError("No recipients for notification")
        if len(unique) > self.MAX_FANOUT:
            raise ValueError(
                f"Too many recipients ({len(unique)}); max is {self.MAX_FANOUT}"
            )
        batch_id = new_id()
        rows: list[UserNotification] = []
        for uid in unique:
            row = UserNotification(
                id=new_id(),
                tenant_id=self.context.tenant_id,
                batch_id=batch_id,
                user_id=uid,
                title=title,
                body=body,
                created_by=created_by,
                audience=audience,
            )
            self.session.add(row)
            rows.append(row)
        await self.session.flush()
        await self.audit(
            action="notification.send",
            resource_type="user_notification_batch",
            resource_id=str(batch_id),
            details={
                "audience": audience,
                "recipient_count": len(rows),
                "title": title[:80],
            },
        )
        return batch_id, rows

    async def list_for_user(
        self,
        user_id: str,
        *,
        unread_only: bool = False,
        limit: int = 50,
    ) -> Sequence[UserNotification]:
        stmt = (
            select(UserNotification)
            .where(UserNotification.user_id == user_id)
            .order_by(UserNotification.created_at.desc())
            .limit(max(1, min(limit, 100)))
        )
        if unread_only:
            stmt = stmt.where(UserNotification.read_at.is_(None))
        rows = await self.session.scalars(self.scoped(stmt, UserNotification))
        return rows.all()

    async def unread_count(self, user_id: str) -> int:
        value = await self.session.scalar(
            self.scoped(
                select(func.count())
                .select_from(UserNotification)
                .where(
                    UserNotification.user_id == user_id,
                    UserNotification.read_at.is_(None),
                ),
                UserNotification,
            )
        )
        return int(value or 0)

    async def get_for_user(
        self, user_id: str, notification_id: uuid.UUID
    ) -> UserNotification | None:
        return await self.session.scalar(
            self.scoped(
                select(UserNotification).where(
                    UserNotification.id == notification_id,
                    UserNotification.user_id == user_id,
                ),
                UserNotification,
            )
        )

    async def mark_read(
        self, user_id: str, notification_id: uuid.UUID
    ) -> UserNotification | None:
        row = await self.get_for_user(user_id, notification_id)
        if row is None:
            return None
        if row.read_at is None:
            row.read_at = datetime.now(UTC)
            await self.session.flush()
        return row

    async def mark_all_read(self, user_id: str) -> int:
        now = datetime.now(UTC)
        result = await self.session.execute(
            update(UserNotification)
            .where(
                UserNotification.tenant_id == self.context.tenant_id,
                UserNotification.user_id == user_id,
                UserNotification.read_at.is_(None),
            )
            .values(read_at=now, updated_at=now)
        )
        await self.session.flush()
        return int(result.rowcount or 0)

    async def list_sent_batches(self, *, limit: int = 40) -> list[dict[str, Any]]:
        """Recent admin sends grouped by batch_id (newest first)."""
        capped = max(1, min(limit, 100))
        batch_agg = (
            select(
                UserNotification.batch_id.label("batch_id"),
                func.max(UserNotification.created_at).label("sent_at"),
                func.count().label("recipient_count"),
                # Postgres has no min(uuid); use textual min for a stable sample row.
                func.min(cast(UserNotification.id, String)).label("sample_id"),
            )
            .where(UserNotification.tenant_id == self.context.tenant_id)
            .group_by(UserNotification.batch_id)
            .subquery()
        )
        rows = (
            await self.session.execute(
                select(
                    UserNotification,
                    batch_agg.c.recipient_count,
                    batch_agg.c.sent_at,
                )
                .join(
                    batch_agg,
                    cast(UserNotification.id, String) == batch_agg.c.sample_id,
                )
                .order_by(batch_agg.c.sent_at.desc())
                .limit(capped)
            )
        ).all()
        return [
            {
                "batch_id": row.batch_id,
                "title": row.title,
                "body": row.body,
                "audience": row.audience,
                "created_by": row.created_by,
                "recipient_count": int(recipient_count),
                "created_at": sent_at,
            }
            for row, recipient_count, sent_at in rows
        ]


class ServiceAccountRepository(TenantRepository):
    async def list_accounts(self) -> Sequence[ServiceAccount]:
        rows = await self.session.scalars(
            self.scoped(
                select(ServiceAccount).order_by(ServiceAccount.created_at.desc()),
                ServiceAccount,
            )
        )
        return rows.all()

    async def get(self, account_id: uuid.UUID) -> ServiceAccount | None:
        return await self.session.scalar(
            self.scoped(
                select(ServiceAccount).where(ServiceAccount.id == account_id),
                ServiceAccount,
            )
        )

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

    async def get(
        self, definition_id: uuid.UUID, *, lock: bool = False
    ) -> ToolDefinition | None:
        statement = select(ToolDefinition).where(ToolDefinition.id == definition_id)
        if lock:
            statement = statement.with_for_update()
        return await self.session.scalar(self.scoped(statement, ToolDefinition))

    async def get_by_slug(self, slug: str) -> ToolDefinition | None:
        return await self.session.scalar(
            self.scoped(
                select(ToolDefinition).where(ToolDefinition.slug == slug),
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
        # Server-side onupdate (updated_at) expires attrs after flush; refresh so
        # sync serializers like _out() never trigger MissingGreenlet lazy loads.
        await self.session.refresh(definition)
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
        await self.session.refresh(definition)
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

    async def get(
        self, version_id: uuid.UUID, *, lock: bool = False
    ) -> ToolDefinitionVersion | None:
        statement = select(ToolDefinitionVersion).where(
            ToolDefinitionVersion.id == version_id
        )
        if lock:
            statement = statement.with_for_update()
        return await self.session.scalar(
            self.scoped(statement, ToolDefinitionVersion)
        )

    async def latest_draft(
        self, tool_definition_id: uuid.UUID, *, lock: bool = False
    ) -> ToolDefinitionVersion | None:
        statement = (
            select(ToolDefinitionVersion)
            .where(
                ToolDefinitionVersion.tool_definition_id == tool_definition_id,
                ToolDefinitionVersion.status.in_(("draft", "validated")),
            )
            .order_by(ToolDefinitionVersion.version.desc())
            .limit(1)
        )
        if lock:
            statement = statement.with_for_update()
        return await self.session.scalar(
            self.scoped(statement, ToolDefinitionVersion)
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
        await self.session.refresh(tool)
        await self.audit(
            action="tool.publish",
            resource_type="tool_definition",
            resource_id=str(tool.id),
            details={"version_id": str(row.id), "version": row.version},
        )
        return row

    def _apply_version_to_tool(
        self, tool: ToolDefinition, row: ToolDefinitionVersion, *, version_status: str
    ) -> None:
        config = dict(tool.config or {})
        config["source_code"] = row.source_code
        config["dependencies"] = list(row.dependencies or [])
        config["capabilities"] = list(row.capabilities or [])
        config["settings"] = dict(row.settings or {})
        config["version_status"] = version_status
        tool.config = config
        if any(bool(item.get("mutating")) for item in row.capabilities if isinstance(item, dict)):
            tool.approval_required = True

    async def restore_version(
        self,
        tool: ToolDefinition,
        version_id: uuid.UUID,
        *,
        as_draft: bool,
        created_by: str,
    ) -> ToolDefinitionVersion:
        """Restore a historical tool source snapshot."""
        version = await self.get(version_id)
        if version is None or version.tool_definition_id != tool.id:
            raise LookupError("Tool version not found")

        if as_draft:
            draft = await self.upsert_draft(
                tool_definition_id=tool.id,
                source_code=version.source_code,
                dependencies=list(version.dependencies or []),
                capabilities=list(version.capabilities or []),
                settings=dict(version.settings or {}),
                created_by=created_by,
            )
            self._apply_version_to_tool(tool, draft, version_status="draft")
            await self.session.flush()
            await self.session.refresh(tool)
            await self.audit(
                action="tool.restore_draft",
                resource_type="tool_definition",
                resource_id=str(tool.id),
                details={
                    "source_version_id": str(version.id),
                    "source_version": version.version,
                    "draft_version": draft.version,
                },
            )
            return draft

        if version.status != "published":
            published = await self.publish(version.id, tool)
            if published is None:
                raise ValueError("Unable to publish tool version")
            return published

        tool.published_version_id = version.id
        self._apply_version_to_tool(tool, version, version_status="published")
        await self.session.flush()
        await self.session.refresh(tool)
        await self.audit(
            action="tool.restore",
            resource_type="tool_definition",
            resource_id=str(tool.id),
            details={"version_id": str(version.id), "version": version.version},
        )
        return version


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

    async def update_base(
        self,
        knowledge_base_id: uuid.UUID,
        *,
        name: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> KnowledgeBase | None:
        base = await self.get_base(knowledge_base_id)
        if base is None:
            return None
        if name is not None:
            base.name = name
        if config is not None:
            base.config = config
        await self.session.flush()
        return base

    async def delete_base(self, knowledge_base_id: uuid.UUID) -> list[str] | None:
        """Delete a knowledge base and its sources/chunks. Returns source URIs to unlink."""
        base = await self.get_base(knowledge_base_id)
        if base is None:
            return None
        sources = await self.list_sources(knowledge_base_id)
        uris = [source.uri for source in sources]
        await self.session.execute(
            delete(KnowledgeChunk).where(
                KnowledgeChunk.tenant_id == self.context.tenant_id,
                KnowledgeChunk.knowledge_base_id == knowledge_base_id,
            )
        )
        await self.session.execute(
            delete(KnowledgeSource).where(
                KnowledgeSource.tenant_id == self.context.tenant_id,
                KnowledgeSource.knowledge_base_id == knowledge_base_id,
            )
        )
        await self.session.execute(
            delete(KnowledgeBase).where(
                KnowledgeBase.id == knowledge_base_id,
                KnowledgeBase.tenant_id == self.context.tenant_id,
            )
        )
        await self.audit(
            action="knowledge.delete",
            resource_type="knowledge_base",
            resource_id=str(knowledge_base_id),
        )
        return uris

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
        limit: int | None = None,
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
        statement = statement.order_by(ConversationSession.updated_at.desc())
        if limit is not None:
            statement = statement.limit(max(1, min(limit, 500)))
        rows = await self.session.scalars(
            self.scoped(
                statement,
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

    async def bind_verified_end_user(
        self,
        *,
        external_session_id: str,
        end_user_id: uuid.UUID,
        guest_user_id: str,
    ) -> ConversationSession | None:
        row = await self.get_by_external(external_session_id)
        if row is None:
            return None
        if row.user_id != guest_user_id:
            raise PermissionError("Session belongs to another guest")
        row.verified_end_user_id = end_user_id
        await self.session.flush()
        return row


class EndUserSessionBindRepository(TenantRepository):
    async def upsert(
        self,
        *,
        external_session_id: str,
        guest_user_id: str,
        end_user_id: uuid.UUID,
    ) -> EndUserSessionBind:
        existing = await self.session.scalar(
            self.scoped(
                select(EndUserSessionBind).where(
                    EndUserSessionBind.external_session_id == external_session_id
                ),
                EndUserSessionBind,
            )
        )
        if existing is not None:
            if existing.guest_user_id != guest_user_id:
                raise PermissionError("Session bind belongs to another guest")
            existing.end_user_id = end_user_id
            await self.session.flush()
            return existing
        row = EndUserSessionBind(
            id=new_id(),
            tenant_id=self.context.tenant_id,
            external_session_id=external_session_id,
            guest_user_id=guest_user_id,
            end_user_id=end_user_id,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def get_for_session(
        self, *, external_session_id: str, guest_user_id: str
    ) -> EndUserSessionBind | None:
        row = await self.session.scalar(
            self.scoped(
                select(EndUserSessionBind).where(
                    EndUserSessionBind.external_session_id == external_session_id
                ),
                EndUserSessionBind,
            )
        )
        if row is None:
            return None
        if row.guest_user_id != guest_user_id:
            return None
        return row


class EndUserRepository(TenantRepository):
    async def get(self, end_user_id: uuid.UUID) -> EndUser | None:
        return await self.session.scalar(
            self.scoped(select(EndUser).where(EndUser.id == end_user_id), EndUser)
        )

    async def get_by_email(self, email: str) -> EndUser | None:
        normalized = email.strip().lower()
        return await self.session.scalar(
            self.scoped(select(EndUser).where(EndUser.email == normalized), EndUser)
        )

    async def list(self, *, limit: int = 100) -> Sequence[EndUser]:
        return (
            await self.session.scalars(
                self.scoped(
                    select(EndUser).order_by(EndUser.created_at.desc()).limit(limit),
                    EndUser,
                )
            )
        ).all()

    async def get_or_create(
        self,
        *,
        email: str,
        display_name: str = "",
        mark_verified: bool = False,
    ) -> EndUser:
        normalized = email.strip().lower()
        existing = await self.get_by_email(normalized)
        if existing is not None:
            if mark_verified and existing.email_verified_at is None:
                existing.email_verified_at = datetime.now(UTC)
            if display_name and not existing.display_name:
                existing.display_name = display_name.strip()
            return existing
        row = EndUser(
            id=new_id(),
            tenant_id=self.context.tenant_id,
            email=normalized,
            display_name=display_name.strip(),
            email_verified_at=datetime.now(UTC) if mark_verified else None,
            is_active=True,
            user_metadata={},
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def update_profile(
        self,
        end_user_id: uuid.UUID,
        *,
        display_name: str | None = None,
        metadata_patch: dict[str, Any] | None = None,
        is_active: bool | None = None,
        allow_inactive: bool = False,
    ) -> EndUser | None:
        row = await self.get(end_user_id)
        if row is None:
            return None
        if not row.is_active and not allow_inactive and is_active is not True:
            return None
        if display_name is not None:
            row.display_name = display_name.strip()[:255]
        if metadata_patch is not None:
            merged = dict(row.user_metadata or {})
            merged.update(metadata_patch)
            row.user_metadata = merged
        if is_active is not None:
            row.is_active = is_active
        await self.session.flush()
        return row


class VerificationChallengeRepository(TenantRepository):
    async def create_challenge(
        self,
        *,
        email: str,
        code_hash: str,
        external_session_id: str,
        guest_user_id: str,
        ttl_minutes: int = 15,
    ) -> VerificationChallenge:
        # Invalidate prior open challenges for this session+email.
        await self.session.execute(
            update(VerificationChallenge)
            .where(
                VerificationChallenge.tenant_id == self.context.tenant_id,
                VerificationChallenge.external_session_id == external_session_id,
                VerificationChallenge.email == email.strip().lower(),
                VerificationChallenge.consumed_at.is_(None),
            )
            .values(consumed_at=datetime.now(UTC))
        )
        row = VerificationChallenge(
            id=new_id(),
            tenant_id=self.context.tenant_id,
            email=email.strip().lower(),
            code_hash=code_hash,
            purpose="bind_session",
            external_session_id=external_session_id,
            guest_user_id=guest_user_id,
            attempt_count=0,
            expires_at=datetime.now(UTC) + timedelta(minutes=ttl_minutes),
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def get_open(
        self, *, email: str, external_session_id: str
    ) -> VerificationChallenge | None:
        return await self.session.scalar(
            self.scoped(
                select(VerificationChallenge)
                .where(
                    VerificationChallenge.email == email.strip().lower(),
                    VerificationChallenge.external_session_id == external_session_id,
                    VerificationChallenge.consumed_at.is_(None),
                )
                .order_by(VerificationChallenge.created_at.desc()),
                VerificationChallenge,
            )
        )
