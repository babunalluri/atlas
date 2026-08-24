"""Tenant-aware Agno Agent/Team factories.

Preferred production path: register these callables with Agno `AgentFactory` /
`TeamFactory` so AgentOS rebuilds a fresh component per request from verified
JWT claims. The helpers below also support a thin custom run wrapper used when
running without the full AgentOS control plane extras.
"""

from __future__ import annotations

import inspect
import logging
import os
import uuid
from dataclasses import dataclass
from typing import Any

from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.persistence import get_agno_db, runtime_session_id, runtime_user_id
from app.core.settings import get_settings
from app.credentials.provider import AwsKmsCipher, EncryptedEnvelope, LocalFernetCipher
from app.db.models import AgentToolBinding, AgentVersion, TeamToolBinding
from app.db.repositories import (
    AgentRepository,
    CredentialRepository,
    SessionRepository,
    TeamRepository,
    ToolDefinitionRepository,
    ToolDefinitionVersionRepository,
    WorkflowRepository,
)
from app.knowledge.embeddings import EmbeddingService
from app.knowledge import build_tenant_knowledge_store
from app.knowledge.store import MemoryConfig
from app.observability.tracing import trace_metadata
from app.tenancy.context import TenantContext, current_tenant
from app.tools.providers import (
    PROVIDERS,
    ProviderBuildContext,
    ProviderValidationError,
    legacy_http_config,
)
from app.tools.registry import (
    SafeRestClient,
    build_mutating_rest_tool,
    build_read_rest_tool,
    web_search,
)

try:
    from agno.agent import Agent
    from agno.team import Team
except ImportError:  # pragma: no cover
    Agent = None  # type: ignore[assignment,misc]
    Team = None  # type: ignore[assignment,misc]

# Import each model provider independently so a single missing optional
# client library does not disable the other providers.
try:
    from agno.models.openai import OpenAIChat
except ImportError:  # pragma: no cover
    OpenAIChat = None  # type: ignore[assignment,misc]

try:
    from agno.models.anthropic import Claude
except ImportError:  # pragma: no cover
    Claude = None  # type: ignore[assignment,misc]

try:
    from agno.models.groq import Groq
except ImportError:  # pragma: no cover
    Groq = None  # type: ignore[assignment,misc]

try:
    from agno.models.moonshot import MoonShot
except ImportError:  # pragma: no cover
    MoonShot = None  # type: ignore[assignment,misc]

try:
    from agno.models.nvidia import Nvidia
except ImportError:  # pragma: no cover
    Nvidia = None  # type: ignore[assignment,misc]

try:
    from agno.models.google import Gemini
except ImportError:  # pragma: no cover
    Gemini = None  # type: ignore[assignment,misc]

try:
    from agno.factory import RequestContext
except ImportError:  # pragma: no cover
    RequestContext = Any  # type: ignore[misc,assignment]


ALLOWED_MODELS = {
    "openai:gpt-4.1-mini": "gpt-4.1-mini",
    "openai:gpt-4.1": "gpt-4.1",
    "openai:gpt-5-mini": "gpt-5-mini",
    "anthropic:claude-sonnet-4": "claude-sonnet-4-20250514",
    "anthropic:claude-haiku": "claude-3-5-haiku-20241022",
    "groq:llama-3.3-70b": "llama-3.3-70b-versatile",
    "groq:llama-3.1-8b": "llama-3.1-8b-instant",
    "groq:gpt-oss-120b": "openai/gpt-oss-120b",
    "moonshot:kimi-k2.5": "kimi-k2.5",
    "moonshot:kimi-k2": "kimi-k2",
    "moonshot:kimi-latest": "kimi-latest",
    "nvidia:nvidia-llama-3.3-70b": "meta/llama-3.3-70b-instruct",
    "nvidia:nvidia-llama-3.1-8b": "meta/llama-3.1-8b-instruct",
    "nvidia:nvidia-nemotron-70b": "nvidia/llama-3.1-nemotron-70b-instruct",
    "gemini:gemini-2.5-flash": "gemini-2.5-flash",
    "gemini:gemini-2.5-pro": "gemini-2.5-pro",
    "gemini:gemini-2.0-flash": "gemini-2.0-flash",
}

logger = logging.getLogger(__name__)


class McpToolSkipped(Exception):
    """MCP tool cannot be built at run start; skip instead of failing the run."""

    def __init__(self, message: str, *, name: str, slug: str) -> None:
        super().__init__(message)
        self.name = name
        self.slug = slug

    def user_message(self) -> str:
        return (
            f"Skipped MCP tool '{self.name}' ({self.slug}): {self.args[0]}. "
            "Detach it from this team or keep only a published Python groww_toolkit."
        )


def _with_skipped_mcp_note(instructions: str, skipped: list[str]) -> str:
    if not skipped:
        return instructions
    return "[Atlas] " + " ".join(skipped) + "\n\n" + instructions


@dataclass(frozen=True, slots=True)
class RuntimeRequest:
    version_id: uuid.UUID
    session_id: str
    preview: bool = False
    knowledge_base_id: uuid.UUID | None = None
    pin_session: bool = True


@dataclass(frozen=True, slots=True)
class TeamRuntimeRequest:
    version_id: uuid.UUID
    session_id: str
    preview: bool = False
    pin_session: bool = True


@dataclass(frozen=True, slots=True)
class WorkflowRuntimeRequest:
    version_id: uuid.UUID
    session_id: str
    preview: bool = False


def _supported_kwargs(callable_: Any, values: dict[str, Any]) -> dict[str, Any]:
    signature = inspect.signature(callable_)
    if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in signature.parameters.values()):
        return values
    return {key: value for key, value in values.items() if key in signature.parameters}


def _guardrails_from_team_config(team_config: dict[str, Any] | None) -> list[Any]:
    """Build Agno pre_hooks from AgentVersion.team_config.guardrails JSON."""
    raw = (team_config or {}).get("guardrails") if team_config else None
    if not isinstance(raw, dict):
        return []
    hooks: list[Any] = []
    try:
        if raw.get("prompt_injection"):
            from agno.guardrails import PromptInjectionGuardrail

            hooks.append(PromptInjectionGuardrail())
        if raw.get("pii_detection"):
            from agno.guardrails import PIIDetectionGuardrail

            hooks.append(PIIDetectionGuardrail())
        if raw.get("openai_moderation"):
            from agno.guardrails import OpenAIModerationGuardrail

            hooks.append(OpenAIModerationGuardrail())
    except ImportError:
        return hooks
    return hooks


def normalize_guardrails(value: dict[str, Any] | None) -> dict[str, bool]:
    value = value or {}
    return {
        "prompt_injection": bool(value.get("prompt_injection")),
        "pii_detection": bool(value.get("pii_detection")),
        "openai_moderation": bool(value.get("openai_moderation")),
    }


def _extract_factory_input(ctx: Any) -> dict[str, Any]:
    trusted = getattr(ctx, "trusted", None)
    claims = getattr(trusted, "claims", None) or getattr(ctx, "claims", None) or {}
    client_input = getattr(ctx, "input", None) or {}
    if hasattr(client_input, "model_dump"):
        client_input = client_input.model_dump()
    return {
        "claims": claims if isinstance(claims, dict) else {},
        "input": client_input if isinstance(client_input, dict) else {},
        "user_id": getattr(ctx, "user_id", None),
        "session_id": getattr(ctx, "session_id", None),
    }


class AgentFactoryService:
    def __init__(
        self,
        session: AsyncSession,
        context: TenantContext,
        allowed_hosts: set[str] | None = None,
    ) -> None:
        self.repo = AgentRepository(session, context)
        self.credentials = CredentialRepository(session, context)
        self.tool_definitions = ToolDefinitionRepository(session, context)
        self.tool_versions = ToolDefinitionVersionRepository(session, context)
        self.sessions = SessionRepository(session, context)
        self.knowledge = build_tenant_knowledge_store(session, context)
        self.context = context
        self.session = session
        self._user_vault: dict[str, str] | None = None
        settings = get_settings()
        self.rest_client = SafeRestClient(allowed_hosts or settings.allowed_outbound_hosts)

    async def _user_vault_map(self) -> dict[str, str]:
        if self._user_vault is None:
            from app.vault import aload_user_vault_map

            self._user_vault = await aload_user_vault_map(self.session, self.context)
        return self._user_vault

    async def _attach_session_state(self, component: Any) -> None:
        from app.vault import session_state_for_user

        vault = await self._user_vault_map()
        component._saas_session_state = session_state_for_user(self.context, vault)

    async def create(self, request: RuntimeRequest) -> Any:
        allow_draft = request.preview and self.context.can_administer()
        version = await self.repo.get_version(request.version_id, allow_draft=allow_draft)
        if version is None:
            raise LookupError("Agent version not found for tenant")
        if Agent is None or OpenAIChat is None:
            raise RuntimeError("Agno runtime is unavailable; install the 'agno' dependency")

        if request.pin_session:
            await self.sessions.pin(
                external_session_id=request.session_id,
                agent_config_id=version.agent_config_id,
                agent_version_id=version.id,
                runtime_session_id=runtime_session_id(self.context, request.session_id),
                runtime_user_id=runtime_user_id(self.context),
            )

        adapter = str((version.team_config or {}).get("framework_adapter") or "agno")
        if adapter != "agno":
            return await self._create_framework_adapter(version, request, adapter)

        bindings = await self.repo.bindings(version.id)
        tools, skipped_mcp = await self._runtime_tools(bindings)
        model = await self._build_model(version)

        memory = MemoryConfig(mode=version.memory_mode)  # type: ignore[arg-type]
        kb_id = request.knowledge_base_id
        if kb_id is None and version.team_config:
            raw = version.team_config.get("knowledge_base_id")
            kb_id = uuid.UUID(raw) if raw else None

        knowledge_retriever = await self._knowledge_retriever(kb_id) if kb_id else None
        pre_hooks = _guardrails_from_team_config(version.team_config)

        values = {
            "id": str(version.agent_config_id),
            "name": f"tenant-{self.context.tenant_id}-agent-{version.agent_config_id}",
            "model": model,
            "instructions": _with_skipped_mcp_note(version.instructions, skipped_mcp),
            "tools": tools,
            "db": get_agno_db(),
            "markdown": True,
            "add_datetime_to_context": True,
            "add_history_to_context": memory.mode != "none",
            "num_history_runs": memory.max_messages // 2,
            "enable_agentic_memory": memory.persistent,
            "enable_user_memories": memory.persistent,
            "add_memories_to_context": memory.persistent,
            "update_memory_on_run": memory.persistent,
            "cache_session": True,
            "store_events": True,
            "stream_events": True,
            "knowledge_retriever": knowledge_retriever,
            "add_knowledge_to_context": knowledge_retriever is not None,
            "search_knowledge": False,
            "metadata": self._metadata(version, request),
            "debug_mode": False,
            "pre_hooks": pre_hooks or None,
        }
        agent = Agent(**_supported_kwargs(Agent, values))
        if hasattr(agent, "initialize_agent"):
            agent.initialize_agent()
        agent._saas_metadata = values["metadata"]
        agent._saas_memory_mode = version.memory_mode
        agent._saas_guardrails = (version.team_config or {}).get("guardrails") or {}
        await self._attach_session_state(agent)
        return agent

    async def _knowledge_retriever(self, knowledge_base_id: uuid.UUID) -> Any:
        settings = get_settings()
        api_key = settings.openai_api_key.get_secret_value() or None
        tenant_credential = await self.credentials.get_for_provider("openai")
        if tenant_credential is not None:
            api_key = self._decrypt(
                tenant_credential.encrypted_value,
                tenant_credential.key_version,
            )
        embedder = EmbeddingService(
            api_key=api_key,
            model=settings.embedding_model,
            dimensions=settings.embedding_dimensions,
        )
        context = self.context

        async def retrieve(
            query: str,
            num_documents: int | None = None,
            **_: Any,
        ) -> list[dict[str, Any]]:
            from sqlalchemy import text

            from app.db.session import SessionFactory

            vector = (await embedder.embed([query]))[0]
            async with SessionFactory() as session:
                if session.bind and session.bind.dialect.name == "postgresql":
                    await session.execute(
                        text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                        {"tenant_id": str(context.tenant_id)},
                    )
                session.info["tenant_id"] = context.tenant_id
                return await build_tenant_knowledge_store(
                    session, context, settings=settings
                ).search(
                    knowledge_base_id,
                    query,
                    vector,
                    top_k=min(num_documents or settings.knowledge_top_k, settings.knowledge_top_k),
                    score_threshold=settings.knowledge_score_threshold,
                    max_context_chars=settings.max_knowledge_context_chars,
                )

        return retrieve

    async def _create_framework_adapter(
        self,
        version: AgentVersion,
        request: RuntimeRequest,
        adapter: str,
    ) -> Any:
        """Build a non-Agno runtime via Agno's adapter classes, or fail with ValueError."""
        adapter_cfg = dict((version.team_config or {}).get("adapter_config") or {})
        common = {
            "id": str(version.agent_config_id),
            "name": f"tenant-{self.context.tenant_id}-agent-{version.agent_config_id}",
            "description": version.instructions[:240],
            "db": get_agno_db(),
            "markdown": True,
        }
        try:
            if adapter == "claude_agent_sdk":
                from agno.agents.claude.agent import ClaudeAgent

                model_name = ALLOWED_MODELS.get(version.model_id)
                agent = ClaudeAgent(
                    **_supported_kwargs(
                        ClaudeAgent,
                        {
                            **common,
                            "system_prompt": version.instructions,
                            "model": model_name,
                            **adapter_cfg,
                        },
                    )
                )
            elif adapter == "antigravity":
                from agno.agents.antigravity.agent import AntigravityAgent

                agent = AntigravityAgent(
                    **_supported_kwargs(
                        AntigravityAgent,
                        {
                            **common,
                            "custom_agent_instructions": version.instructions,
                            "custom_agent_name": common["name"],
                            **adapter_cfg,
                        },
                    )
                )
            elif adapter == "langgraph":
                from agno.agents.langgraph.agent import LangGraphAgent

                if adapter_cfg.get("graph") is None:
                    raise ValueError(
                        "langgraph adapter requires adapter_config.graph "
                        "(a compiled LangGraph); native Agno runtime is preferred"
                    )
                agent = LangGraphAgent(
                    **_supported_kwargs(LangGraphAgent, {**common, **adapter_cfg})
                )
            elif adapter == "dspy":
                from agno.agents.dspy.agent import DSPyAgent

                if adapter_cfg.get("program") is None:
                    raise ValueError(
                        "dspy adapter requires adapter_config.program; "
                        "native Agno runtime is preferred"
                    )
                agent = DSPyAgent(
                    **_supported_kwargs(DSPyAgent, {**common, **adapter_cfg})
                )
            else:
                raise ValueError(f"Unknown framework_adapter: {adapter}")
        except ImportError as exc:
            raise ValueError(
                f"framework_adapter '{adapter}' is unavailable "
                f"(missing dependency: {exc})"
            ) from exc

        agent._saas_metadata = self._metadata(version, request)
        agent._saas_metadata["framework_adapter"] = adapter
        agent._saas_memory_mode = version.memory_mode
        await self._attach_session_state(agent)
        return agent

    async def _build_model(self, version: AgentVersion) -> Any:
        model_name = ALLOWED_MODELS.get(version.model_id)
        if model_name is None:
            raise ValueError(f"Model is not allowlisted: {version.model_id}")

        settings = get_settings()
        provider = version.model_id.split(":", 1)[0]
        providers: dict[str, tuple[Any, SecretStr]] = {
            "openai": (OpenAIChat, settings.openai_api_key),
            "anthropic": (Claude, settings.anthropic_api_key),
            "groq": (Groq, settings.groq_api_key),
            "moonshot": (MoonShot, settings.moonshot_api_key),
            "nvidia": (Nvidia, settings.nvidia_api_key),
            "gemini": (Gemini, settings.gemini_api_key),
        }
        if provider not in providers:
            raise ValueError(f"Model provider is not supported: {provider}")
        model_type, platform_secret = providers[provider]
        if model_type is None:
            raise RuntimeError(f"{provider} model support is unavailable")

        env_keys = {
            "openai": "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "groq": "GROQ_API_KEY",
            "moonshot": "MOONSHOT_API_KEY",
            "nvidia": "NVIDIA_API_KEY",
            "gemini": "GOOGLE_API_KEY",
        }
        api_key = platform_secret.get_secret_value() or os.getenv(env_keys[provider])

        # Tenant-provided credentials (BYOK) take precedence over platform keys.
        tenant_model_credential = await self.credentials.get_for_provider(provider)
        if tenant_model_credential is not None:
            api_key = self._decrypt(
                tenant_model_credential.encrypted_value,
                tenant_model_credential.key_version,
            )

        return model_type(
            **_supported_kwargs(
                model_type,
                {
                    "id": model_name,
                    "temperature": version.temperature,
                    "api_key": api_key or None,
                },
            )
        )

    async def _runtime_tools(
        self, bindings: list[AgentToolBinding] | list[TeamToolBinding]
    ) -> tuple[list[Any], list[str]]:
        tools: list[Any] = []
        skipped: list[str] = []
        for binding in bindings:
            try:
                built = await self._build_tool(binding)
            except McpToolSkipped as exc:
                logger.warning("Skipping MCP tool at run start: %s", exc.user_message())
                skipped.append(exc.user_message())
                continue
            tools.extend(built if isinstance(built, list) else [built])
        return tools, skipped

    async def _build_tool(self, binding: AgentToolBinding | TeamToolBinding) -> Any:
        if binding.tool_definition_id is not None:
            definition = await self.tool_definitions.get(binding.tool_definition_id)
            if definition is None or not definition.active:
                raise ValueError("Active tool definition was not found for tenant")
            provider_key = "http" if definition.kind in {"rest", "webhook"} else definition.kind
            provider = PROVIDERS.get(provider_key)
            if provider is None:
                raise ValueError(f"Tool provider is not supported: {provider_key}")
            config = (
                legacy_http_config(
                    {
                        "base_url": definition.base_url,
                        "http_method": definition.http_method,
                        "path": definition.path,
                        "request_schema": definition.request_schema,
                        "response_description": definition.response_description,
                        "response_schema": definition.response_schema,
                        "headers": definition.headers,
                        "config": definition.config,
                    }
                )
                if definition.kind in {"rest", "webhook"}
                else definition.config
            )
            if provider_key == "tenant_python":
                if definition.published_version_id is None:
                    raise ValueError("Editable Python tool has no published version")
                published = await self.tool_versions.get(definition.published_version_id)
                if published is None or published.status != "published":
                    raise ValueError("Editable Python published version was not found")
                config = {
                    "source_code": published.source_code,
                    "dependencies": published.dependencies,
                    "capabilities": published.capabilities,
                    "settings": published.settings,
                    "version_status": "published",
                }
            parsed = provider.validate_config(config)
            config = parsed.model_dump(mode="json", exclude_none=True)
            headers: dict[str, str] = {}
            credential_provider: str | None = None
            credential_value: str | None = None
            cred_id = definition.credential_id or getattr(binding, "credential_id", None)
            if cred_id is not None:
                credential = await self.credentials.get(cred_id)
                if credential is None:
                    raise ValueError("Tool credential was not found for tenant")
                credential_provider = credential.provider
                credential_value = self._decrypt(
                    credential.encrypted_value,
                    credential.key_version,
                )
                if provider_key not in {"python_toolkit", "custom_python", "tenant_python"}:
                    header_name = str(config.get("credential_header", "Authorization"))
                    prefix = str(config.get("credential_prefix", "Bearer "))
                    headers[header_name] = prefix + credential_value
            timeout = binding.config.get("timeout_seconds", config.get("timeout_seconds", 10))
            client = SafeRestClient(
                self.rest_client.allowed_hosts,
                timeout_seconds=float(timeout),
            )
            try:
                built = await provider.build_tools(
                    config,
                    ProviderBuildContext(
                        client=client,
                        prefix=definition.slug.replace("-", "_"),
                        headers=headers,
                        approval_required=definition.approval_required,
                        credential_provider=credential_provider,
                        credential_value=credential_value,
                        tenant_id=str(self.context.tenant_id),
                        user_vault=await self._user_vault_map()
                        if provider_key == "tenant_python"
                        else None,
                    ),
                )
            except ProviderValidationError as exc:
                if provider_key == "mcp":
                    raise McpToolSkipped(
                        str(exc), name=definition.name, slug=definition.slug
                    ) from exc
                raise
            return built[0] if len(built) == 1 else built
        if binding.tool_key == "web_search":
            return web_search
        base_url = str(binding.config.get("base_url", ""))
        headers: dict[str, str] = {}
        if binding.credential_id is not None:
            credential = await self.credentials.get(binding.credential_id)
            if credential is None:
                raise ValueError("Tool credential was not found for tenant")
            header_name = str(binding.config.get("credential_header", "Authorization"))
            prefix = str(binding.config.get("credential_prefix", "Bearer "))
            headers[header_name] = prefix + self._decrypt(
                credential.encrypted_value,
                credential.key_version,
            )
        if binding.tool_key == "rest_read":
            return build_read_rest_tool(self.rest_client, base_url, headers)
        if binding.tool_key == "rest_mutate":
            return build_mutating_rest_tool(self.rest_client, base_url, headers)
        raise ValueError(f"Tool is not allowlisted: {binding.tool_key}")

    @staticmethod
    def _decrypt(ciphertext: str, key_version: str) -> str:
        settings = get_settings()
        envelope = EncryptedEnvelope(ciphertext, key_version)
        if key_version.startswith("kms-") or settings.aws_kms_key_id:
            cipher = AwsKmsCipher(
                settings.aws_kms_key_id or "",
                settings.aws_region,
                key_version if key_version.startswith("kms-") else "kms-v1",
            )
        else:
            # Current + previous keys; envelope.key_version selects which Fernet.
            cipher = LocalFernetCipher(
                settings.encryption_key.get_secret_value(),
                settings.encryption_key_version,
                previous_keys=settings.encryption_previous_keys,
            )
        return cipher.decrypt(envelope)

    def _metadata(self, version: AgentVersion, request: RuntimeRequest) -> dict[str, str]:
        return trace_metadata(
            tenant_id=str(self.context.tenant_id),
            agent_id=str(version.agent_config_id),
            version_id=str(version.id),
            session_id=request.session_id,
        ) | {"user_id": self.context.user_id}


class TeamFactoryService:
    def __init__(self, agent_factory: AgentFactoryService) -> None:
        self.agent_factory = agent_factory
        self.repo = TeamRepository(agent_factory.repo.session, agent_factory.context)

    async def create(self, request: TeamRuntimeRequest) -> Any:
        if Team is None:
            raise RuntimeError("Agno Team runtime is unavailable")
        allow_draft = request.preview and self.agent_factory.context.can_administer()
        version = await self.repo.get_version(request.version_id, allow_draft=allow_draft)
        if version is None:
            raise LookupError("Team version not found for tenant")
        # Empty members is valid: Agno Team can run leader-only with tools.
        member_rows = await self.repo.members(version.id)
        members = [
            await self.agent_factory.create(
                RuntimeRequest(
                    version_id=member.agent_version_id,
                    session_id=request.session_id,
                    preview=request.preview,
                    pin_session=False,
                )
            )
            for member in member_rows
        ]
        if request.pin_session:
            await self.agent_factory.sessions.pin_team(
                external_session_id=request.session_id,
                team_config_id=version.team_config_id,
                team_version_id=version.id,
                runtime_session_id=runtime_session_id(
                    self.agent_factory.context, request.session_id
                ),
                runtime_user_id=runtime_user_id(self.agent_factory.context),
            )
        model = await self.agent_factory._build_model(version)  # type: ignore[arg-type]
        bindings = await self.repo.bindings(version.id)
        tools, skipped_mcp = await self.agent_factory._runtime_tools(bindings)
        metadata = trace_metadata(
            tenant_id=str(self.agent_factory.context.tenant_id),
            agent_id=f"team:{version.team_config_id}",
            version_id=str(version.id),
            session_id=request.session_id,
        ) | {
            "user_id": self.agent_factory.context.user_id,
            "team_id": str(version.team_config_id),
            "team_version_id": str(version.id),
            "team_mode": version.mode,
            "skipped_tools": skipped_mcp,
        }
        values = {
            "id": str(version.team_config_id),
            "name": f"tenant-{self.agent_factory.context.tenant_id}-team-{version.team_config_id}",
            "members": members,
            "instructions": _with_skipped_mcp_note(version.instructions, skipped_mcp),
            "mode": version.mode,
            "model": model,
            "tools": tools,
            "db": get_agno_db(),
            "metadata": metadata,
            "add_history_to_context": True,
            "cache_session": True,
            "store_events": True,
            "stream_events": True,
            "debug_mode": False,
        }
        team = Team(**_supported_kwargs(Team, values))
        if hasattr(team, "initialize_team"):
            team.initialize_team()
        team._saas_metadata = metadata
        await self.agent_factory._attach_session_state(team)
        return team


class WorkflowFactoryService:
    def __init__(self, agent_factory: AgentFactoryService) -> None:
        self.agent_factory = agent_factory
        self.repo = WorkflowRepository(agent_factory.repo.session, agent_factory.context)

    async def create(self, request: WorkflowRuntimeRequest) -> Any:
        try:
            from agno.workflow import Workflow
            from agno.workflow.parallel import Parallel
            from agno.workflow.step import Step
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("Agno Workflow runtime is unavailable") from exc

        allow_draft = request.preview and self.agent_factory.context.can_administer()
        version = await self.repo.get_version(request.version_id, allow_draft=allow_draft)
        if version is None:
            raise LookupError("Workflow version not found for tenant")
        step_rows = await self.repo.steps(version.id)
        if not step_rows:
            raise ValueError("Workflow has no steps")

        try:
            from agno.workflow.condition import Condition
        except ImportError:  # pragma: no cover
            Condition = None  # type: ignore[misc, assignment]

        built_steps: list[Any] = []
        team_factory = TeamFactoryService(self.agent_factory)
        for row in step_rows:
            if row.target_type == "agent":
                executor = await self.agent_factory.create(
                    RuntimeRequest(
                        version_id=row.agent_version_id,  # type: ignore[arg-type]
                        session_id=request.session_id,
                        preview=request.preview,
                        pin_session=False,
                    )
                )
                step = Step(
                    name=row.name,
                    step_id=str(row.id),
                    agent=executor,
                    description=f"Tenant agent step {row.position + 1}",
                )
            else:
                executor = await team_factory.create(
                    TeamRuntimeRequest(
                        version_id=row.team_version_id,  # type: ignore[arg-type]
                        session_id=request.session_id,
                        preview=request.preview,
                        pin_session=False,
                    )
                )
                step = Step(
                    name=row.name,
                    step_id=str(row.id),
                    team=executor,
                    description=f"Tenant team step {row.position + 1}",
                )
            if row.condition_expression:
                if Condition is None:  # pragma: no cover
                    raise RuntimeError("Agno Condition runtime is unavailable")
                built_steps.append(
                    Condition(
                        steps=[step],
                        evaluator=row.condition_expression,
                        name=row.name,
                        description=f"Condition for step {row.position + 1}",
                    )
                )
            else:
                built_steps.append(step)

        await self.agent_factory.sessions.pin_workflow(
            external_session_id=request.session_id,
            workflow_config_id=version.workflow_config_id,
            workflow_version_id=version.id,
            runtime_session_id=runtime_session_id(self.agent_factory.context, request.session_id),
            runtime_user_id=runtime_user_id(self.agent_factory.context),
        )
        metadata = trace_metadata(
            tenant_id=str(self.agent_factory.context.tenant_id),
            agent_id=f"workflow:{version.workflow_config_id}",
            version_id=str(version.id),
            session_id=request.session_id,
        ) | {
            "user_id": self.agent_factory.context.user_id,
            "workflow_id": str(version.workflow_config_id),
            "workflow_version_id": str(version.id),
            "workflow_mode": version.mode,
        }
        workflow_steps: Any = (
            Parallel(built_steps, name="Parallel workflow steps")
            if version.mode == "parallel"
            else built_steps
        )
        workflow = Workflow(
            id=str(version.workflow_config_id),
            name=f"tenant-{self.agent_factory.context.tenant_id}-workflow-{version.workflow_config_id}",
            db=get_agno_db(),
            steps=workflow_steps,
            session_id=runtime_session_id(self.agent_factory.context, request.session_id),
            user_id=runtime_user_id(self.agent_factory.context),
            metadata=metadata,
            stream_events=True,
            store_events=True,
            cache_session=True,
            telemetry=False,
        )
        workflow.initialize_workflow()
        workflow._saas_metadata = metadata
        await self.agent_factory._attach_session_state(workflow)
        return workflow


# Backwards-compatible aliases used by earlier scaffold files/tests.
AgentFactory = AgentFactoryService
TeamFactory = TeamFactoryService
WorkflowFactory = WorkflowFactoryService


async def build_tenant_agent_from_request(ctx: Any, session: AsyncSession) -> Any:
    """Agno AgentFactory-compatible callable."""
    extracted = _extract_factory_input(ctx)
    claims = extracted["claims"]
    client_input = extracted["input"]

    # Never trust tenant_id from client input — only from middleware context / claims.
    context = current_tenant()
    claim_org = claims.get("org_id")
    if claim_org and claim_org != context.auth_org_id and context.auth_org_id != "dev":
        raise PermissionError("Tenant claim mismatch")

    version_raw = client_input.get("version_id") or client_input.get("agent_version_id")
    if not version_raw:
        raise ValueError("factory_input.version_id is required")
    session_id = extracted["session_id"] or client_input.get("session_id") or str(uuid.uuid4())
    preview = bool(client_input.get("preview", False))

    service = AgentFactoryService(session, context)
    return await service.create(
        RuntimeRequest(
            version_id=uuid.UUID(str(version_raw)),
            session_id=str(session_id),
            preview=preview,
        )
    )
