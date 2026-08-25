import {
  MOCK_AGENT_DETAIL,
  MOCK_AGENTS,
  MOCK_APPROVALS,
  MOCK_INGESTION,
} from "./mocks";
import type {
  AgentConfig,
  AgentDraftInput,
  AgentSummary,
  AvailableWorkflow,
  AvailableTeam,
  AdminSession,
  ApprovalRequest,
  CatalogPage,
  ChatMessage,
  KnowledgeBaseSummary,
  KnowledgeSearchResult,
  KnowledgeSource,
  PlatformAuditEvent,
  PlatformTenant,
  PublicTeamSurface,
  PublicWorkflowSurface,
  TeamConfig,
  TeamDraftInput,
  TeamSummary,
  TeamVersionDetail,
  TeamVersionSummary,
  TenantBranding,
  TenantUser,
  TenantUserInput,
  EndCustomer,
  NotificationAudience,
  NotificationBatch,
  NotificationSendResult,
  UserNotification,
  BillingPlan,
  BillingWallet,
  BillingLedgerEntry,
  PlatformTenantWallet,
  ToolBinding,
  ToolCapability,
  ToolDefinition,
  ToolValidation,
  UserMemory,
  WorkflowConfig,
  WorkflowAssignments,
  WorkflowDraftInput,
  WorkflowSummary,
  WorkspaceDomain,
  WorkspaceDomainOption,
} from "./types";
import { TOOL_CATALOG, toBackendModelId } from "./types";
import {
  buildPublicApiRunCatalog,
  teamStepsFromPublished,
  type PublicApiCatalogLoad,
  type PublicApiTeamOption,
} from "@/lib/api/public-api-catalog";
import { agentOsUrl, apiFetch } from "@/lib/agentos/client";
import { unpackAccessContext } from "@/lib/auth/access-context";
import { devTenantHeaders } from "@/lib/auth/token";

interface BackendVersion {
  id: string;
  version: number;
  status: string;
  instructions: string;
  model_id: string;
  temperature: number;
  memory_mode: string;
  created_at: string;
}

interface BackendAgent {
  id: string;
  name: string;
  slug: string;
  description: string | null;
  published_version_id: string | null;
  updated_at: string;
  tools: Array<{
    tool_key: ToolBinding["kind"] | null;
    tool_definition_id?: string | null;
    config: Record<string, unknown>;
    credential_id?: string | null;
  }>;
  knowledge_base_id: string | null;
  framework_adapter?: string | null;
  guardrails?: {
    prompt_injection?: boolean;
    pii_detection?: boolean;
    openai_moderation?: boolean;
  } | null;
  draft: BackendVersion | null;
  published: BackendVersion | null;
}

interface BackendApproval {
  id: string;
  tool_name: ToolBinding["kind"];
  status: ApprovalRequest["status"];
  redacted_arguments: Record<string, unknown>;
  resolved_by: string | null;
  decision_reason: string | null;
  session_id: string | null;
  run_id: string | null;
  continuation_error: string | null;
  expires_at: string | null;
  created_at: string;
}

interface BackendTeamMember {
  agent_config_id: string;
  agent_version_id: string;
  position: number;
  name: string;
  slug: string;
  version: number;
  status: TeamConfig["status"];
}

interface BackendTeamVersion {
  id: string;
  version: number;
  status: TeamConfig["status"];
  instructions: string;
  mode: TeamConfig["mode"];
  model_id: string;
  temperature: number;
  members: BackendTeamMember[];
  created_at: string;
}

interface BackendTeam {
  id: string;
  name: string;
  slug: string;
  description: string | null;
  published_version_id: string | null;
  updated_at: string;
  tools: Array<{
    tool_key: ToolBinding["kind"] | null;
    tool_definition_id?: string | null;
    config: Record<string, unknown>;
    credential_id?: string | null;
  }>;
  draft: BackendTeamVersion | null;
  published: BackendTeamVersion | null;
}

interface BackendWorkflowStep {
  id: string;
  position: number;
  name: string;
  target_type: "agent" | "team";
  target_config_id: string;
  target_version_id: string;
  target_name: string;
  target_slug: string;
  target_version: number;
  target_status: WorkflowConfig["status"];
  condition_expression: string | null;
}

interface BackendWorkflowVersion {
  id: string;
  version: number;
  status: WorkflowConfig["status"];
  mode: WorkflowConfig["mode"];
  steps: BackendWorkflowStep[];
  created_at: string;
}

interface BackendWorkflow {
  id: string;
  name: string;
  slug: string;
  description: string | null;
  published_version_id: string | null;
  updated_at: string;
  draft: BackendWorkflowVersion | null;
  published: BackendWorkflowVersion | null;
}

function mapApproval(raw: BackendApproval): ApprovalRequest {
  const catalog = TOOL_CATALOG.find((item) => item.kind === raw.tool_name);
  return {
    id: raw.id,
    agentName: "Tenant agent",
    agentId: "",
    toolLabel: catalog?.label ?? raw.tool_name,
    toolKind: raw.tool_name,
    summary: `Approval required for ${catalog?.label ?? raw.tool_name}`,
    argumentsPreview: raw.redacted_arguments,
    status: raw.status,
    requestedBy: raw.resolved_by ?? "End user",
    createdAt: raw.created_at,
    sessionId: raw.session_id,
    runId: raw.run_id,
    continuationError: raw.continuation_error,
  };
}

function frontendModel(modelId: string): AgentConfig["model"] {
  return modelId.replace(
    /^(openai|anthropic|groq|moonshot|nvidia|gemini):/,
    "",
  ) as AgentConfig["model"];
}

function backendModel(modelId: AgentDraftInput["model"]): string {
  return toBackendModelId(modelId);
}

function mapAgent(raw: BackendAgent): AgentConfig {
  const editable = raw.draft ?? raw.published;
  const tools = raw.tools.map((tool) => {
    const catalog = TOOL_CATALOG.find((item) => item.kind === tool.tool_key);
    return {
      id: `${raw.id}:${tool.tool_key ?? tool.tool_definition_id}`,
      kind: tool.tool_key ?? ("rest_mutate" as const),
      label: catalog?.label ?? "Reusable tool",
      enabled: true,
      config: {
        ...tool.config,
        ...(tool.credential_id
          ? { credential_id: tool.credential_id }
          : {}),
      },
      requiresApproval: catalog?.requiresApproval ?? true,
      ...(tool.tool_definition_id
        ? { definitionId: tool.tool_definition_id }
        : {}),
    };
  });
  return {
    id: raw.id,
    name: raw.name,
    slug: raw.slug,
    description: raw.description ?? "",
    instructions: editable?.instructions ?? "You are a helpful assistant.",
    model: frontendModel(editable?.model_id ?? "openai:gpt-4.1-mini"),
    temperature: editable?.temperature ?? 0.2,
    memoryMode:
      editable?.memory_mode === "persistent_user" ? "persistent" : "session",
    status: raw.published ? "published" : "draft",
    tools,
    knowledgeBaseId: raw.knowledge_base_id,
    knowledgeBase: null,
    frameworkAdapter: (raw.framework_adapter ||
      "agno") as import("@/lib/api/types").FrameworkAdapter,
    guardrails: {
      promptInjection: Boolean(raw.guardrails?.prompt_injection),
      piiDetection: Boolean(raw.guardrails?.pii_detection),
      openaiModeration: Boolean(raw.guardrails?.openai_moderation),
    },
    draftVersion: raw.draft?.version ?? raw.published?.version ?? 1,
    publishedVersion: raw.published?.version ?? null,
    updatedAt: raw.updated_at,
  };
}

function backendDraft(draft: AgentDraftInput) {
  return {
    name: draft.name,
    description: draft.description,
    instructions: draft.instructions,
    model_id: backendModel(draft.model),
    temperature: draft.temperature,
    memory_mode:
      draft.memoryMode === "persistent" ? "persistent_user" : "session",
    tools: draft.tools
      .filter((tool) => tool.enabled)
      .map((tool) => {
        const { credential_id, ...config } = tool.config;
        return {
          tool_key: tool.definitionId ? null : tool.kind,
          tool_definition_id: tool.definitionId ?? null,
          config,
          credential_id:
            typeof credential_id === "string" && credential_id
              ? credential_id
              : null,
        };
      }),
    knowledge_base_id: draft.knowledgeBaseId,
    framework_adapter: draft.frameworkAdapter,
    guardrails: {
      prompt_injection: draft.guardrails.promptInjection,
      pii_detection: draft.guardrails.piiDetection,
      openai_moderation: draft.guardrails.openaiModeration,
    },
  };
}

function mapTeam(raw: BackendTeam): TeamConfig {
  const editable = raw.draft ?? raw.published;
  const tools = (raw.tools ?? []).map((tool) => {
    const catalog = TOOL_CATALOG.find((item) => item.kind === tool.tool_key);
    return {
      id: `${raw.id}:${tool.tool_key ?? tool.tool_definition_id}`,
      kind: tool.tool_key ?? ("rest_mutate" as const),
      label: catalog?.label ?? "Reusable tool",
      enabled: true,
      config: {
        ...tool.config,
        ...(tool.credential_id
          ? { credential_id: tool.credential_id }
          : {}),
      },
      requiresApproval: catalog?.requiresApproval ?? true,
      ...(tool.tool_definition_id
        ? { definitionId: tool.tool_definition_id }
        : {}),
    };
  });
  return {
    id: raw.id,
    name: raw.name,
    slug: raw.slug,
    description: raw.description ?? "",
    instructions:
      editable?.instructions ??
      "Coordinate the team specialists and return one clear answer.",
    mode: editable?.mode ?? "coordinate",
    model: frontendModel(
      editable?.model_id ?? "openai:gpt-4.1-mini",
    ),
    temperature: editable?.temperature ?? 0.2,
    status: raw.published ? "published" : "draft",
    tools,
    members: (editable?.members ?? []).map((member) => ({
      agentConfigId: member.agent_config_id,
      agentVersionId: member.agent_version_id,
      position: member.position,
      name: member.name,
      slug: member.slug,
      version: member.version,
      status: member.status,
    })),
    draftVersion: raw.draft?.version ?? raw.published?.version ?? 1,
    publishedVersion: raw.published?.version ?? null,
    updatedAt: raw.updated_at,
  };
}

function mapWorkflow(raw: BackendWorkflow): WorkflowConfig {
  const editable = raw.draft ?? raw.published;
  return {
    id: raw.id,
    name: raw.name,
    slug: raw.slug,
    description: raw.description ?? "",
    mode: editable?.mode ?? "sequential",
    status: raw.published ? "published" : "draft",
    steps: (editable?.steps ?? []).map((step) => ({
      id: step.id,
      name: step.name,
      targetType: step.target_type,
      targetConfigId: step.target_config_id,
      targetVersionId: step.target_version_id,
      targetName: step.target_name,
      targetSlug: step.target_slug,
      targetVersion: step.target_version,
      targetStatus: step.target_status,
      conditionExpression: step.condition_expression,
    })),
    draftVersion: raw.draft?.version ?? raw.published?.version ?? 0,
    publishedVersion: raw.published?.version ?? null,
    updatedAt: raw.updated_at,
  };
}

function mocksEnabled() {
  return process.env.NEXT_PUBLIC_USE_MOCKS === "1";
}

async function withFallback<T>(
  live: () => Promise<T>,
  fallback: T,
): Promise<T> {
  try {
    return await live();
  } catch {
    if (mocksEnabled()) {
      return fallback;
    }
    throw new Error("API unavailable and mocks disabled");
  }
}

export type CatalogListParams = {
  q?: string;
  status?: "all" | "published" | "draft";
  page?: number;
  pageSize?: number;
};

function catalogQuery(params: CatalogListParams = {}): string {
  const search = new URLSearchParams();
  if (params.q?.trim()) search.set("q", params.q.trim());
  if (params.status && params.status !== "all") {
    search.set("status", params.status);
  }
  search.set("page", String(params.page ?? 1));
  search.set("page_size", String(params.pageSize ?? 25));
  return search.toString();
}

/** Backend catalog `page_size` max is 100 — walk pages for picker lists. */
async function loadAllCatalogPages<T>(
  fetchPage: (page: number, pageSize: number) => Promise<CatalogPage<T>>,
): Promise<T[]> {
  const pageSize = 100;
  const first = await fetchPage(1, pageSize);
  if (first.total <= first.items.length) return first.items;
  const items = [...first.items];
  const totalPages = Math.ceil(first.total / pageSize);
  for (let page = 2; page <= totalPages; page += 1) {
    const next = await fetchPage(page, pageSize);
    items.push(...next.items);
  }
  return items;
}

export async function listAgentCatalog(
  accessToken: string,
  params: CatalogListParams = {},
): Promise<CatalogPage<AgentSummary>> {
  const row = await apiFetch<{
    items: Array<{
      id: string;
      slug: string;
      name: string;
      status: "draft" | "published" | "archived";
      model_id: string;
      domain?: string;
      published_version: number | null;
      updated_at: string;
    }>;
    total: number;
    page: number;
    page_size: number;
  }>(`/admin/agents/catalog?${catalogQuery(params)}`, { accessToken });
  return {
    items: row.items.map((item) => ({
      id: item.id,
      name: item.name,
      slug: item.slug,
      status: item.status,
      model: frontendModel(item.model_id),
      domain: item.domain || "generic",
      publishedVersion: item.published_version,
      updatedAt: item.updated_at,
    })),
    total: row.total,
    page: row.page,
    pageSize: row.page_size,
  };
}

export async function listTeamCatalog(
  accessToken: string,
  params: CatalogListParams = {},
): Promise<CatalogPage<TeamSummary>> {
  const row = await apiFetch<{
    items: Array<{
      id: string;
      slug: string;
      name: string;
      status: "draft" | "published" | "archived";
      mode: "route" | "coordinate";
      member_count: number;
      domain?: string;
      published_version: number | null;
      updated_at: string;
    }>;
    total: number;
    page: number;
    page_size: number;
  }>(`/admin/teams/catalog?${catalogQuery(params)}`, { accessToken });
  return {
    items: row.items.map((item) => ({
      id: item.id,
      name: item.name,
      slug: item.slug,
      status: item.status,
      mode: item.mode,
      memberCount: item.member_count,
      domain: item.domain || "generic",
      publishedVersion: item.published_version,
      updatedAt: item.updated_at,
    })),
    total: row.total,
    page: row.page,
    pageSize: row.page_size,
  };
}

export async function listWorkflowCatalog(
  accessToken: string,
  params: CatalogListParams = {},
): Promise<CatalogPage<WorkflowSummary>> {
  const row = await apiFetch<{
    items: Array<{
      id: string;
      slug: string;
      name: string;
      status: "draft" | "published" | "archived";
      mode: "sequential" | "parallel";
      step_count: number;
      domain?: string;
      published_version: number | null;
      updated_at: string;
    }>;
    total: number;
    page: number;
    page_size: number;
  }>(`/admin/workflows/catalog?${catalogQuery(params)}`, { accessToken });
  return {
    items: row.items.map((item) => ({
      id: item.id,
      name: item.name,
      slug: item.slug,
      mode: item.mode,
      status: item.status,
      stepCount: item.step_count,
      domain: item.domain || "generic",
      publishedVersion: item.published_version,
      updatedAt: item.updated_at,
    })),
    total: row.total,
    page: row.page,
    pageSize: row.page_size,
  };
}

/**
 * Summaries for pickers / name lookup. Uses `/catalog` (thin rows) instead of
 * `/admin/agents` which hydrates every draft + tools and dominated RSC time.
 */
export function listAgents(accessToken: string): Promise<AgentSummary[]> {
  return withFallback(
    () =>
      loadAllCatalogPages((page, pageSize) =>
        listAgentCatalog(accessToken, { page, pageSize }),
      ),
    MOCK_AGENTS,
  );
}

export function getAgent(
  accessToken: string,
  agentId: string,
): Promise<AgentConfig> {
  return withFallback(
    async () =>
      mapAgent(
        await apiFetch<BackendAgent>(`/admin/agents/${agentId}`, {
          accessToken,
        }),
      ),
    agentId === MOCK_AGENT_DETAIL.id
      ? MOCK_AGENT_DETAIL
      : {
          ...MOCK_AGENT_DETAIL,
          id: agentId,
          name: "New agent",
          slug: "new-agent",
          status: "draft",
          publishedVersion: null,
          draftVersion: 1,
          knowledgeBaseId: null,
          knowledgeBase: null,
          frameworkAdapter: "agno",
          guardrails: {
            promptInjection: false,
            piiDetection: false,
            openaiModeration: false,
          },
          tools: [],
        },
  );
}

export function saveAgentDraft(
  accessToken: string,
  agentId: string,
  draft: AgentDraftInput,
): Promise<AgentConfig> {
  return withFallback(
    () =>
      apiFetch<BackendAgent>(`/admin/agents/${agentId}`, {
        accessToken,
        method: "PATCH",
        body: backendDraft(draft),
      }).then(mapAgent),
    {
      ...MOCK_AGENT_DETAIL,
      ...draft,
      id: agentId,
      status: "draft",
      draftVersion: MOCK_AGENT_DETAIL.draftVersion + 1,
      updatedAt: new Date().toISOString(),
    },
  );
}

export async function publishAgent(
  accessToken: string,
  agentId: string,
): Promise<AgentConfig> {
  return withFallback(
    () =>
      apiFetch<BackendAgent>(`/admin/agents/${agentId}/publish`, {
        accessToken,
        method: "POST",
      }).then(mapAgent),
    {
      ...MOCK_AGENT_DETAIL,
      id: agentId,
      status: "published",
      publishedVersion: (MOCK_AGENT_DETAIL.publishedVersion ?? 0) + 1,
      updatedAt: new Date().toISOString(),
    },
  );
}

export async function listAgentVersions(
  accessToken: string,
  agentId: string,
): Promise<
  Array<{
    id: string;
    version: number;
    status: AgentConfig["status"];
    modelId: string;
    isLive: boolean;
    createdAt: string;
  }>
> {
  const rows = await apiFetch<
    Array<{
      id: string;
      version: number;
      status: AgentConfig["status"];
      model_id: string;
      is_live: boolean;
      created_at: string;
    }>
  >(`/admin/agents/${agentId}/versions`, { accessToken });
  return rows.map((row) => ({
    id: row.id,
    version: row.version,
    status: row.status,
    modelId: row.model_id,
    isLive: row.is_live,
    createdAt: row.created_at,
  }));
}

export async function getAgentVersion(
  accessToken: string,
  agentId: string,
  versionId: string,
): Promise<{
  id: string;
  version: number;
  status: AgentConfig["status"];
  instructions: string;
  modelId: string;
  temperature: number;
  memoryMode: string;
  createdAt: string;
}> {
  const raw = await apiFetch<{
    id: string;
    version: number;
    status: AgentConfig["status"];
    instructions: string;
    model_id: string;
    temperature: number;
    memory_mode: string;
    created_at: string;
  }>(`/admin/agents/${agentId}/versions/${versionId}`, { accessToken });
  return {
    id: raw.id,
    version: raw.version,
    status: raw.status,
    instructions: raw.instructions,
    modelId: raw.model_id,
    temperature: raw.temperature,
    memoryMode: raw.memory_mode,
    createdAt: raw.created_at,
  };
}

export async function restoreAgentVersion(
  accessToken: string,
  agentId: string,
  versionId: string,
  options: { asDraft?: boolean } = {},
): Promise<AgentConfig> {
  return mapAgent(
    await apiFetch<BackendAgent>(
      `/admin/agents/${agentId}/versions/${versionId}/restore`,
      {
        accessToken,
        method: "POST",
        body: { as_draft: options.asDraft ?? false },
      },
    ),
  );
}

export async function deleteAgent(
  accessToken: string,
  agentId: string,
): Promise<void> {
  await apiFetch<void>(`/admin/agents/${agentId}`, {
    accessToken,
    method: "DELETE",
  });
}

export async function cloneAgent(
  accessToken: string,
  agentId: string,
): Promise<AgentConfig> {
  return mapAgent(
    await apiFetch<BackendAgent>(`/admin/agents/${agentId}/clone`, {
      accessToken,
      method: "POST",
    }),
  );
}

export function createAgent(
  accessToken: string,
  input: Pick<AgentDraftInput, "name" | "slug">,
): Promise<AgentConfig> {
  return withFallback(
    () =>
      apiFetch<BackendAgent>("/admin/agents", {
        accessToken,
        method: "POST",
        body: {
          ...input,
          model_id: "openai:gpt-4.1-mini",
          memory_mode: "session",
        },
      }).then(mapAgent),
    {
      ...MOCK_AGENT_DETAIL,
      id: `agt_${Date.now()}`,
      name: input.name,
      slug: input.slug,
      status: "draft",
      publishedVersion: null,
      draftVersion: 1,
      instructions: "Describe how this agent should behave.",
      description: "",
      tools: [],
      knowledgeBaseId: null,
      knowledgeBase: null,
      frameworkAdapter: "agno",
      guardrails: {
        promptInjection: false,
        piiDetection: false,
        openaiModeration: false,
      },
      updatedAt: new Date().toISOString(),
    },
  );
}

export async function listTeams(
  accessToken: string,
): Promise<TeamSummary[]> {
  return loadAllCatalogPages((page, pageSize) =>
    listTeamCatalog(accessToken, { page, pageSize }),
  );
}

export async function getTeam(
  accessToken: string,
  teamId: string,
): Promise<TeamConfig> {
  return mapTeam(
    await apiFetch<BackendTeam>(`/admin/teams/${teamId}`, { accessToken }),
  );
}

export async function createTeam(
  accessToken: string,
  input: { name: string; slug: string },
): Promise<TeamConfig> {
  return mapTeam(
    await apiFetch<BackendTeam>("/admin/teams", {
      accessToken,
      method: "POST",
      body: input,
    }),
  );
}

export async function cloneTeam(
  accessToken: string,
  teamId: string,
): Promise<TeamConfig> {
  return mapTeam(
    await apiFetch<BackendTeam>(`/admin/teams/${teamId}/clone`, {
      accessToken,
      method: "POST",
    }),
  );
}

export async function saveTeamDraft(
  accessToken: string,
  teamId: string,
  draft: TeamDraftInput,
): Promise<TeamConfig> {
  return mapTeam(
    await apiFetch<BackendTeam>(`/admin/teams/${teamId}`, {
      accessToken,
      method: "PATCH",
      body: {
        slug: draft.slug,
        name: draft.name,
        description: draft.description,
        instructions: draft.instructions,
        mode: draft.mode,
        model_id: backendModel(draft.model),
        temperature: draft.temperature,
        member_config_ids: draft.memberConfigIds,
        tools: draft.tools
          .filter((tool) => tool.enabled)
          .map((tool) => {
            const { credential_id, ...config } = tool.config;
            return {
              tool_key: tool.definitionId ? null : tool.kind,
              tool_definition_id: tool.definitionId ?? null,
              config,
              credential_id:
                typeof credential_id === "string" && credential_id
                  ? credential_id
                  : null,
            };
          }),
      },
    }),
  );
}

export async function publishTeam(
  accessToken: string,
  teamId: string,
): Promise<TeamConfig> {
  return mapTeam(
    await apiFetch<BackendTeam>(`/admin/teams/${teamId}/publish`, {
      accessToken,
      method: "POST",
    }),
  );
}

export async function listTeamVersions(
  accessToken: string,
  teamId: string,
): Promise<TeamVersionSummary[]> {
  const rows = await apiFetch<
    Array<{
      id: string;
      version: number;
      status: TeamConfig["status"];
      mode: TeamConfig["mode"];
      member_count: number;
      is_live: boolean;
      created_at: string;
    }>
  >(`/admin/teams/${teamId}/versions`, { accessToken });
  return rows.map((row) => ({
    id: row.id,
    version: row.version,
    status: row.status,
    mode: row.mode,
    memberCount: row.member_count,
    isLive: row.is_live,
    createdAt: row.created_at,
  }));
}

export async function getTeamVersion(
  accessToken: string,
  teamId: string,
  versionId: string,
): Promise<TeamVersionDetail> {
  const raw = await apiFetch<BackendTeamVersion>(
    `/admin/teams/${teamId}/versions/${versionId}`,
    { accessToken },
  );
  return {
    id: raw.id,
    version: raw.version,
    status: raw.status,
    instructions: raw.instructions,
    mode: raw.mode,
    model: frontendModel(raw.model_id),
    temperature: raw.temperature,
    members: (raw.members ?? []).map((member) => ({
      agentConfigId: member.agent_config_id,
      agentVersionId: member.agent_version_id,
      position: member.position,
      name: member.name,
      slug: member.slug,
      version: member.version,
      status: member.status,
    })),
    createdAt: raw.created_at,
  };
}

export async function restoreTeamVersion(
  accessToken: string,
  teamId: string,
  versionId: string,
  options: { asDraft?: boolean } = {},
): Promise<TeamConfig> {
  return mapTeam(
    await apiFetch<BackendTeam>(
      `/admin/teams/${teamId}/versions/${versionId}/restore`,
      {
        accessToken,
        method: "POST",
        body: { as_draft: options.asDraft ?? false },
      },
    ),
  );
}

export async function deleteTeam(
  accessToken: string,
  teamId: string,
): Promise<void> {
  await apiFetch<void>(`/admin/teams/${teamId}`, {
    accessToken,
    method: "DELETE",
  });
}

export async function listWorkflows(
  accessToken: string,
): Promise<WorkflowSummary[]> {
  return loadAllCatalogPages((page, pageSize) =>
    listWorkflowCatalog(accessToken, { page, pageSize }),
  );
}

/**
 * Catalog for Public API try-it: all published workflows, with team options from
 * workflow steps when present, otherwise all published teams as a fallback list.
 */
export async function listPublicApiRunCatalog(
  accessToken: string,
): Promise<PublicApiCatalogLoad> {
  const [workflows, teams] = await Promise.all([
    apiFetch<BackendWorkflow[]>("/admin/workflows", { accessToken }),
    apiFetch<BackendTeam[]>("/admin/teams", { accessToken }),
  ]);
  const publishedTeams = teams
    .filter((team) => team.published != null)
    .map((team) => ({ id: team.id, name: team.name, slug: team.slug }));
  return buildPublicApiRunCatalog(workflows, publishedTeams);
}

export async function getWorkflow(
  accessToken: string,
  workflowId: string,
): Promise<WorkflowConfig> {
  return mapWorkflow(
    await apiFetch<BackendWorkflow>(`/admin/workflows/${workflowId}`, {
      accessToken,
    }),
  );
}

export async function getPublishedWorkflowTeamSteps(
  accessToken: string,
  workflowId: string,
): Promise<PublicApiTeamOption[]> {
  const raw = await apiFetch<BackendWorkflow>(`/admin/workflows/${workflowId}`, {
    accessToken,
  });
  return teamStepsFromPublished(raw.published);
}

export async function getWorkflowAssignments(
  accessToken: string,
  workflowId: string,
): Promise<WorkflowAssignments> {
  const result = await apiFetch<{ workflow_id: string; user_ids: string[] }>(
    `/admin/workflows/${workflowId}/assignments`,
    { accessToken },
  );
  return { workflowId: result.workflow_id, userIds: result.user_ids };
}

export async function saveWorkflowAssignments(
  accessToken: string,
  workflowId: string,
  userIds: string[],
): Promise<WorkflowAssignments> {
  const result = await apiFetch<{ workflow_id: string; user_ids: string[] }>(
    `/admin/workflows/${workflowId}/assignments`,
    {
      accessToken,
      method: "PUT",
      body: { user_ids: userIds },
    },
  );
  return { workflowId: result.workflow_id, userIds: result.user_ids };
}

export async function listAvailableWorkflows(
  accessToken: string,
): Promise<AvailableWorkflow[]> {
  return apiFetch<AvailableWorkflow[]>("/api/workflows/available", {
    accessToken,
  });
}

export async function listAvailableTeams(
  accessToken: string,
): Promise<AvailableTeam[]> {
  return apiFetch<AvailableTeam[]>("/api/teams/available", {
    accessToken,
  });
}

interface BackendTenantUser {
  id: string;
  user_id: string;
  display_name: string;
  email: string | null;
  phone?: string | null;
  role: "tenant_admin" | "end_user";
  is_active: boolean;
  timezone?: string;
  invite_pending?: boolean;
  temporary_password?: string | null;
  sign_in_url?: string | null;
  workflow_ids: string[];
  team_ids?: string[];
  created_at: string;
  updated_at: string;
}

function mapTenantUser(row: BackendTenantUser): TenantUser {
  return {
    id: row.id,
    userId: row.user_id,
    displayName: row.display_name,
    email: row.email,
    phone: row.phone ?? null,
    role: row.role,
    isActive: row.is_active,
    timezone: row.timezone || "UTC",
    invitePending: Boolean(row.invite_pending),
    temporaryPassword: row.temporary_password ?? null,
    signInUrl: row.sign_in_url ?? null,
    workflowIds: row.workflow_ids ?? [],
    teamIds: row.team_ids ?? [],
    createdAt: row.created_at,
    updatedAt: row.updated_at,
  };
}

export async function listTenantUsers(
  accessToken: string,
): Promise<TenantUser[]> {
  const rows = await apiFetch<BackendTenantUser[]>("/admin/users", {
    accessToken,
  });
  return rows.map(mapTenantUser);
}

export async function getTenantUser(
  accessToken: string,
  membershipId: string,
): Promise<TenantUser> {
  return mapTenantUser(
    await apiFetch<BackendTenantUser>(`/admin/users/${membershipId}`, {
      accessToken,
    }),
  );
}

export async function createTenantUser(
  accessToken: string,
  input: TenantUserInput,
): Promise<TenantUser> {
  return mapTenantUser(
    await apiFetch<BackendTenantUser>("/admin/users", {
      accessToken,
      method: "POST",
      body: {
        ...(input.userId ? { user_id: input.userId } : {}),
        display_name: input.displayName,
        email: input.email,
        phone: input.phone?.trim() || null,
        role: input.role,
        is_active: input.isActive,
        timezone: input.timezone || "UTC",
        workflow_ids: input.workflowIds,
        team_ids: input.teamIds,
        ...(input.password
          ? {
              password: input.password,
              password_confirm: input.passwordConfirm || input.password,
            }
          : {}),
      },
    }),
  );
}

export async function updateTenantUser(
  accessToken: string,
  membershipId: string,
  input: Partial<TenantUserInput>,
): Promise<TenantUser> {
  return mapTenantUser(
    await apiFetch<BackendTenantUser>(`/admin/users/${membershipId}`, {
      accessToken,
      method: "PATCH",
      body: {
        ...(input.displayName !== undefined
          ? { display_name: input.displayName }
          : {}),
        ...(input.email !== undefined ? { email: input.email } : {}),
        ...(input.phone !== undefined
          ? { phone: input.phone?.trim() || null }
          : {}),
        ...(input.role !== undefined ? { role: input.role } : {}),
        ...(input.isActive !== undefined ? { is_active: input.isActive } : {}),
        ...(input.timezone !== undefined ? { timezone: input.timezone } : {}),
        ...(input.workflowIds !== undefined
          ? { workflow_ids: input.workflowIds }
          : {}),
        ...(input.teamIds !== undefined ? { team_ids: input.teamIds } : {}),
        ...(input.password
          ? {
              password: input.password,
              password_confirm: input.passwordConfirm || input.password,
            }
          : {}),
      },
    }),
  );
}

export async function syncTenantUserIdentity(
  accessToken: string,
  membershipId: string,
): Promise<TenantUser> {
  return mapTenantUser(
    await apiFetch<BackendTenantUser>(
      `/admin/users/${membershipId}/sync-identity`,
      {
        accessToken,
        method: "POST",
      },
    ),
  );
}

export async function setTenantUserPassword(
  accessToken: string,
  membershipId: string,
  password: string,
  passwordConfirm: string,
): Promise<void> {
  await apiFetch<void>(`/admin/users/${membershipId}/password`, {
    accessToken,
    method: "POST",
    body: {
      password,
      password_confirm: passwordConfirm,
    },
  });
}

export async function changeMyPassword(
  accessToken: string,
  currentPassword: string,
  newPassword: string,
  newPasswordConfirm: string,
): Promise<void> {
  await apiFetch<void>("/admin/users/me/password", {
    accessToken,
    method: "POST",
    body: {
      current_password: currentPassword,
      new_password: newPassword,
      new_password_confirm: newPasswordConfirm,
    },
  });
}

export async function deleteTenantUser(
  accessToken: string,
  membershipId: string,
): Promise<void> {
  await apiFetch<void>(`/admin/users/${membershipId}`, {
    accessToken,
    method: "DELETE",
  });
}

interface BackendEndCustomer {
  id: string;
  email: string;
  display_name: string;
  email_verified_at: string | null;
  is_active: boolean;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

function mapEndCustomer(row: BackendEndCustomer): EndCustomer {
  return {
    id: row.id,
    email: row.email,
    displayName: row.display_name,
    emailVerifiedAt: row.email_verified_at,
    isActive: row.is_active,
    metadata: row.metadata ?? {},
    createdAt: row.created_at,
    updatedAt: row.updated_at,
  };
}

export async function listEndCustomers(
  accessToken: string,
): Promise<EndCustomer[]> {
  const rows = await apiFetch<BackendEndCustomer[]>("/admin/customers", {
    accessToken,
  });
  return rows.map(mapEndCustomer);
}

export async function updateEndCustomer(
  accessToken: string,
  customerId: string,
  input: {
    displayName?: string;
    isActive?: boolean;
    metadata?: Record<string, unknown>;
  },
): Promise<EndCustomer> {
  return mapEndCustomer(
    await apiFetch<BackendEndCustomer>(`/admin/customers/${customerId}`, {
      accessToken,
      method: "PATCH",
      body: {
        ...(input.displayName !== undefined
          ? { display_name: input.displayName }
          : {}),
        ...(input.isActive !== undefined ? { is_active: input.isActive } : {}),
        ...(input.metadata !== undefined ? { metadata: input.metadata } : {}),
      },
    }),
  );
}

export async function createWorkflow(
  accessToken: string,
  input: { name: string; slug: string },
): Promise<WorkflowConfig> {
  return mapWorkflow(
    await apiFetch<BackendWorkflow>("/admin/workflows", {
      accessToken,
      method: "POST",
      body: input,
    }),
  );
}

export async function cloneWorkflow(
  accessToken: string,
  workflowId: string,
): Promise<WorkflowConfig> {
  return mapWorkflow(
    await apiFetch<BackendWorkflow>(`/admin/workflows/${workflowId}/clone`, {
      accessToken,
      method: "POST",
    }),
  );
}

export async function saveWorkflowDraft(
  accessToken: string,
  workflowId: string,
  draft: WorkflowDraftInput,
): Promise<WorkflowConfig> {
  return mapWorkflow(
    await apiFetch<BackendWorkflow>(`/admin/workflows/${workflowId}`, {
      accessToken,
      method: "PATCH",
      body: {
        name: draft.name,
        description: draft.description,
        mode: draft.mode,
        steps: draft.steps.map((step) => ({
          name: step.name,
          target_type: step.targetType,
          target_config_id: step.targetConfigId,
          condition_expression: step.conditionExpression || null,
        })),
      },
    }),
  );
}

export async function publishWorkflow(
  accessToken: string,
  workflowId: string,
): Promise<WorkflowConfig> {
  return mapWorkflow(
    await apiFetch<BackendWorkflow>(`/admin/workflows/${workflowId}/publish`, {
      accessToken,
      method: "POST",
    }),
  );
}

export async function listWorkflowVersions(
  accessToken: string,
  workflowId: string,
): Promise<
  Array<{
    id: string;
    version: number;
    status: WorkflowConfig["status"];
    mode: WorkflowConfig["mode"];
    stepCount: number;
    isLive: boolean;
    createdAt: string;
  }>
> {
  const rows = await apiFetch<
    Array<{
      id: string;
      version: number;
      status: WorkflowConfig["status"];
      mode: WorkflowConfig["mode"];
      step_count: number;
      is_live: boolean;
      created_at: string;
    }>
  >(`/admin/workflows/${workflowId}/versions`, { accessToken });
  return rows.map((row) => ({
    id: row.id,
    version: row.version,
    status: row.status,
    mode: row.mode,
    stepCount: row.step_count,
    isLive: row.is_live,
    createdAt: row.created_at,
  }));
}

export async function getWorkflowVersion(
  accessToken: string,
  workflowId: string,
  versionId: string,
): Promise<{
  id: string;
  version: number;
  status: WorkflowConfig["status"];
  mode: WorkflowConfig["mode"];
  steps: WorkflowConfig["steps"];
  createdAt: string;
}> {
  const raw = await apiFetch<BackendWorkflowVersion>(
    `/admin/workflows/${workflowId}/versions/${versionId}`,
    { accessToken },
  );
  return {
    id: raw.id,
    version: raw.version,
    status: raw.status,
    mode: raw.mode,
    steps: (raw.steps ?? []).map((step) => ({
      id: step.id,
      name: step.name,
      targetType: step.target_type,
      targetConfigId: step.target_config_id,
      targetVersionId: step.target_version_id,
      targetName: step.target_name,
      targetSlug: step.target_slug,
      targetVersion: step.target_version,
      targetStatus: step.target_status,
      conditionExpression: step.condition_expression,
    })),
    createdAt: raw.created_at,
  };
}

export async function restoreWorkflowVersion(
  accessToken: string,
  workflowId: string,
  versionId: string,
  options: { asDraft?: boolean } = {},
): Promise<WorkflowConfig> {
  return mapWorkflow(
    await apiFetch<BackendWorkflow>(
      `/admin/workflows/${workflowId}/versions/${versionId}/restore`,
      {
        accessToken,
        method: "POST",
        body: { as_draft: options.asDraft ?? false },
      },
    ),
  );
}

export async function deleteWorkflow(
  accessToken: string,
  workflowId: string,
): Promise<void> {
  await apiFetch<void>(`/admin/workflows/${workflowId}`, {
    accessToken,
    method: "DELETE",
  });
}

export function listApprovals(accessToken: string): Promise<ApprovalRequest[]> {
  return withFallback(
    async () =>
      (
        await apiFetch<BackendApproval[]>("/admin/approvals", { accessToken })
      ).map(mapApproval),
    MOCK_APPROVALS,
  );
}

export function resolveApproval(
  accessToken: string,
  approvalId: string,
  decision: "approved" | "rejected",
  reason?: string,
): Promise<ApprovalRequest> {
  return withFallback(
    () =>
      apiFetch<BackendApproval>(`/admin/approvals/${approvalId}/resolve`, {
        accessToken,
        method: "POST",
        body: { approved: decision === "approved", reason },
      }).then(mapApproval),
    {
      ...(MOCK_APPROVALS.find((a) => a.id === approvalId) ?? MOCK_APPROVALS[0]),
      status: decision,
    },
  );
}

export function listIngestionStatuses(
  accessToken: string,
): Promise<KnowledgeSource[]> {
  return withFallback(
    async () => {
      const rows = await apiFetch<
        Array<{
          id: string;
          knowledge_base_id: string;
          status: string;
          uri: string;
          metadata: Record<string, unknown>;
          created_at: string;
          updated_at: string;
        }>
      >("/admin/knowledge/sources", { accessToken });
      return rows.map((row) => ({
        id: row.id,
        knowledgeBaseId: row.knowledge_base_id,
        name: String(row.metadata.filename ?? row.uri.split("/").pop() ?? "source"),
        mimeType: String(row.metadata.content_type ?? "application/octet-stream"),
        byteSize: Number(row.metadata.bytes ?? 0),
        status:
          row.status === "queued" || row.status === "indexing"
            ? "processing"
            : (row.status as KnowledgeSource["status"]),
        errorMessage:
          typeof row.metadata.error_message === "string"
            ? row.metadata.error_message
            : null,
        createdAt: row.created_at,
        updatedAt: row.updated_at,
      }));
    },
    MOCK_INGESTION,
  );
}

export async function uploadKnowledgeSource(
  accessToken: string,
  knowledgeBaseId: string,
  file: File,
): Promise<KnowledgeSource> {
  const form = new FormData();
  form.set("file", file);
  // Same unpacking as apiFetch — packed platform-tenant tokens must not be
  // sent raw as Bearer (breaks KB upload for platform admins in a workspace).
  const access = unpackAccessContext(accessToken);
  const response = await fetch(
    agentOsUrl(`/admin/knowledge/bases/${knowledgeBaseId}/upload`),
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${access.token}`,
        ...(access.platformTenantId
          ? { "X-Platform-Tenant-ID": access.platformTenantId }
          : {}),
        ...devTenantHeaders(),
      },
      body: form,
    },
  );
  if (!response.ok) {
    throw new Error(`Upload failed (${response.status})`);
  }
  const row = (await response.json()) as {
    id: string;
    uri: string;
    status: string;
    metadata: Record<string, unknown>;
    created_at: string;
    updated_at: string;
  };
  return mapKnowledgeSourceRow(row, knowledgeBaseId, file.name, file.type, file.size);
}

function mapKnowledgeSourceRow(
  row: {
    id: string;
    uri?: string;
    status: string;
    metadata: Record<string, unknown>;
    created_at: string;
    updated_at: string;
  },
  knowledgeBaseId: string,
  fallbackName: string,
  fallbackMime = "text/plain",
  fallbackBytes = 0,
): KnowledgeSource {
  return {
    id: row.id,
    knowledgeBaseId,
    name: String(row.metadata.filename ?? fallbackName),
    mimeType: String(row.metadata.content_type ?? fallbackMime),
    byteSize: Number(row.metadata.bytes ?? fallbackBytes),
    status:
      row.status === "queued" || row.status === "indexing"
        ? "processing"
        : (row.status as KnowledgeSource["status"]),
    errorMessage:
      typeof row.metadata.error_message === "string"
        ? row.metadata.error_message
        : null,
    createdAt: row.created_at,
    updatedAt: row.updated_at,
  };
}

export async function ingestKnowledgeUrl(
  accessToken: string,
  knowledgeBaseId: string,
  url: string,
): Promise<KnowledgeSource> {
  const row = await apiFetch<{
    id: string;
    uri: string;
    status: string;
    metadata: Record<string, unknown>;
    created_at: string;
    updated_at: string;
  }>(`/admin/knowledge/bases/${knowledgeBaseId}/ingest/url`, {
    accessToken,
    method: "POST",
    body: { url },
  });
  return mapKnowledgeSourceRow(row, knowledgeBaseId, url);
}

export async function ingestKnowledgeS3(
  accessToken: string,
  knowledgeBaseId: string,
  uri: string,
): Promise<KnowledgeSource> {
  const row = await apiFetch<{
    id: string;
    uri: string;
    status: string;
    metadata: Record<string, unknown>;
    created_at: string;
    updated_at: string;
  }>(`/admin/knowledge/bases/${knowledgeBaseId}/ingest/s3`, {
    accessToken,
    method: "POST",
    body: { uri },
  });
  return mapKnowledgeSourceRow(row, knowledgeBaseId, uri);
}

export async function ingestKnowledgeGithub(
  accessToken: string,
  knowledgeBaseId: string,
  input: { repo: string; path: string; ref?: string; credentialId?: string },
): Promise<KnowledgeSource> {
  const row = await apiFetch<{
    id: string;
    uri: string;
    status: string;
    metadata: Record<string, unknown>;
    created_at: string;
    updated_at: string;
  }>(`/admin/knowledge/bases/${knowledgeBaseId}/ingest/github`, {
    accessToken,
    method: "POST",
    body: {
      repo: input.repo,
      path: input.path,
      ref: input.ref ?? "main",
      credential_id: input.credentialId ?? null,
    },
  });
  return mapKnowledgeSourceRow(row, knowledgeBaseId, input.path);
}

export async function createKnowledgeBase(
  accessToken: string,
  name: string,
): Promise<{ id: string; name: string }> {
  return apiFetch<{ id: string; name: string }>("/admin/knowledge/bases", {
    accessToken,
    method: "POST",
    body: { name, config: {} },
  });
}

export async function updateKnowledgeBase(
  accessToken: string,
  knowledgeBaseId: string,
  input: { name: string },
): Promise<{ id: string; name: string }> {
  return apiFetch<{ id: string; name: string }>(
    `/admin/knowledge/bases/${knowledgeBaseId}`,
    {
      accessToken,
      method: "PATCH",
      body: { name: input.name },
    },
  );
}

export async function deleteKnowledgeBase(
  accessToken: string,
  knowledgeBaseId: string,
): Promise<void> {
  await apiFetch<void>(`/admin/knowledge/bases/${knowledgeBaseId}`, {
    accessToken,
    method: "DELETE",
  });
}

export async function listKnowledgeBases(
  accessToken: string,
): Promise<Array<Pick<KnowledgeBaseSummary, "id" | "name">>> {
  return apiFetch<Array<{ id: string; name: string }>>(
    "/admin/knowledge/bases",
    { accessToken },
  );
}

function mapKnowledgeSource(row: {
  id: string;
  knowledge_base_id: string;
  status: string;
  uri: string;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}): KnowledgeSource {
  return {
    id: row.id,
    knowledgeBaseId: row.knowledge_base_id,
    name: String(row.metadata.filename ?? row.uri.split("/").pop() ?? "source"),
    mimeType: String(row.metadata.content_type ?? "application/octet-stream"),
    byteSize: Number(row.metadata.bytes ?? 0),
    status:
      row.status === "queued" || row.status === "indexing"
        ? "processing"
        : (row.status as KnowledgeSource["status"]),
    errorMessage:
      typeof row.metadata.error_message === "string"
        ? row.metadata.error_message
        : null,
    createdAt: row.created_at,
    updatedAt: row.updated_at,
  };
}

export async function listKnowledgeSources(
  accessToken: string,
  knowledgeBaseId: string,
): Promise<KnowledgeSource[]> {
  const rows = await apiFetch<
    Array<{
      id: string;
      knowledge_base_id: string;
      status: string;
      uri: string;
      metadata: Record<string, unknown>;
      created_at: string;
      updated_at: string;
    }>
  >(`/admin/knowledge/bases/${knowledgeBaseId}/sources`, { accessToken });
  return rows.map(mapKnowledgeSource);
}

export async function reindexKnowledgeSource(
  accessToken: string,
  sourceId: string,
): Promise<KnowledgeSource> {
  const row = await apiFetch<{
    id: string;
    knowledge_base_id: string;
    status: string;
    uri: string;
    metadata: Record<string, unknown>;
    created_at: string;
    updated_at: string;
  }>(`/admin/knowledge/sources/${sourceId}/reindex`, {
    accessToken,
    method: "POST",
  });
  return {
    id: row.id,
    knowledgeBaseId: row.knowledge_base_id,
    name: String(row.metadata.filename ?? row.uri),
    mimeType: String(row.metadata.content_type ?? "application/octet-stream"),
    byteSize: Number(row.metadata.bytes ?? 0),
    status:
      row.status === "indexing"
        ? "processing"
        : (row.status as KnowledgeSource["status"]),
    errorMessage:
      typeof row.metadata.error_message === "string"
        ? row.metadata.error_message
        : null,
    createdAt: row.created_at,
    updatedAt: row.updated_at,
  };
}

export async function deleteKnowledgeSource(
  accessToken: string,
  sourceId: string,
): Promise<void> {
  await apiFetch<void>(`/admin/knowledge/sources/${sourceId}`, {
    accessToken,
    method: "DELETE",
  });
}

export async function testKnowledgeSearch(
  accessToken: string,
  knowledgeBaseId: string,
  query: string,
): Promise<KnowledgeSearchResult[]> {
  const rows = await apiFetch<
    Array<{
      id: string;
      content: string;
      score: number;
      source_id: string;
      metadata: Record<string, unknown>;
    }>
  >(`/admin/knowledge/bases/${knowledgeBaseId}/search`, {
    accessToken,
    method: "POST",
    body: { query, top_k: 6 },
  });
  return rows.map((row) => ({
    id: row.id,
    content: row.content,
    score: row.score,
    sourceId: row.source_id,
    metadata: row.metadata,
  }));
}

export async function listAdminSessions(
  accessToken: string,
  options: { limit?: number } = {},
): Promise<AdminSession[]> {
  const limit = options.limit ?? 100;
  const rows = await apiFetch<
    Array<{
      id: string;
      title: string;
      target_type: "agent" | "team" | "workflow";
      agent_config_id: string | null;
      agent_version_id: string | null;
      team_config_id: string | null;
      team_version_id: string | null;
      workflow_config_id: string | null;
      workflow_version_id: string | null;
      user_id: string;
      user_label?: string | null;
      last_run_id: string | null;
      status: AdminSession["status"];
      created_at: string;
      updated_at: string;
    }>
  >(
    `/api/sessions?all_users=true&limit=${encodeURIComponent(String(limit))}`,
    { accessToken },
  );
  return rows.map((row) => ({
    id: row.id,
    title: row.title,
    targetType: row.target_type,
    targetId:
      row.agent_config_id ??
      row.team_config_id ??
      row.workflow_config_id ??
      "",
    versionId:
      row.agent_version_id ??
      row.team_version_id ??
      row.workflow_version_id ??
      "",
    userId: row.user_id,
    userLabel: row.user_label ?? null,
    lastRunId: row.last_run_id,
    status: row.status,
    pausedForApproval: row.status === "paused",
    createdAt: row.created_at,
    updatedAt: row.updated_at,
  }));
}

export async function deleteAdminSession(
  accessToken: string,
  sessionId: string,
): Promise<void> {
  await apiFetch<void>(`/api/sessions/${encodeURIComponent(sessionId)}`, {
    accessToken,
    method: "DELETE",
  });
}

export async function getAdminSessionDetail(
  accessToken: string,
  sessionId: string,
): Promise<{ session: AdminSession; messages: ChatMessage[] }> {
  const row = await apiFetch<{
    id: string;
    title: string;
    target_type: "agent" | "team" | "workflow";
    agent_config_id: string | null;
    agent_version_id: string | null;
    team_config_id: string | null;
    team_version_id: string | null;
    workflow_config_id: string | null;
    workflow_version_id: string | null;
    user_id: string;
    user_label?: string | null;
    last_run_id: string | null;
    status: AdminSession["status"];
    created_at: string;
    updated_at: string;
    history?: { runs?: Array<Record<string, unknown>> };
  }>(`/api/sessions/${encodeURIComponent(sessionId)}`, { accessToken });

  const messages: ChatMessage[] = [];
  for (const [index, run] of (row.history?.runs ?? []).entries()) {
    const input = run.input;
    let userContent = "";
    if (typeof input === "string") {
      userContent = input;
    } else if (input && typeof input === "object") {
      const value = input as Record<string, unknown>;
      userContent = String(
        value.input_content ?? value.input ?? value.message ?? "",
      );
    }
    const createdAt = new Date(
      Number(run.created_at ?? 0) * 1000,
    ).toISOString();
    if (userContent) {
      messages.push({
        id: `${String(run.run_id ?? index)}:user`,
        role: "user",
        content: userContent,
        createdAt,
        status: "complete",
      });
    }
    const content = typeof run.content === "string" ? run.content : "";
    if (content) {
      messages.push({
        id: `${String(run.run_id ?? index)}:assistant`,
        role: "assistant",
        content,
        createdAt,
        status:
          String(run.status).toUpperCase() === "PAUSED"
            ? "paused"
            : "complete",
      });
    }
  }

  return {
    session: {
      id: row.id,
      title: row.title,
      targetType: row.target_type,
      targetId:
        row.agent_config_id ??
        row.team_config_id ??
        row.workflow_config_id ??
        "",
      versionId:
        row.agent_version_id ??
        row.team_version_id ??
        row.workflow_version_id ??
        "",
      userId: row.user_id,
      userLabel: row.user_label ?? null,
      lastRunId: row.last_run_id,
      status: row.status,
      pausedForApproval: row.status === "paused",
      createdAt: row.created_at,
      updatedAt: row.updated_at,
    },
    messages,
  };
}

export async function listUserMemories(
  accessToken: string,
  userId: string,
): Promise<UserMemory[]> {
  const rows = await apiFetch<Array<Record<string, unknown>>>(
    `/api/memories?user_id=${encodeURIComponent(userId)}`,
    { accessToken },
  );
  return rows.map((row) => ({
    id: String(row.memory_id ?? row.id ?? ""),
    userId,
    memory: String(row.memory ?? row.content ?? ""),
    topics: Array.isArray(row.topics)
      ? row.topics.map((topic) => String(topic))
      : [],
    updatedAt:
      typeof row.updated_at === "string"
        ? row.updated_at
        : typeof row.updated_at === "number"
          ? new Date(row.updated_at * 1000).toISOString()
          : null,
  }));
}

export async function createUserMemory(
  accessToken: string,
  input: { userId: string; memory: string; topics?: string[] },
): Promise<UserMemory> {
  const row = await apiFetch<Record<string, unknown>>("/api/memories", {
    accessToken,
    method: "POST",
    body: {
      user_id: input.userId,
      memory: input.memory,
      topics: input.topics ?? [],
    },
  });
  return {
    id: String(row.memory_id ?? row.id ?? ""),
    userId: input.userId,
    memory: String(row.memory ?? input.memory),
    topics: Array.isArray(row.topics)
      ? row.topics.map((topic) => String(topic))
      : input.topics ?? [],
    updatedAt: null,
  };
}

export async function updateUserMemory(
  accessToken: string,
  memoryId: string,
  input: { userId: string; memory: string; topics?: string[] },
): Promise<UserMemory> {
  const row = await apiFetch<Record<string, unknown>>(
    `/api/memories/${encodeURIComponent(memoryId)}`,
    {
      accessToken,
      method: "PATCH",
      body: {
        user_id: input.userId,
        memory: input.memory,
        topics: input.topics ?? [],
      },
    },
  );
  return {
    id: String(row.memory_id ?? memoryId),
    userId: input.userId,
    memory: String(row.memory ?? input.memory),
    topics: Array.isArray(row.topics)
      ? row.topics.map((topic) => String(topic))
      : input.topics ?? [],
    updatedAt: null,
  };
}

export async function optimizeUserMemories(
  accessToken: string,
  userId: string,
  apply = true,
): Promise<{
  memoriesBefore: number;
  memoriesAfter: number;
  memories: UserMemory[];
}> {
  const row = await apiFetch<{
    memories_before: number;
    memories_after: number;
    memories: Array<Record<string, unknown>>;
  }>("/api/memories/optimize", {
    accessToken,
    method: "POST",
    body: { user_id: userId, apply },
  });
  return {
    memoriesBefore: row.memories_before,
    memoriesAfter: row.memories_after,
    memories: (row.memories ?? []).map((item) => ({
      id: String(item.memory_id ?? item.id ?? ""),
      userId,
      memory: String(item.memory ?? ""),
      topics: Array.isArray(item.topics)
        ? item.topics.map((topic) => String(topic))
        : [],
      updatedAt: null,
    })),
  };
}

export async function deleteUserMemory(
  accessToken: string,
  memoryId: string,
  userId: string,
): Promise<void> {
  await apiFetch<void>(
    `/api/memories/${encodeURIComponent(memoryId)}?user_id=${encodeURIComponent(userId)}`,
    { accessToken, method: "DELETE" },
  );
}

export interface LearningRecord {
  learningId: string;
  learningType: string;
  namespace: string | null;
  userId: string | null;
  content: Record<string, unknown>;
  metadata: Record<string, unknown>;
  updatedAt: number | null;
}

export async function listLearnings(
  accessToken: string,
  opts?: { userId?: string; learningType?: string },
): Promise<LearningRecord[]> {
  const params = new URLSearchParams();
  if (opts?.userId) params.set("user_id", opts.userId);
  if (opts?.learningType) params.set("learning_type", opts.learningType);
  const query = params.toString();
  const row = await apiFetch<{
    data: Array<Record<string, unknown>>;
  }>(`/api/admin/learnings${query ? `?${query}` : ""}`, { accessToken });
  return (row.data ?? []).map((item) => ({
    learningId: String(item.learning_id ?? item.id ?? ""),
    learningType: String(item.learning_type ?? ""),
    namespace: item.namespace == null ? null : String(item.namespace),
    userId: item.user_id == null ? null : String(item.user_id),
    content:
      item.content && typeof item.content === "object"
        ? (item.content as Record<string, unknown>)
        : {},
    metadata:
      item.metadata && typeof item.metadata === "object"
        ? (item.metadata as Record<string, unknown>)
        : {},
    updatedAt: typeof item.updated_at === "number" ? item.updated_at : null,
  }));
}

export async function createLearning(
  accessToken: string,
  input: {
    learningType: string;
    content: Record<string, unknown>;
    userId?: string;
    namespace?: string;
  },
): Promise<LearningRecord> {
  const row = await apiFetch<Record<string, unknown>>("/api/admin/learnings", {
    accessToken,
    method: "POST",
    body: {
      learning_type: input.learningType,
      content: input.content,
      user_id: input.userId,
      namespace: input.namespace,
    },
  });
  return {
    learningId: String(row.learning_id ?? ""),
    learningType: String(row.learning_type ?? input.learningType),
    namespace: row.namespace == null ? null : String(row.namespace),
    userId: row.user_id == null ? null : String(row.user_id),
    content:
      row.content && typeof row.content === "object"
        ? (row.content as Record<string, unknown>)
        : input.content,
    metadata: {},
    updatedAt: null,
  };
}

export async function deleteLearning(
  accessToken: string,
  learningId: string,
): Promise<void> {
  await apiFetch<void>(`/api/admin/learnings/${encodeURIComponent(learningId)}`, {
    accessToken,
    method: "DELETE",
  });
}

export interface CredentialSummary {
  id: string;
  name: string;
  provider: string;
  keyVersion: string;
  createdAt: string;
}

export interface ToolkitCatalogEntry {
  key: string;
  label: string;
  category: string;
  description: string;
  module: string;
  class_name: string;
  tier: "safe" | "credential" | "blocked";
  credentials: Array<{ env_var: string; provider: string; kwarg: string }>;
  options: Record<
    string,
    {
      type: "integer" | "string" | "boolean";
      minimum?: number;
      maximum?: number;
      default?: unknown;
    }
  >;
  side_effects: boolean;
  install_hint: string | null;
  disabled_reason: string | null;
  available: boolean;
  status: "ready" | "needs_credential" | "unavailable" | "blocked";
  unavailable_reason: string | null;
  exposed: boolean;
}

export function listToolkitCatalog(
  accessToken: string,
): Promise<ToolkitCatalogEntry[]> {
  return apiFetch<ToolkitCatalogEntry[]>("/admin/tools/toolkits", {
    accessToken,
  });
}

export interface CustomPythonCatalogEntry {
  key: string;
  label: string;
  category: string;
  description: string;
  credential_provider: string | null;
  credential_label: string | null;
  settings_schema: {
    properties?: Record<
      string,
      {
        type?: string;
        title?: string;
        default?: unknown;
        maxLength?: number;
        pattern?: string;
      }
    >;
    required?: string[];
  };
  capabilities: Array<{
    name: string;
    description: string;
    input_schema: Record<string, unknown>;
    mutating: boolean;
  }>;
}

export function listCustomPythonCatalog(
  accessToken: string,
): Promise<CustomPythonCatalogEntry[]> {
  return apiFetch<CustomPythonCatalogEntry[]>("/admin/tools/custom-python", {
    accessToken,
  });
}

export async function listCredentials(
  accessToken: string,
): Promise<CredentialSummary[]> {
  const rows = await apiFetch<
    Array<{
      id: string;
      name: string;
      provider: CredentialSummary["provider"];
      key_version: string;
      created_at: string;
    }>
  >("/admin/credentials", { accessToken });
  return rows.map((row) => ({
    id: row.id,
    name: row.name,
    provider: row.provider,
    keyVersion: row.key_version,
    createdAt: row.created_at,
  }));
}

export type UserVaultKind = "secret" | "variable";

export type UserVaultEntry = {
  name: string;
  kind: UserVaultKind;
  updatedAt: string;
};

export async function listUserVault(
  accessToken: string,
): Promise<UserVaultEntry[]> {
  const rows = await apiFetch<
    Array<{ name: string; kind: UserVaultKind; updated_at: string }>
  >("/api/me/vault", { accessToken });
  return rows.map((row) => ({
    name: row.name,
    kind: row.kind,
    updatedAt: row.updated_at,
  }));
}

export async function upsertUserVaultEntry(
  accessToken: string,
  name: string,
  input: { value: string; kind: UserVaultKind },
): Promise<UserVaultEntry> {
  const row = await apiFetch<{
    name: string;
    kind: UserVaultKind;
    updated_at: string;
  }>(`/api/me/vault/${encodeURIComponent(name)}`, {
    accessToken,
    method: "PUT",
    body: input,
  });
  return {
    name: row.name,
    kind: row.kind,
    updatedAt: row.updated_at,
  };
}

export async function deleteUserVaultEntry(
  accessToken: string,
  name: string,
): Promise<void> {
  await apiFetch<void>(`/api/me/vault/${encodeURIComponent(name)}`, {
    accessToken,
    method: "DELETE",
  });
}

export async function sendOrgNotification(
  accessToken: string,
  input: { title: string; body: string; userId?: string | null },
): Promise<NotificationSendResult> {
  const row = await apiFetch<{
    batch_id: string;
    audience: NotificationAudience;
    recipient_count: number;
    title: string;
  }>("/admin/notifications", {
    accessToken,
    method: "POST",
    body: {
      title: input.title,
      body: input.body,
      user_id: input.userId ?? null,
    },
  });
  return {
    batchId: row.batch_id,
    audience: row.audience,
    recipientCount: row.recipient_count,
    title: row.title,
  };
}

export async function listSentNotifications(
  accessToken: string,
): Promise<NotificationBatch[]> {
  const rows = await apiFetch<
    Array<{
      batch_id: string;
      title: string;
      body: string;
      audience: NotificationAudience;
      created_by: string;
      recipient_count: number;
      created_at: string;
    }>
  >("/admin/notifications", { accessToken });
  return rows.map((row) => ({
    batchId: row.batch_id,
    title: row.title,
    body: row.body,
    audience: row.audience,
    createdBy: row.created_by,
    recipientCount: row.recipient_count,
    createdAt: row.created_at,
  }));
}

export async function listMyNotifications(
  accessToken: string,
  opts?: { unreadOnly?: boolean; limit?: number },
): Promise<UserNotification[]> {
  const params = new URLSearchParams();
  if (opts?.unreadOnly) params.set("unread_only", "true");
  if (opts?.limit != null) params.set("limit", String(opts.limit));
  const qs = params.toString();
  const rows = await apiFetch<
    Array<{
      id: string;
      batch_id: string;
      title: string;
      body: string;
      audience: NotificationAudience;
      created_by: string;
      read_at: string | null;
      created_at: string;
    }>
  >(`/api/me/notifications${qs ? `?${qs}` : ""}`, { accessToken });
  return rows.map((row) => ({
    id: row.id,
    batchId: row.batch_id,
    title: row.title,
    body: row.body,
    audience: row.audience,
    createdBy: row.created_by,
    readAt: row.read_at,
    createdAt: row.created_at,
  }));
}

export async function getMyUnreadNotificationCount(
  accessToken: string,
): Promise<number> {
  const row = await apiFetch<{ count: number }>(
    "/api/me/notifications/unread-count",
    { accessToken },
  );
  return row.count;
}

export async function markNotificationRead(
  accessToken: string,
  notificationId: string,
): Promise<UserNotification> {
  const row = await apiFetch<{
    id: string;
    batch_id: string;
    title: string;
    body: string;
    audience: NotificationAudience;
    created_by: string;
    read_at: string | null;
    created_at: string;
  }>(`/api/me/notifications/${notificationId}/read`, {
    accessToken,
    method: "POST",
  });
  return {
    id: row.id,
    batchId: row.batch_id,
    title: row.title,
    body: row.body,
    audience: row.audience,
    createdBy: row.created_by,
    readAt: row.read_at,
    createdAt: row.created_at,
  };
}

export async function markAllNotificationsRead(
  accessToken: string,
): Promise<number> {
  const row = await apiFetch<{ updated: number }>(
    "/api/me/notifications/read-all",
    { accessToken, method: "POST" },
  );
  return row.updated;
}

export type AdminVaultTarget = {
  userId: string;
  displayName: string;
  email: string;
};

export async function listAdminVaultTargets(
  accessToken: string,
): Promise<AdminVaultTarget[]> {
  const rows = await apiFetch<
    Array<{ user_id: string; display_name: string; email: string }>
  >("/admin/vault/users", { accessToken });
  return rows.map((row) => ({
    userId: row.user_id,
    displayName: row.display_name,
    email: row.email,
  }));
}

export async function listAdminUserVault(
  accessToken: string,
  userId: string,
): Promise<UserVaultEntry[]> {
  const rows = await apiFetch<
    Array<{ name: string; kind: UserVaultKind; updated_at: string }>
  >(`/admin/vault/users/${encodeURIComponent(userId)}`, { accessToken });
  return rows.map((row) => ({
    name: row.name,
    kind: row.kind,
    updatedAt: row.updated_at,
  }));
}

export async function upsertAdminUserVaultEntry(
  accessToken: string,
  userId: string,
  name: string,
  input: { value: string; kind: UserVaultKind },
): Promise<UserVaultEntry> {
  const row = await apiFetch<{
    name: string;
    kind: UserVaultKind;
    updated_at: string;
  }>(
    `/admin/vault/users/${encodeURIComponent(userId)}/${encodeURIComponent(name)}`,
    {
      accessToken,
      method: "PUT",
      body: input,
    },
  );
  return {
    name: row.name,
    kind: row.kind,
    updatedAt: row.updated_at,
  };
}

export async function deleteAdminUserVaultEntry(
  accessToken: string,
  userId: string,
  name: string,
): Promise<void> {
  await apiFetch<void>(
    `/admin/vault/users/${encodeURIComponent(userId)}/${encodeURIComponent(name)}`,
    {
      accessToken,
      method: "DELETE",
    },
  );
}

export async function createCredential(
  accessToken: string,
  input: { name: string; provider: CredentialSummary["provider"]; value: string },
): Promise<CredentialSummary> {
  const row = await apiFetch<{
    id: string;
    name: string;
    provider: CredentialSummary["provider"];
    key_version: string;
    created_at: string;
  }>("/admin/credentials", {
    accessToken,
    method: "POST",
    body: input,
  });
  return {
    id: row.id,
    name: row.name,
    provider: row.provider,
    keyVersion: row.key_version,
    createdAt: row.created_at,
  };
}

export type ChannelProvider = "slack" | "telegram" | "whatsapp";

export interface ChannelBinding {
  id: string;
  provider: ChannelProvider;
  credentialId: string;
  targetType: "team" | "workflow";
  targetConfigId: string;
  externalConfig: Record<string, unknown>;
  active: boolean;
  createdAt: string;
  updatedAt: string;
}

export async function listChannelBindings(
  accessToken: string,
): Promise<ChannelBinding[]> {
  const rows = await apiFetch<
    Array<{
      id: string;
      provider: ChannelProvider;
      credential_id: string;
      target_type: "team" | "workflow";
      target_config_id: string;
      external_config: Record<string, unknown>;
      active: boolean;
      created_at: string;
      updated_at: string;
    }>
  >("/admin/channels", { accessToken });
  return rows.map((row) => ({
    id: row.id,
    provider: row.provider,
    credentialId: row.credential_id,
    targetType: row.target_type,
    targetConfigId: row.target_config_id,
    externalConfig: row.external_config ?? {},
    active: row.active,
    createdAt: row.created_at,
    updatedAt: row.updated_at,
  }));
}

export async function createChannelBinding(
  accessToken: string,
  input: {
    provider: ChannelProvider;
    credentialId: string;
    targetType: "team" | "workflow";
    targetConfigId: string;
    externalConfig?: Record<string, unknown>;
    active?: boolean;
  },
): Promise<ChannelBinding> {
  const row = await apiFetch<{
    id: string;
    provider: ChannelProvider;
    credential_id: string;
    target_type: "team" | "workflow";
    target_config_id: string;
    external_config: Record<string, unknown>;
    active: boolean;
    created_at: string;
    updated_at: string;
  }>("/admin/channels", {
    accessToken,
    method: "POST",
    body: {
      provider: input.provider,
      credential_id: input.credentialId,
      target_type: input.targetType,
      target_config_id: input.targetConfigId,
      external_config: input.externalConfig ?? {},
      active: input.active ?? true,
    },
  });
  return {
    id: row.id,
    provider: row.provider,
    credentialId: row.credential_id,
    targetType: row.target_type,
    targetConfigId: row.target_config_id,
    externalConfig: row.external_config ?? {},
    active: row.active,
    createdAt: row.created_at,
    updatedAt: row.updated_at,
  };
}

export async function deleteChannelBinding(
  accessToken: string,
  bindingId: string,
): Promise<void> {
  await apiFetch<void>(`/admin/channels/${bindingId}`, {
    accessToken,
    method: "DELETE",
  });
}

export async function deleteCredential(
  accessToken: string,
  credentialId: string,
): Promise<void> {
  await apiFetch<void>(`/admin/credentials/${credentialId}`, {
    accessToken,
    method: "DELETE",
  });
}

export interface ServiceAccountSummary {
  id: string;
  name: string;
  tokenPrefix: string;
  scopes: string[];
  createdBy: string;
  lastUsedAt: string | null;
  expiresAt: string | null;
  revokedAt: string | null;
  createdAt: string;
}

interface BackendServiceAccount {
  id: string;
  name: string;
  token_prefix: string;
  scopes: string[];
  created_by: string;
  last_used_at: string | null;
  expires_at: string | null;
  revoked_at: string | null;
  created_at: string;
}

function mapServiceAccount(row: BackendServiceAccount): ServiceAccountSummary {
  return {
    id: row.id,
    name: row.name,
    tokenPrefix: row.token_prefix,
    scopes: row.scopes,
    createdBy: row.created_by,
    lastUsedAt: row.last_used_at,
    expiresAt: row.expires_at,
    revokedAt: row.revoked_at,
    createdAt: row.created_at,
  };
}

export async function listServiceAccounts(
  accessToken: string,
): Promise<ServiceAccountSummary[]> {
  const rows = await apiFetch<BackendServiceAccount[]>(
    "/admin/service-accounts",
    { accessToken },
  );
  return rows.map(mapServiceAccount);
}

export async function getServiceAccount(
  accessToken: string,
  accountId: string,
): Promise<ServiceAccountSummary> {
  return mapServiceAccount(
    await apiFetch<BackendServiceAccount>(
      `/admin/service-accounts/${accountId}`,
      { accessToken },
    ),
  );
}

export async function createServiceAccount(
  accessToken: string,
  input: { name: string; scopes: string[]; expiresAt: string | null },
): Promise<ServiceAccountSummary & { token: string }> {
  const row = await apiFetch<BackendServiceAccount & { token: string }>(
    "/admin/service-accounts",
    {
      accessToken,
      method: "POST",
      body: {
        name: input.name,
        scopes: input.scopes,
        expires_at: input.expiresAt,
      },
    },
  );
  return { ...mapServiceAccount(row), token: row.token };
}

export async function revokeServiceAccount(
  accessToken: string,
  accountId: string,
): Promise<void> {
  await apiFetch<void>(`/admin/service-accounts/${accountId}`, {
    accessToken,
    method: "DELETE",
  });
}

export interface TraceSummary {
  id: string;
  runId: string | null;
  sessionId: string;
  targetType: "agent" | "team" | "workflow";
  targetId: string;
  versionId: string;
  userId: string;
  name: string;
  status: "running" | "completed" | "paused" | "error" | "cancelled";
  startedAt: string;
  endedAt: string | null;
  durationMs: number | null;
  spanCount: number;
}

export interface TraceSpan {
  id: string;
  parentSpanId: string | null;
  name: string;
  kind: string;
  status: string;
  sequence: number;
  attributes: Record<string, unknown>;
  input: Record<string, unknown>;
  output: Record<string, unknown>;
  error: string | null;
  startedAt: string;
  endedAt: string | null;
  durationMs: number | null;
}

export interface TraceDetail extends TraceSummary {
  input: Record<string, unknown>;
  output: Record<string, unknown>;
  metadata: Record<string, unknown>;
  spans: TraceSpan[];
}

interface BackendTraceSummary {
  id: string;
  run_id: string | null;
  session_id: string;
  target_type: TraceSummary["targetType"];
  target_id: string;
  version_id: string;
  user_id: string;
  name: string;
  status: TraceSummary["status"];
  started_at: string;
  ended_at: string | null;
  duration_ms: number | null;
  span_count: number;
}

function mapTrace(row: BackendTraceSummary): TraceSummary {
  return {
    id: row.id,
    runId: row.run_id,
    sessionId: row.session_id,
    targetType: row.target_type,
    targetId: row.target_id,
    versionId: row.version_id,
    userId: row.user_id,
    name: row.name,
    status: row.status,
    startedAt: row.started_at,
    endedAt: row.ended_at,
    durationMs: row.duration_ms,
    spanCount: row.span_count,
  };
}

export async function listTraces(
  accessToken: string,
  options?: { sessionId?: string; limit?: number },
): Promise<TraceSummary[]> {
  const params = new URLSearchParams();
  if (options?.sessionId) params.set("session_id", options.sessionId);
  if (options?.limit) params.set("limit", String(options.limit));
  const query = params.toString();
  const rows = await apiFetch<BackendTraceSummary[]>(
    `/api/admin/traces${query ? `?${query}` : ""}`,
    { accessToken },
  );
  return rows.map(mapTrace);
}

export async function getTrace(
  accessToken: string,
  traceId: string,
): Promise<TraceDetail> {
  const row = await apiFetch<
    BackendTraceSummary & {
      input: Record<string, unknown>;
      output: Record<string, unknown>;
      metadata: Record<string, unknown>;
      spans: Array<{
        id: string;
        parent_span_id: string | null;
        name: string;
        kind: string;
        status: string;
        sequence: number;
        attributes: Record<string, unknown>;
        input: Record<string, unknown>;
        output: Record<string, unknown>;
        error: string | null;
        started_at: string;
        ended_at: string | null;
        duration_ms: number | null;
      }>;
    }
  >(`/api/admin/traces/${traceId}`, { accessToken });
  return {
    ...mapTrace(row),
    input: row.input,
    output: row.output,
    metadata: row.metadata,
    spans: row.spans.map((span) => ({
      id: span.id,
      parentSpanId: span.parent_span_id,
      name: span.name,
      kind: span.kind,
      status: span.status,
      sequence: span.sequence,
      attributes: span.attributes,
      input: span.input,
      output: span.output,
      error: span.error,
      startedAt: span.started_at,
      endedAt: span.ended_at,
      durationMs: span.duration_ms,
    })),
  };
}

export type UserTraceSummary = TraceSummary & {
  sessionTitle: string | null;
  targetName: string | null;
  error: string | null;
};

export type UserTraceDetail = TraceDetail & {
  sessionTitle: string | null;
  targetName: string | null;
  error: string | null;
};

interface BackendUserTraceSummary extends BackendTraceSummary {
  session_title: string | null;
  target_name: string | null;
  error: string | null;
}

function mapUserTrace(row: BackendUserTraceSummary): UserTraceSummary {
  return {
    ...mapTrace(row),
    sessionTitle: row.session_title,
    targetName: row.target_name,
    error: row.error,
  };
}

export async function listMyTraces(
  accessToken: string,
  options?: { limit?: number },
): Promise<UserTraceSummary[]> {
  const params = new URLSearchParams();
  if (options?.limit) params.set("limit", String(options.limit));
  const query = params.toString();
  const rows = await apiFetch<BackendUserTraceSummary[]>(
    `/api/me/traces${query ? `?${query}` : ""}`,
    { accessToken },
  );
  return rows.map(mapUserTrace);
}

export async function getMyTrace(
  accessToken: string,
  traceId: string,
): Promise<UserTraceDetail> {
  const row = await apiFetch<
    BackendUserTraceSummary & {
      input: Record<string, unknown>;
      output: Record<string, unknown>;
      metadata: Record<string, unknown>;
      spans: Array<{
        id: string;
        parent_span_id: string | null;
        name: string;
        kind: string;
        status: string;
        sequence: number;
        attributes: Record<string, unknown>;
        input: Record<string, unknown>;
        output: Record<string, unknown>;
        error: string | null;
        started_at: string;
        ended_at: string | null;
        duration_ms: number | null;
      }>;
    }
  >(`/api/me/traces/${traceId}`, { accessToken });
  return {
    ...mapUserTrace(row),
    input: row.input,
    output: row.output,
    metadata: row.metadata,
    spans: row.spans.map((span) => ({
      id: span.id,
      parentSpanId: span.parent_span_id,
      name: span.name,
      kind: span.kind,
      status: span.status,
      sequence: span.sequence,
      attributes: span.attributes,
      input: span.input,
      output: span.output,
      error: span.error,
      startedAt: span.started_at,
      endedAt: span.ended_at,
      durationMs: span.duration_ms,
    })),
  };
}

interface BackendToolDefinition {
  id: string;
  name: string;
  slug: string;
  description: string | null;
  kind: ToolDefinition["kind"];
  http_method: ToolDefinition["httpMethod"];
  base_url: string | null;
  path: string | null;
  request_schema: Record<string, unknown>;
  response_description: string | null;
  response_schema: Record<string, unknown> | null;
  headers: Record<string, string>;
  config: Record<string, unknown>;
  credential_id: string | null;
  approval_required: boolean;
  active: boolean;
  connection_status: ToolDefinition["connectionStatus"];
  last_validated_at: string | null;
  last_validation_error: string | null;
  published_version_id?: string | null;
  created_at: string;
  updated_at: string;
}

function mapToolDefinition(row: BackendToolDefinition): ToolDefinition {
  return {
    id: row.id,
    name: row.name,
    slug: row.slug,
    description: row.description ?? "",
    kind: row.kind,
    httpMethod: row.http_method ?? null,
    baseUrl: row.base_url ?? null,
    path: row.path ?? null,
    requestSchema: row.request_schema,
    responseDescription: row.response_description ?? "",
    responseSchema: row.response_schema,
    headers: row.headers,
    config: row.config,
    credentialId: row.credential_id,
    approvalRequired: row.approval_required,
    active: row.active,
    connectionStatus: row.connection_status,
    lastValidatedAt: row.last_validated_at,
    lastValidationError: row.last_validation_error,
    publishedVersionId: row.published_version_id ?? null,
    createdAt: row.created_at,
    updatedAt: row.updated_at,
  };
}

function backendToolDefinition(input: Omit<ToolDefinition, "id" | "createdAt" | "updatedAt">) {
  return {
    name: input.name,
    slug: input.slug,
    description: input.description || null,
    kind: input.kind,
    http_method: input.httpMethod,
    base_url: input.baseUrl,
    path: input.path,
    request_schema: input.requestSchema,
    response_description: input.responseDescription || null,
    response_schema: input.responseSchema,
    headers: input.headers,
    config: input.config,
    credential_id: input.credentialId,
    approval_required: input.approvalRequired,
    active: input.active,
  };
}

function mapToolValidation(row: {
  ok: boolean;
  message: string;
  capabilities: Array<{
    name: string;
    description: string;
    approval_required: boolean;
    input_schema: Record<string, unknown>;
  }>;
}): ToolValidation {
  return {
    ok: row.ok,
    message: row.message,
    capabilities: row.capabilities.map(
      (capability): ToolCapability => ({
        name: capability.name,
        description: capability.description,
        approvalRequired: capability.approval_required,
        inputSchema: capability.input_schema,
      }),
    ),
  };
}

export async function listToolDefinitions(accessToken: string): Promise<ToolDefinition[]> {
  const rows = await apiFetch<BackendToolDefinition[]>("/admin/tools", { accessToken });
  return rows.map(mapToolDefinition);
}

export async function getToolDefinition(
  accessToken: string,
  toolId: string,
): Promise<ToolDefinition> {
  return mapToolDefinition(
    await apiFetch<BackendToolDefinition>(`/admin/tools/${toolId}`, { accessToken }),
  );
}

export async function createToolDefinition(
  accessToken: string,
  input: Omit<ToolDefinition, "id" | "createdAt" | "updatedAt">,
): Promise<ToolDefinition> {
  return mapToolDefinition(
    await apiFetch<BackendToolDefinition>("/admin/tools", {
      accessToken,
      method: "POST",
      body: backendToolDefinition(input),
    }),
  );
}

export async function updateToolDefinition(
  accessToken: string,
  toolId: string,
  input: Omit<ToolDefinition, "id" | "createdAt" | "updatedAt">,
): Promise<ToolDefinition> {
  return mapToolDefinition(
    await apiFetch<BackendToolDefinition>(`/admin/tools/${toolId}`, {
      accessToken,
      method: "PATCH",
      body: backendToolDefinition(input),
    }),
  );
}

export async function deleteToolDefinition(
  accessToken: string,
  toolId: string,
): Promise<void> {
  await apiFetch<void>(`/admin/tools/${toolId}`, {
    accessToken,
    method: "DELETE",
  });
}

export async function cloneToolDefinition(
  accessToken: string,
  toolId: string,
): Promise<ToolDefinition> {
  return mapToolDefinition(
    await apiFetch<BackendToolDefinition>(`/admin/tools/${toolId}/clone`, {
      accessToken,
      method: "POST",
    }),
  );
}

export async function testToolDefinition(
  accessToken: string,
  toolId: string,
): Promise<ToolValidation> {
  return mapToolValidation(
    await apiFetch<Parameters<typeof mapToolValidation>[0]>(
      `/admin/tools/${toolId}/test`,
      { accessToken, method: "POST" },
    ),
  );
}

export async function enumerateToolCapabilities(
  accessToken: string,
  toolId: string,
): Promise<ToolValidation> {
  return mapToolValidation(
    await apiFetch<Parameters<typeof mapToolValidation>[0]>(
      `/admin/tools/${toolId}/capabilities`,
      { accessToken },
    ),
  );
}

export async function validateToolDefinition(
  accessToken: string,
  input: Omit<ToolDefinition, "id" | "createdAt" | "updatedAt">,
): Promise<ToolValidation> {
  return mapToolValidation(
    await apiFetch<Parameters<typeof mapToolValidation>[0]>("/admin/tools/validate", {
      accessToken,
      method: "POST",
      body: backendToolDefinition(input),
    }),
  );
}

export async function validateTenantPythonSource(
  accessToken: string,
  toolId: string,
): Promise<ToolValidation> {
  return mapToolValidation(
    await apiFetch<Parameters<typeof mapToolValidation>[0]>(
      `/admin/tools/${toolId}/validate-source`,
      { accessToken, method: "POST" },
    ),
  );
}

export async function publishTenantPythonTool(
  accessToken: string,
  toolId: string,
): Promise<ToolDefinition> {
  return mapToolDefinition(
    await apiFetch<BackendToolDefinition>(`/admin/tools/${toolId}/publish`, {
      accessToken,
      method: "POST",
    }),
  );
}

export type ToolDefinitionVersion = {
  id: string;
  toolDefinitionId: string;
  version: number;
  status: string;
  sourceCode: string;
  dependencies: Array<Record<string, unknown>>;
  capabilities: Array<Record<string, unknown>>;
  settings: Record<string, unknown>;
  publishedAt: string | null;
  createdAt: string;
  updatedAt: string;
};

function mapToolDefinitionVersion(row: {
  id: string;
  tool_definition_id: string;
  version: number;
  status: string;
  source_code: string;
  dependencies: Array<Record<string, unknown>>;
  capabilities: Array<Record<string, unknown>>;
  settings: Record<string, unknown>;
  published_at: string | null;
  created_at: string;
  updated_at: string;
}): ToolDefinitionVersion {
  return {
    id: row.id,
    toolDefinitionId: row.tool_definition_id,
    version: row.version,
    status: row.status,
    sourceCode: row.source_code,
    dependencies: row.dependencies,
    capabilities: row.capabilities,
    settings: row.settings,
    publishedAt: row.published_at,
    createdAt: row.created_at,
    updatedAt: row.updated_at,
  };
}

export async function listToolVersions(
  accessToken: string,
  toolId: string,
): Promise<ToolDefinitionVersion[]> {
  const rows = await apiFetch<
    Array<Parameters<typeof mapToolDefinitionVersion>[0]>
  >(`/admin/tools/${toolId}/versions`, { accessToken });
  return rows.map(mapToolDefinitionVersion);
}

export async function getToolVersion(
  accessToken: string,
  toolId: string,
  versionId: string,
): Promise<ToolDefinitionVersion> {
  return mapToolDefinitionVersion(
    await apiFetch<Parameters<typeof mapToolDefinitionVersion>[0]>(
      `/admin/tools/${toolId}/versions/${versionId}`,
      { accessToken },
    ),
  );
}

export async function restoreToolVersion(
  accessToken: string,
  toolId: string,
  versionId: string,
  options: { asDraft?: boolean } = {},
): Promise<ToolDefinition> {
  return mapToolDefinition(
    await apiFetch<BackendToolDefinition>(
      `/admin/tools/${toolId}/versions/${versionId}/restore`,
      {
        accessToken,
        method: "POST",
        body: { as_draft: options.asDraft ?? false },
      },
    ),
  );
}

export async function listSandboxPackages(
  accessToken: string,
): Promise<import("./types").SandboxPythonPackage[]> {
  const rows = await apiFetch<
    Array<{
      id: string;
      name: string;
      version: string;
      sha256: string;
      active: boolean;
      created_at: string;
      updated_at: string;
    }>
  >("/admin/tools/sandbox-packages", { accessToken });
  return rows.map((row) => ({
    id: row.id,
    name: row.name,
    version: row.version,
    sha256: row.sha256,
    active: row.active,
    createdAt: row.created_at,
    updatedAt: row.updated_at,
  }));
}

export async function listTenantPythonTemplates(
  accessToken: string,
): Promise<import("./types").TenantPythonTemplate[]> {
  return apiFetch("/admin/tools/tenant-python/templates", { accessToken });
}

export async function listPlatformSandboxPackages(
  accessToken: string,
  activeOnly = false,
): Promise<import("./types").SandboxPythonPackage[]> {
  const query = activeOnly ? "?active_only=true" : "";
  const rows = await apiFetch<
    Array<{
      id: string;
      name: string;
      version: string;
      sha256: string;
      active: boolean;
      created_at: string;
      updated_at: string;
    }>
  >(`/admin/platform/sandbox-packages${query}`, { accessToken });
  return rows.map((row) => ({
    id: row.id,
    name: row.name,
    version: row.version,
    sha256: row.sha256,
    active: row.active,
    createdAt: row.created_at,
    updatedAt: row.updated_at,
  }));
}

export async function createPlatformSandboxPackage(
  accessToken: string,
  input: { name: string; version: string; sha256: string; active?: boolean },
): Promise<import("./types").SandboxPythonPackage> {
  const row = await apiFetch<{
    id: string;
    name: string;
    version: string;
    sha256: string;
    active: boolean;
    created_at: string;
    updated_at: string;
  }>("/admin/platform/sandbox-packages", {
    accessToken,
    method: "POST",
    body: {
      name: input.name,
      version: input.version,
      sha256: input.sha256,
      active: input.active ?? true,
    },
  });
  return {
    id: row.id,
    name: row.name,
    version: row.version,
    sha256: row.sha256,
    active: row.active,
    createdAt: row.created_at,
    updatedAt: row.updated_at,
  };
}

export async function updatePlatformSandboxPackage(
  accessToken: string,
  packageId: string,
  input: { sha256?: string; active?: boolean },
): Promise<import("./types").SandboxPythonPackage> {
  const row = await apiFetch<{
    id: string;
    name: string;
    version: string;
    sha256: string;
    active: boolean;
    created_at: string;
    updated_at: string;
  }>(`/admin/platform/sandbox-packages/${packageId}`, {
    accessToken,
    method: "PATCH",
    body: input,
  });
  return {
    id: row.id,
    name: row.name,
    version: row.version,
    sha256: row.sha256,
    active: row.active,
    createdAt: row.created_at,
    updatedAt: row.updated_at,
  };
}

export async function getPublicTenantBranding(
  tenantSlug: string,
): Promise<TenantBranding> {
  const result = await apiFetch<{
    slug: string;
    name: string;
    domain?: string;
    branding: Record<string, unknown>;
  }>(`/public/tenants/${tenantSlug}`, { accessToken: "public" });
  return {
    name: result.name,
    slug: result.slug,
    domain: result.domain || "generic",
    primaryColor:
      typeof result.branding.primaryColor === "string"
        ? result.branding.primaryColor
        : "#0f766e",
    accentColor:
      typeof result.branding.accentColor === "string"
        ? result.branding.accentColor
        : "#5eead4",
    logoUrl:
      typeof result.branding.logoUrl === "string"
        ? result.branding.logoUrl
        : null,
    tagline:
      typeof result.branding.tagline === "string"
        ? result.branding.tagline
        : null,
  };
}

export function getPublicTeamChatSurface(
  tenantSlug: string,
  teamSlug: string,
): Promise<PublicTeamSurface> {
  return apiFetch<PublicTeamSurface>(
    `/public/t/${tenantSlug}/teams/${teamSlug}`,
    { accessToken: "public" },
  );
}

export function getPublicWorkflowSurface(
  tenantSlug: string,
  workflowSlug: string,
): Promise<PublicWorkflowSurface> {
  return apiFetch<PublicWorkflowSurface>(
    `/public/t/${tenantSlug}/workflows/${workflowSlug}`,
    { accessToken: "public" },
  );
}

export interface WorkspaceInfo {
  id: string;
  name: string;
  slug: string;
  domain?: string;
  branding: Record<string, unknown>;
  email_inbound_domain?: string | null;
  user_id?: string;
  role?: "platform_admin" | "tenant_admin" | "end_user";
  can_administer?: boolean;
  timezone?: string;
  tenant_timezone?: string;
}

export async function getWorkspaceInfo(
  accessToken: string,
): Promise<WorkspaceInfo> {
  return apiFetch<WorkspaceInfo>("/admin/workspace", { accessToken });
}

export interface OnboardingStatus {
  provisioned: boolean;
  can_create: boolean;
  org_id: string | null;
  org_role: string | null;
  tenant_id: string | null;
  tenant_slug: string | null;
  tenant_name: string | null;
}

export async function getOnboardingStatus(
  accessToken: string,
): Promise<OnboardingStatus> {
  return apiFetch<OnboardingStatus>("/admin/onboarding/status", {
    accessToken,
  });
}

export async function createSelfServeWorkspace(
  accessToken: string,
  input: { name: string; slug: string; domain?: WorkspaceDomain },
): Promise<WorkspaceInfo & { auth_org_id: string; is_active: boolean; domain: string }> {
  return apiFetch("/admin/onboarding/workspace", {
    accessToken,
    method: "POST",
    body: input,
  });
}

export interface DomainDashboardWidget {
  id: string;
  label: string;
  value: string;
  hint: string;
  group?: string;
  kind?: string;
  columns?: string[];
  rows?: Array<Record<string, string | number | boolean | null>>;
}

export interface DomainDeskBook {
  id: string;
  label: string;
  tab: string;
  via: string | null;
  team_slug: string | null;
  source: string | null;
  columns: string[];
  rows: Array<Record<string, string | number | boolean | null>>;
  error: string | null;
  empty_hint: string;
}

export interface DomainChatTarget {
  id: string;
  slug: string;
  name: string;
  published: boolean;
}

export interface DomainBrokerTool {
  id: string;
  slug: string;
  name: string;
  kind: string;
  active: boolean;
  published: boolean;
  connection_status: string;
  via_team?: string;
  via_team_name?: string;
  via_agent?: string | null;
}

export interface DomainDashboard {
  domain: WorkspaceDomain;
  domain_label: string;
  range_days: number;
  fetched_at: string;
  widgets: DomainDashboardWidget[];
  quick_links: Array<{ label: string; href: string }>;
  chat_targets: DomainChatTarget[];
  broker_tools: DomainBrokerTool[];
  desk_snapshot?: {
    team: { id: string; slug: string; name: string } | null;
    tools: DomainBrokerTool[];
    widgets: DomainDashboardWidget[];
    books?: DomainDeskBook[];
    error: string | null;
  } | null;
  metrics: MetricsDashboard;
  catalog: {
    agents: number;
    teams: number;
    workflows: number;
    published_agents: number;
    published_teams: number;
    published_workflows: number;
    pending_approvals: number;
    team_slugs: string[];
    workflow_slugs: string[];
    teams_detail?: DomainChatTarget[];
  };
}

export async function listWorkspaceDomains(
  accessToken: string,
): Promise<WorkspaceDomainOption[]> {
  return apiFetch<WorkspaceDomainOption[]>("/admin/onboarding/domains", {
    accessToken,
  });
}

export async function getDomainDashboard(
  accessToken: string,
  days = 30,
  deskSnapshot = false,
): Promise<DomainDashboard> {
  const params = new URLSearchParams({
    days: String(days),
    desk_snapshot: deskSnapshot ? "true" : "false",
  });
  return apiFetch<DomainDashboard>(`/admin/domains/dashboard?${params}`, {
    accessToken,
  });
}

export interface SignalMetricRow {
  id: string;
  check_no?: number;
  category?: string;
  label: string;
  value: number | null;
  target: string;
  rule: string;
  tier: string;
  passed: boolean | null;
  gates_entry?: boolean;
  hint?: string;
}

export type SignalEntryStatus = "ready" | "blocked" | "waiting";

export interface SignalEntry {
  side: string;
  atm: number | null;
  ce: number;
  pe: number;
  exit_pct: number;
  label: string;
  status?: SignalEntryStatus;
  status_note?: string;
}

export interface SignalUnderlyingPreset {
  label: string;
  symbol: string;
  strike_step: number;
  fut_symbol?: string;
  universe?: string;
  lot_size?: number;
}

export interface SignalEngineAdminConfig {
  underlying_symbol: string;
  underlying_label: string;
  fut_symbol: string;
  ce_symbol: string;
  pe_symbol: string;
  crude_symbol: string;
  india_vix_symbol: string;
  strike_step: number;
  pcr: number | null;
  max_pain: number | null;
  ivp: number | null;
  dow_change_pct: number | null;
  oi_pct_chg?: number | null;
  iv_chg?: number | null;
  india_vix?: number | null;
  fii_net?: number | null;
  mock?: boolean;
  engine_enabled?: boolean;
}

export interface SignalEngineConfigResponse {
  config: SignalEngineAdminConfig;
  presets: SignalUnderlyingPreset[];
  tool_bound: boolean;
  tool_slug: string | null;
}

export interface SignalEngineState {
  metrics: SignalMetricRow[];
  entry_ready: boolean;
  entry: SignalEntry | null;
  passed: number;
  evaluable: number;
  feed_source: string;
  evaluated_at: number;
  poll_ms: number;
  broker_poll_ms?: number;
  stream?: boolean;
  mock?: boolean;
  live: boolean;
  has_broker: boolean;
  engine_enabled?: boolean;
  engine_active?: boolean;
  /** Wall-clock ms when the snapshot was computed (worker / stream). */
  computed_at_ms?: number;
  /** Age of the snapshot in ms; null when computed_at_ms is unknown. */
  data_age_ms?: number | null;
  /** Server freshness window (ms); echoed for clients that want to display it. */
  snapshot_fresh_ms?: number;
  /** true when data_age_ms > snapshot_fresh_ms; null when age unknown. */
  snapshot_stale?: boolean | null;
  live_warnings: string[];
  /**
   * True when the engine is live/mock-off but underlying LTP is missing
   * (auth failure, empty underlying, broker miss). Prefer this over regex
   * on live_warnings for the Running / No quote badge.
   */
  live_quote_missing?: boolean;
  team_slug: string;
  underlying?: { symbol: string; label: string };
}

export interface SignalPublishResult {
  ok: boolean;
  error?: string;
  deduped?: boolean;
  entry?: SignalEntry;
  notification?: {
    batch_id: string;
    recipient_count: number;
    title: string;
    body: string;
  };
}

export async function getSignalConfig(
  accessToken: string,
): Promise<SignalEngineConfigResponse> {
  return apiFetch<SignalEngineConfigResponse>("/admin/signals/config", {
    accessToken,
  });
}

export async function patchSignalConfig(
  accessToken: string,
  config: Partial<SignalEngineAdminConfig>,
): Promise<SignalEngineConfigResponse & { ok: boolean }> {
  return apiFetch("/admin/signals/config", {
    accessToken,
    method: "PATCH",
    body: config,
  });
}

export async function getSignalState(
  accessToken: string,
): Promise<SignalEngineState> {
  return apiFetch<SignalEngineState>("/admin/signals/state", { accessToken });
}

export async function publishSignalEntry(
  accessToken: string,
  payload?: { title?: string; body?: string },
): Promise<SignalPublishResult> {
  return apiFetch<SignalPublishResult>("/admin/signals/publish", {
    accessToken,
    method: "POST",
    body: payload && (payload.title || payload.body) ? payload : {},
  });
}

export interface ParamChartAdminConfig {
  underlying_symbol: string;
  underlying_label: string;
  fut_symbol: string;
  strike_step: number;
  strike: number | null;
  entry_ce_premium: number;
  entry_pe_premium: number;
  ce_symbol: string;
  pe_symbol: string;
  year: number;
  month: number;
  interval?: "1m" | "5m" | "15m" | "1H" | "1D" | "1W" | "1M" | string;
}

export interface ParamChartSharedMetric {
  id: string;
  check_no: number;
  category: string;
  label: string;
  rule?: string | null;
  hint?: string | null;
}

export interface ParamChartMetricValue {
  id: string;
  value?: unknown;
  passed?: boolean | null;
  label?: string | null;
  category?: string | null;
  check_no?: number | null;
}

export interface ParamChartDay {
  date: string;
  day_index: number;
  weekday: string;
  open: number | null;
  high: number | null;
  low: number | null;
  close: number | null;
  volume?: number | null;
  /** Day-over-day close change (for bottom Δ histogram). */
  chg?: number | null;
  ce: number | null;
  pe: number | null;
  total: number | null;
  pct_vs_entry: number | null;
  metrics: Record<string, ParamChartMetricValue>;
  is_today?: boolean;
}

export interface ParamChartConfigResponse {
  ok: boolean;
  error?: string;
  config: ParamChartAdminConfig;
  presets: SignalUnderlyingPreset[];
  shared_metrics: ParamChartSharedMetric[];
  shared_categories: string[];
  intervals?: Array<{ id: string; label: string; kite?: string }>;
  tool_bound: boolean;
  has_broker: boolean;
  team_ready: boolean;
}

export interface ParamChartMonthSnapshot {
  ok: boolean;
  error?: string;
  building?: boolean;
  /** SSE tick with today bars only — merge into the REST hist pack client-side. */
  stream_patch?: boolean;
  year: number;
  month: number;
  interval?: string;
  underlying_symbol?: string;
  underlying_label?: string;
  strike?: number | null;
  entry_ce_premium?: number;
  entry_pe_premium?: number;
  entry_total?: number;
  ce_symbol?: string;
  pe_symbol?: string;
  fut_symbol?: string;
  days: ParamChartDay[];
  today?: string;
  /** Calendar-day → shared checklist metrics (prefer over per-bar ``metrics``). */
  metrics_by_day?: Record<string, Record<string, ParamChartMetricValue>>;
  live_metrics?: Record<string, ParamChartMetricValue>;
  config?: ParamChartAdminConfig;
  shared_metrics?: ParamChartSharedMetric[];
  shared_categories?: string[];
  poll_ms?: number;
  fetched_at?: number;
  stream_interval_ms?: number;
  data_source?: string;
  kite?: {
    source?: string;
    spot_token?: number | null;
    ce_token?: number | null;
    pe_token?: number | null;
    ohlc_days?: number;
    premium_days?: number;
    errors?: string[];
  };
  kite_live?: {
    source?: string;
    spot?: number | null;
    ce?: number | null;
    pe?: number | null;
    error?: string | null;
  };
  stale?: boolean;
}

export async function getParamChartConfig(
  accessToken: string,
): Promise<ParamChartConfigResponse> {
  return apiFetch<ParamChartConfigResponse>("/admin/param-chart/config", {
    accessToken,
  });
}

export async function patchParamChartConfig(
  accessToken: string,
  config: Partial<ParamChartAdminConfig>,
): Promise<ParamChartConfigResponse> {
  return apiFetch("/admin/param-chart/config", {
    accessToken,
    method: "PATCH",
    body: config,
  });
}

export async function getParamChartMonth(
  accessToken: string,
  opts?: { year?: number; month?: number; refresh?: boolean },
): Promise<ParamChartMonthSnapshot> {
  const params = new URLSearchParams();
  if (opts?.year != null) params.set("year", String(opts.year));
  if (opts?.month != null) params.set("month", String(opts.month));
  if (opts?.refresh) params.set("refresh", "true");
  const qs = params.toString();
  return apiFetch<ParamChartMonthSnapshot>(
    `/admin/param-chart/month${qs ? `?${qs}` : ""}`,
    { accessToken },
  );
}

export async function persistParamChartMetrics(
  accessToken: string,
): Promise<{
  ok: boolean;
  persisted?: boolean;
  eod_at?: number;
  metrics_persisted_at?: number;
  day?: string | null;
}> {
  return apiFetch("/admin/param-chart/persist-metrics", {
    accessToken,
    method: "POST",
    body: {},
  });
}

export interface OptionsLabAdminConfig {
  underlying_symbol: string;
  underlying_label: string;
  fut_symbol: string;
  strike_step: number;
  mock: boolean;
}

export interface OptionsLabConfigResponse {
  ok: boolean;
  error?: string;
  config: OptionsLabAdminConfig;
  presets: SignalUnderlyingPreset[];
  tool_bound: boolean;
  has_broker: boolean;
  team_ready: boolean;
}

export async function getOptionsLabConfig(
  accessToken: string,
): Promise<OptionsLabConfigResponse> {
  return apiFetch<OptionsLabConfigResponse>("/admin/options-lab/config", {
    accessToken,
  });
}

export async function patchOptionsLabConfig(
  accessToken: string,
  config: Partial<OptionsLabAdminConfig>,
): Promise<OptionsLabConfigResponse> {
  return apiFetch("/admin/options-lab/config", {
    accessToken,
    method: "PATCH",
    body: config,
  });
}

export interface OptionsChainLeg {
  symbol: string;
  ltp: number | null;
  oi: number | null;
  volume: number | null;
  iv: number | null;
  delta: number | null;
}

export interface OptionsChainRow {
  strike: number;
  is_atm: boolean;
  ce: OptionsChainLeg;
  pe: OptionsChainLeg;
}

export interface OptionsOiChartRow {
  strike: number;
  is_atm: boolean;
  ce_oi: number | null;
  pe_oi: number | null;
  ce_oi_chg: number | null;
  pe_oi_chg: number | null;
}

export interface OptionsStraddlePoint {
  t: number;
  ce: number;
  pe: number;
  combined: number;
  atm?: number | null;
  strike?: number;
}

export interface OptionsIvPoint {
  day: string;
  iv: number;
  t?: number;
}

export interface OptionsIvChart {
  points: OptionsIvPoint[];
  atm_iv: number | null;
  ivp: number | null;
  sample_days?: number;
}

export interface OptionsLabCharts {
  oi: OptionsOiChartRow[];
  oi_baseline_at?: number;
  straddle: {
    points: OptionsStraddlePoint[];
    atm: number | null;
    series?: Record<string, OptionsStraddlePoint[]>;
    strikes?: number[];
  };
  iv?: OptionsIvChart;
}

export interface OptionsChainSnapshot {
  ok: boolean;
  error?: string;
  mock?: boolean;
  spot: number | null;
  atm: number | null;
  underlying_symbol?: string;
  underlying_label?: string;
  fut_symbol?: string;
  strike_step?: number;
  wings?: number;
  fetched_at?: number;
  poll_ms?: number;
  stream?: boolean;
  quote_source?: "ticker" | "rest" | string;
  warnings?: string[];
  summary?: {
    pcr: number | null;
    max_pain: number | null;
    chain_ce_oi: number | null;
    chain_pe_oi: number | null;
    writer_grip_score: number | null;
    atm_iv?: number | null;
    ivp?: number | null;
  };
  rows?: OptionsChainRow[];
  charts?: OptionsLabCharts;
}

export async function resetOptionsLabOiBaseline(
  accessToken: string,
): Promise<{ ok: boolean }> {
  return apiFetch("/admin/options-lab/reset-oi-baseline", {
    accessToken,
    method: "POST",
  });
}

export async function getOptionsChain(
  accessToken: string,
  wings = 15,
): Promise<OptionsChainSnapshot> {
  const params = new URLSearchParams({ wings: String(wings) });
  return apiFetch<OptionsChainSnapshot>(`/admin/options-lab/chain?${params}`, {
    accessToken,
  });
}

export interface OptionsScreenerRow {
  underlying_symbol: string;
  underlying_label: string;
  fut_symbol: string;
  strike_step?: number | null;
  spot: number | null;
  atm: number | null;
  atm_iv: number | null;
  straddle: number | null;
  pcr: number | null;
  max_pain: number | null;
  chain_ce_oi: number | null;
  chain_pe_oi: number | null;
  oi_pct_chg: number | null;
  iv_chg: number | null;
  ivp: number | null;
  error: string | null;
}

export interface OptionsScreenerSnapshot {
  ok: boolean;
  error?: string;
  mock?: boolean;
  universe?: string;
  mode?: "fast" | "full" | string;
  /** True when spot/ATM only — PCR/IV/OI still enriching. */
  partial?: boolean;
  fetched_at?: number;
  warnings?: string[];
  rows?: OptionsScreenerRow[];
}

export async function getOptionsScreener(
  accessToken: string,
  universe = "indices",
  mode: "fast" | "full" = "full",
  signal?: AbortSignal,
): Promise<OptionsScreenerSnapshot> {
  const params = new URLSearchParams({ universe, mode });
  return apiFetch<OptionsScreenerSnapshot>(`/admin/options-lab/screener?${params}`, {
    accessToken,
    signal,
  });
}

export interface OptionsLabFlowsPoint {
  label: string;
  day?: string | null;
  fii_net: number | null;
  dii_net: number | null;
  mock?: boolean;
}

export interface OptionsLabFlowsSnapshot {
  ok: boolean;
  error?: string;
  mock?: boolean;
  fetched_at?: number;
  fii_net?: number | null;
  dii_net?: number | null;
  advance_decline_ratio?: number | null;
  series?: OptionsLabFlowsPoint[];
  warnings?: string[];
}

export async function getOptionsLabFlows(
  accessToken: string,
): Promise<OptionsLabFlowsSnapshot> {
  return apiFetch<OptionsLabFlowsSnapshot>(`/admin/options-lab/flows`, {
    accessToken,
  });
}

export interface OptionsLabGttRow {
  trigger_id?: string | number | null;
  status?: string | null;
  type?: string | null;
  trigger_values?: number[] | null;
  tradingsymbol?: string | null;
  symbols?: string[];
  created_at?: string | null;
}

export interface OptionsLabGttsResponse {
  ok: boolean;
  error?: string;
  mock?: boolean;
  tool?: string;
  team_slug?: string;
  gtts?: OptionsLabGttRow[];
  warnings?: string[];
}

export async function listOptionsLabGtts(
  accessToken: string,
): Promise<OptionsLabGttsResponse> {
  return apiFetch<OptionsLabGttsResponse>(`/admin/options-lab/gtts`, {
    accessToken,
  });
}

export async function deleteOptionsLabGtt(
  accessToken: string,
  triggerId: string | number,
): Promise<{ ok: boolean; error?: string; trigger_id?: string | number; warnings?: string[] }> {
  return apiFetch(`/admin/options-lab/gtts/${encodeURIComponent(String(triggerId))}`, {
    accessToken,
    method: "DELETE",
  });
}

export async function resetOptionsLabScreenerBaseline(
  accessToken: string,
): Promise<{ ok: boolean }> {
  return apiFetch("/admin/options-lab/reset-screener-baseline", {
    accessToken,
    method: "POST",
  });
}

export async function resetOptionsLabIvHistory(
  accessToken: string,
): Promise<{ ok: boolean }> {
  return apiFetch("/admin/options-lab/reset-iv-history", {
    accessToken,
    method: "POST",
  });
}

export interface OptionsIvHistoryResponse {
  ok: boolean;
  error?: string;
  symbol?: string;
  mock?: boolean;
  points?: OptionsIvPoint[];
  atm_iv?: number | null;
  ivp?: number | null;
  sample_days?: number;
}

export async function getOptionsIvHistory(
  accessToken: string,
  symbol: string,
): Promise<OptionsIvHistoryResponse> {
  const params = new URLSearchParams({ symbol });
  return apiFetch<OptionsIvHistoryResponse>(`/admin/options-lab/iv-history?${params}`, {
    accessToken,
  });
}

export interface OptionsPortfolioLeg {
  id: string;
  side: "buy" | "sell";
  type: "CE" | "PE";
  strike: number;
  qty: number;
  entry_premium: number;
  symbol?: string;
  unit?: "lots" | "shares";
  current_premium?: number | null;
  mtm?: number | null;
  entry_cash?: number;
}

export interface OptionsPortfolio {
  id: string;
  name: string;
  underlying_symbol: string;
  underlying_label: string;
  fut_symbol: string;
  strike_step: number;
  source: "manual" | "builder" | "kite_import";
  created_at: number;
  updated_at: number;
  legs: OptionsPortfolioLeg[];
}

export interface OptionsPortfolioListResponse {
  ok: boolean;
  error?: string;
  portfolios: OptionsPortfolio[];
  count: number;
}

export interface OptionsPortfolioMarkSummary {
  net_entry_cash: number;
  net_current_value: number | null;
  total_mtm: number;
  missing_quotes: number;
}

export interface OptionsPortfolioMarkResponse {
  ok: boolean;
  error?: string;
  portfolio_id?: string;
  name?: string;
  underlying_symbol?: string;
  underlying_label?: string;
  fut_symbol?: string;
  mock?: boolean;
  marked_at?: number;
  legs?: OptionsPortfolioLeg[];
  summary?: OptionsPortfolioMarkSummary;
}

export interface OptionsPortfolioCreateInput {
  name: string;
  underlying_symbol?: string;
  underlying_label?: string;
  fut_symbol?: string;
  strike_step?: number;
  source?: "manual" | "builder" | "kite_import";
  legs: Array<{
    id?: string;
    side: "buy" | "sell";
    type: "CE" | "PE";
    strike: number;
    qty?: number;
    entry_premium: number;
    symbol?: string;
  }>;
}

export async function listOptionsPortfolios(
  accessToken: string,
): Promise<OptionsPortfolioListResponse> {
  return apiFetch<OptionsPortfolioListResponse>("/admin/options-lab/portfolios", {
    accessToken,
  });
}

export async function createOptionsPortfolio(
  accessToken: string,
  payload: OptionsPortfolioCreateInput,
): Promise<{ ok: boolean; error?: string; portfolio?: OptionsPortfolio }> {
  return apiFetch("/admin/options-lab/portfolios", {
    accessToken,
    method: "POST",
    body: payload,
  });
}

export interface OptionsPortfolioPatchInput {
  name?: string;
  underlying_symbol?: string;
  underlying_label?: string;
  fut_symbol?: string;
  strike_step?: number;
  legs?: OptionsPortfolioCreateInput["legs"];
}

export async function updateOptionsPortfolio(
  accessToken: string,
  portfolioId: string,
  payload: OptionsPortfolioPatchInput,
): Promise<{ ok: boolean; error?: string; portfolio?: OptionsPortfolio }> {
  return apiFetch(`/admin/options-lab/portfolios/${encodeURIComponent(portfolioId)}`, {
    accessToken,
    method: "PATCH",
    body: payload,
  });
}

export async function deleteOptionsPortfolio(
  accessToken: string,
  portfolioId: string,
): Promise<{ ok: boolean; error?: string }> {
  return apiFetch(`/admin/options-lab/portfolios/${encodeURIComponent(portfolioId)}`, {
    accessToken,
    method: "DELETE",
  });
}

export async function markOptionsPortfolio(
  accessToken: string,
  portfolioId: string,
): Promise<OptionsPortfolioMarkResponse> {
  return apiFetch<OptionsPortfolioMarkResponse>(
    `/admin/options-lab/portfolios/${encodeURIComponent(portfolioId)}/mark`,
    { accessToken },
  );
}

export async function importOptionsPortfolioFromKite(
  accessToken: string,
  name?: string,
): Promise<{
  ok: boolean;
  error?: string;
  portfolio?: OptionsPortfolio;
  mark?: OptionsPortfolioMarkResponse;
  warnings?: string[];
}> {
  return apiFetch("/admin/options-lab/portfolios/import-kite", {
    accessToken,
    method: "POST",
    body: name ? { name } : {},
  });
}

export type OptionsLabBrokerReconcileResponse = {
  ok: boolean;
  error?: string;
  broker_ready?: boolean;
  mock?: boolean;
  margin?: {
    available_cash?: number | null;
    used_margin?: number | null;
    net?: number | null;
    span?: number | null;
    exposure?: number | null;
    option_premium?: number | null;
    utilization_pct?: number | null;
    source?: string | null;
    ok?: boolean;
  };
  diff?: {
    qty_unit?: string;
    matched?: Array<{
      symbol: string;
      qty: number;
      broker_lots?: number;
      lab_lots?: number;
      broker_shares?: number;
      lab_shares?: number;
      lot_size?: number;
      qty_unit?: string;
    }>;
    broker_only?: Array<{
      symbol: string;
      broker_qty: number;
      lab_qty: number;
      broker_lots?: number;
      lab_lots?: number;
      broker_shares?: number;
      lab_shares?: number;
      lot_size?: number;
      qty_unit?: string;
    }>;
    lab_only?: Array<{
      symbol: string;
      lab_qty: number;
      broker_qty: number;
      broker_lots?: number;
      lab_lots?: number;
      broker_shares?: number;
      lab_shares?: number;
      lot_size?: number;
      qty_unit?: string;
    }>;
    qty_mismatch?: Array<{
      symbol: string;
      broker_qty: number;
      lab_qty: number;
      delta: number;
      broker_lots?: number;
      lab_lots?: number;
      broker_shares?: number;
      lab_shares?: number;
      lot_size?: number;
      qty_unit?: string;
      lab_unit?: string;
      lab_unit_guess?: string;
    }>;
    summary?: {
      broker_symbols: number;
      lab_symbols: number;
      matched: number;
      broker_only: number;
      lab_only: number;
      qty_mismatch: number;
      in_sync: boolean;
      qty_unit?: string;
    };
    books?: Array<{
      id?: string;
      name?: string;
      source?: string;
      leg_count?: number;
      symbols?: string[];
    }>;
    bots_open?: Array<{
      id?: string;
      name?: string;
      mode?: string;
      leg_count?: number;
      symbols?: string[];
    }>;
    broker_legs?: Array<{
      symbol?: string;
      side?: string;
      qty?: number;
      type?: string;
      strike?: number;
    }>;
  };
  warnings?: string[];
};

export async function getOptionsLabBrokerReconcile(
  accessToken: string,
): Promise<OptionsLabBrokerReconcileResponse> {
  return apiFetch("/admin/options-lab/broker-reconcile", { accessToken });
}

export type OptionsLabMarginResponse = {
  ok: boolean;
  error?: string;
  source?: string;
  funds_needed?: number | null;
  margin_needed?: number | null;
  margin_final?: number | null;
  margin_available?: number | null;
  lot_size?: number;
  product?: string;
  basket?: boolean;
  estimated?: boolean;
  warnings?: string[];
};

export async function postOptionsLabMargins(
  accessToken: string,
  body: {
    legs: Array<{
      id?: string;
      side: string;
      type?: string;
      strike?: number;
      qty: number;
      entry_premium?: number;
      premium?: number;
      symbol?: string;
    }>;
    lot_size?: number;
    product?: string;
    underlying_symbol?: string;
    heuristic?: { marginNeeded?: number; fundsNeeded?: number };
    mock?: boolean;
    basket?: boolean;
  },
): Promise<OptionsLabMarginResponse> {
  return apiFetch<OptionsLabMarginResponse>("/admin/options-lab/margins", {
    accessToken,
    method: "POST",
    body,
  });
}

export type OptionsLabOrderResponse = {
  ok: boolean;
  partial?: boolean;
  error?: string;
  mock?: boolean;
  tool?: string;
  team_slug?: string;
  basket?: boolean;
  submitted_count?: number;
  failed_count?: number;
  orders?: Array<{
    leg_index?: number;
    symbol?: string;
    side?: string;
    quantity?: number;
    status?: string;
    order_id?: string | null;
    error?: string;
  }>;
  gtts?: Array<{
    leg_index?: number;
    symbol?: string;
    status?: string;
    trigger_id?: string | number | null;
    trigger_type?: string;
    trigger_values?: number[];
    error?: string;
  }>;
  errors?: string[];
  warnings?: string[];
  draft?: { ok?: boolean; portfolio?: OptionsPortfolio; error?: string };
};

export async function postOptionsLabOrders(
  accessToken: string,
  body: {
    legs: Array<{
      id?: string;
      side: string;
      type?: string;
      strike?: number;
      qty: number;
      entry_premium?: number;
      premium?: number;
      symbol?: string;
    }>;
    confirm: boolean;
    live?: boolean;
    lot_size?: number;
    product?: string;
    order_type?: string;
    name?: string;
    underlying_symbol?: string;
    save_draft?: boolean;
    mock?: boolean;
    stop_loss_pct?: number;
    target_pct?: number;
    basket?: boolean;
  },
): Promise<OptionsLabOrderResponse> {
  return apiFetch<OptionsLabOrderResponse>("/admin/options-lab/orders", {
    accessToken,
    method: "POST",
    body,
  });
}

export interface OptionsLabBacktestLegInput {
  id?: string;
  side: "buy" | "sell";
  type: "CE" | "PE";
  strike: number;
  qty?: number;
  premium?: number;
  entry_premium?: number;
  symbol?: string;
}

export interface OptionsLabBacktestStats {
  hit_rate: number;
  avg_pnl: number;
  path_trough: number;
  path_peak: number;
}

export interface OptionsLabBacktestResult {
  fidelity: string;
  days: number;
  shock_pct: number;
  path_bias: string;
  spot: number;
  shocks: Array<{
    day: number;
    up: number;
    down: number;
    path_spot: number;
    pnl_up: number;
    pnl_down: number;
    pnl_path: number;
  }>;
  equity: Array<{ day: number; equity: number }>;
  stats: OptionsLabBacktestStats;
}

export interface OptionsLabBacktestSummaryRow {
  id?: string;
  name?: string;
  created_at?: number;
  updated_at?: number;
  fidelity?: string;
  underlying_symbol?: string | null;
  params?: {
    days?: number;
    shock_pct?: number;
    path_bias?: string;
    strike_step?: number | null;
  };
  stats?: OptionsLabBacktestStats;
  leg_count?: number;
}

export interface OptionsLabBacktestRecord extends OptionsLabBacktestSummaryRow {
  spot?: number;
  legs?: OptionsLabBacktestLegInput[];
  result?: OptionsLabBacktestResult;
  underlying_label?: string | null;
  source?: string;
}

export async function listOptionsLabBacktests(
  accessToken: string,
): Promise<{
  ok: boolean;
  backtests?: OptionsLabBacktestSummaryRow[];
  count?: number;
  error?: string;
}> {
  return apiFetch(`/admin/options-lab/backtests`, { accessToken });
}

export async function createOptionsLabBacktest(
  accessToken: string,
  payload: {
    name?: string;
    legs: OptionsLabBacktestLegInput[];
    spot: number;
    days?: number;
    shock_pct?: number;
    path_bias?: string;
    strike_step?: number;
    underlying_symbol?: string;
    underlying_label?: string;
    use_historical?: boolean;
    use_marks?: boolean;
    iv_pct?: number;
    entry_dte?: number;
    closes?: number[];
  },
): Promise<{
  ok: boolean;
  backtest?: OptionsLabBacktestRecord;
  error?: string;
  warnings?: string[];
}> {
  return apiFetch(`/admin/options-lab/backtests`, {
    accessToken,
    method: "POST",
    body: payload,
  });
}

export async function getOptionsLabBacktest(
  accessToken: string,
  backtestId: string,
): Promise<{ ok: boolean; backtest?: OptionsLabBacktestRecord; error?: string }> {
  return apiFetch(`/admin/options-lab/backtests/${encodeURIComponent(backtestId)}`, {
    accessToken,
  });
}

export async function deleteOptionsLabBacktest(
  accessToken: string,
  backtestId: string,
): Promise<{ ok: boolean; error?: string }> {
  return apiFetch(`/admin/options-lab/backtests/${encodeURIComponent(backtestId)}`, {
    accessToken,
    method: "DELETE",
  });
}

export async function summarizeOptionsLabBacktests(
  accessToken: string,
  payload?: { ids?: string[]; limit?: number },
): Promise<{
  ok: boolean;
  count?: number;
  runs?: Array<{
    id?: string;
    name?: string;
    hit_rate?: number;
    avg_pnl?: number;
    path_trough?: number;
    path_peak?: number;
    days?: number;
    fidelity?: string;
  }>;
  stats?: {
    avg_hit_rate: number;
    avg_pnl: number;
    avg_path_trough: number;
    avg_path_peak: number;
  } | null;
  correlations?: Array<{
    a?: string;
    b?: string;
    a_name?: string;
    b_name?: string;
    corr?: number;
  }>;
  note?: string;
  error?: string;
}> {
  return apiFetch(`/admin/options-lab/backtests/summary`, {
    accessToken,
    method: "POST",
    body: payload ?? {},
  });
}

export interface OptionsLabBotEntry {
  min_ivp?: number | null;
  max_ivp?: number | null;
  min_pcr?: number | null;
  max_pcr?: number | null;
  max_dte?: number | null;
}

export interface OptionsLabBotSchedule {
  days?: number[];
  window_start?: string;
  window_end?: string;
}

export interface OptionsLabBot {
  id: string;
  name: string;
  enabled: boolean;
  kill?: boolean;
  mode: "paper" | "live";
  template?: string | null;
  backtest_id?: string | null;
  underlying_symbol?: string | null;
  profit_pct?: number;
  stop_pct?: number;
  avoid_events?: boolean;
  max_dte_hold?: number | null;
  schedule?: OptionsLabBotSchedule;
  entry?: OptionsLabBotEntry;
  cooldown_sec?: number;
  max_runs_per_day?: number;
  runs_today?: number;
  runs_today_date?: string | null;
  last_run_at?: number | null;
  last_run_message?: string | null;
  open_position?: boolean;
  log?: Array<{ ts: number; ok: boolean; auto?: boolean; message: string }>;
  created_at?: number;
  updated_at?: number;
  source?: string;
  leg_count?: number;
}

export type OptionsLabBotInput = {
  name?: string;
  enabled?: boolean;
  kill?: boolean;
  mode?: "paper" | "live";
  template?: string | null;
  backtest_id?: string | null;
  underlying_symbol?: string | null;
  width_steps?: number;
  profit_pct?: number;
  stop_pct?: number;
  avoid_events?: boolean;
  max_dte_hold?: number | null;
  clear_open_position?: boolean;
  schedule?: OptionsLabBotSchedule;
  entry?: OptionsLabBotEntry;
  cooldown_sec?: number;
  max_runs_per_day?: number;
  source?: string;
};

export async function listOptionsLabBots(accessToken: string): Promise<{
  ok: boolean;
  bots?: OptionsLabBot[];
  count?: number;
  armed_paper?: number;
  worker_enabled?: boolean;
  error?: string;
}> {
  return apiFetch(`/admin/options-lab/bots`, { accessToken });
}

export async function createOptionsLabBot(
  accessToken: string,
  payload: OptionsLabBotInput,
): Promise<{ ok: boolean; bot?: OptionsLabBot; error?: string }> {
  return apiFetch(`/admin/options-lab/bots`, {
    accessToken,
    method: "POST",
    body: payload,
  });
}

export async function updateOptionsLabBot(
  accessToken: string,
  botId: string,
  payload: OptionsLabBotInput,
): Promise<{ ok: boolean; bot?: OptionsLabBot; error?: string }> {
  return apiFetch(`/admin/options-lab/bots/${encodeURIComponent(botId)}`, {
    accessToken,
    method: "PATCH",
    body: payload,
  });
}

export async function deleteOptionsLabBot(
  accessToken: string,
  botId: string,
): Promise<{ ok: boolean; error?: string }> {
  return apiFetch(`/admin/options-lab/bots/${encodeURIComponent(botId)}`, {
    accessToken,
    method: "DELETE",
  });
}

export async function runOptionsLabBot(
  accessToken: string,
  botId: string,
  payload?: { confirm?: boolean; auto?: boolean },
): Promise<{
  ok: boolean;
  skipped?: boolean;
  message?: string;
  error?: string;
  bot?: OptionsLabBot;
  mock?: boolean;
  tool?: string;
  submitted_count?: number;
}> {
  return apiFetch(`/admin/options-lab/bots/${encodeURIComponent(botId)}/run`, {
    accessToken,
    method: "POST",
    body: payload ?? { confirm: true },
  });
}

export async function evaluateOptionsLabBots(accessToken: string): Promise<{
  ok: boolean;
  results?: Array<Record<string, unknown>>;
  count?: number;
  error?: string;
}> {
  return apiFetch(`/admin/options-lab/bots/evaluate`, {
    accessToken,
    method: "POST",
    body: {},
  });
}

export async function getCustomerDesk(
  accessToken: string,
  deskSnapshot = false,
): Promise<DomainDashboard> {
  const params = new URLSearchParams({
    desk_snapshot: deskSnapshot ? "true" : "false",
  });
  return apiFetch<DomainDashboard>(`/api/desk?${params}`, { accessToken });
}

export async function getAdminDesk(
  accessToken: string,
  deskSnapshot = false,
): Promise<DomainDashboard> {
  const params = new URLSearchParams({
    desk_snapshot: deskSnapshot ? "true" : "false",
  });
  return apiFetch<DomainDashboard>(`/admin/domains/desk?${params}`, {
    accessToken,
  });
}

export type EvalEvaluator =
  | "exact"
  | "contains"
  | "regex"
  | "accuracy"
  | "agent_as_judge"
  | "performance"
  | "reliability";

export interface EvalCase {
  key: string;
  name: string;
  input: string;
  expected_output: string;
  evaluator: EvalEvaluator;
}

export interface EvalCaseResult {
  id: string;
  case_key: string;
  name: string;
  input: string;
  expected_output: string;
  actual_output: string | null;
  evaluator: string;
  score: number;
  passed: boolean;
  latency_ms: number | null;
  input_tokens: number | null;
  output_tokens: number | null;
  estimated_cost_usd: number | null;
  error: string | null;
  details: { mocked?: boolean };
}

export interface EvalRun {
  id: string;
  eval_definition_id: string;
  trigger: string;
  status: string;
  score: number | null;
  passed: boolean | null;
  total_cases: number;
  passed_cases: number;
  latency_ms: number | null;
  input_tokens: number | null;
  output_tokens: number | null;
  estimated_cost_usd: number | null;
  error: string | null;
  started_at: string;
  completed_at: string | null;
  case_results?: EvalCaseResult[];
}

export interface EvalDefinition {
  id: string;
  name: string;
  slug: string;
  description: string | null;
  suite: string;
  target_type: "team" | "workflow";
  target_id: string;
  version_id: string;
  cases: EvalCase[];
  pass_threshold: number;
  active: boolean;
  run_on_publish: boolean;
  created_by: string;
  created_at: string;
  updated_at: string;
  latest_run: EvalRun | null;
  runs: EvalRun[];
}

export function listEvals(accessToken: string): Promise<EvalDefinition[]> {
  return apiFetch<EvalDefinition[]>("/api/admin/evals", { accessToken });
}

export interface EvalTarget {
  target_type: "team" | "workflow";
  target_id: string;
  version_id: string;
  name: string;
  slug: string;
  version_status: "draft" | "published";
}

export function listEvalTargets(accessToken: string): Promise<EvalTarget[]> {
  return apiFetch<EvalTarget[]>("/api/admin/evals/targets/catalog", {
    accessToken,
  });
}

export function getEval(
  accessToken: string,
  evalId: string,
): Promise<EvalDefinition> {
  return apiFetch<EvalDefinition>(`/api/admin/evals/${evalId}`, {
    accessToken,
  });
}

export function createEval(
  accessToken: string,
  input: {
    name: string;
    slug: string;
    target_type: "team" | "workflow";
    target_id: string;
    version_id: string;
    cases: EvalCase[];
  },
): Promise<EvalDefinition> {
  return apiFetch<EvalDefinition>("/api/admin/evals", {
    accessToken,
    method: "POST",
    body: { ...input, suite: "smoke", pass_threshold: 1 },
  });
}

export function runEval(
  accessToken: string,
  evalId: string,
): Promise<EvalRun> {
  return apiFetch<EvalRun>(`/api/admin/evals/${evalId}/runs`, {
    accessToken,
    method: "POST",
  });
}

export interface MetricsDashboard {
  range_days: number;
  generated_at: string;
  kpis: {
    runs: number;
    success_rate: number;
    error_rate: number;
    latency_p95_ms: number | null;
    input_tokens: number;
    output_tokens: number;
    estimated_cost_usd: number;
    approval_waits: number;
    unique_sessions: number;
  };
  daily: Array<{
    date: string;
    runs: number;
    success_count: number;
    error_count: number;
    latency_p50_ms: number | null;
    latency_p95_ms: number | null;
  }>;
  top_targets: Array<{
    target_type: string;
    target_id: string;
    name: string;
    run_count: number;
    success_count: number;
    error_count: number;
    approval_waits: number;
    latency_p95_ms: number | null;
    success_rate: number;
  }>;
  top_tools: Array<{ name: string; count: number }>;
}

export function getMetrics(
  accessToken: string,
  days = 30,
): Promise<MetricsDashboard> {
  return apiFetch<MetricsDashboard>(`/api/admin/metrics?days=${days}`, {
    accessToken,
  });
}

export interface ScheduleTarget {
  target_type: "team" | "workflow";
  target_id: string;
  version_id: string;
  name: string;
  slug: string;
}

export interface ScheduleRun {
  id: string;
  trigger: "manual" | "cron";
  status: "running" | "completed" | "error";
  session_id: string;
  run_id: string | null;
  error: string | null;
  started_at: string;
  completed_at: string | null;
}

export interface AgentSchedule {
  id: string;
  name: string;
  cron_expression: string;
  timezone: string;
  enabled: boolean;
  target_type: ScheduleTarget["target_type"];
  target_id: string;
  version_id: string;
  message: string;
  input_payload: Record<string, unknown>;
  last_run_at: string | null;
  next_run_at: string | null;
  last_status: "running" | "queued" | "completed" | "error" | null;
  last_error: string | null;
  created_at: string;
  updated_at: string;
  runs: ScheduleRun[];
}

export function listSchedules(accessToken: string): Promise<AgentSchedule[]> {
  return apiFetch<AgentSchedule[]>("/api/admin/schedules", { accessToken });
}

export function listScheduleTargets(accessToken: string): Promise<ScheduleTarget[]> {
  return apiFetch<ScheduleTarget[]>("/api/admin/schedules/targets/catalog", {
    accessToken,
  });
}

export function createSchedule(
  accessToken: string,
  input: Omit<
    AgentSchedule,
    | "id"
    | "last_run_at"
    | "next_run_at"
    | "last_status"
    | "last_error"
    | "created_at"
    | "updated_at"
    | "runs"
  >,
): Promise<AgentSchedule> {
  return apiFetch<AgentSchedule>("/api/admin/schedules", {
    accessToken,
    method: "POST",
    body: input,
  });
}

export function updateSchedule(
  accessToken: string,
  scheduleId: string,
  input: Partial<Parameters<typeof createSchedule>[1]>,
): Promise<AgentSchedule> {
  return apiFetch<AgentSchedule>(`/api/admin/schedules/${scheduleId}`, {
    accessToken,
    method: "PATCH",
    body: input,
  });
}

export function setScheduleEnabled(
  accessToken: string,
  scheduleId: string,
  enabled: boolean,
): Promise<AgentSchedule> {
  return apiFetch<AgentSchedule>(`/api/admin/schedules/${scheduleId}/state`, {
    accessToken,
    method: "POST",
    body: { enabled },
  });
}

export function runScheduleNow(
  accessToken: string,
  scheduleId: string,
): Promise<ScheduleRun> {
  return apiFetch<ScheduleRun>(`/api/admin/schedules/${scheduleId}/run`, {
    accessToken,
    method: "POST",
  });
}

export async function deleteSchedule(
  accessToken: string,
  scheduleId: string,
): Promise<void> {
  await apiFetch<void>(`/api/admin/schedules/${scheduleId}`, {
    accessToken,
    method: "DELETE",
  });
}

export interface McpServerSettings {
  enabled: boolean;
  status: "ready" | "disabled";
  endpoint: string;
  protocol_version: string;
  implementation: "atlas_gateway";
  required_scopes: string[];
  supports: string[];
  limitations: string[];
}

export function getMcpServerSettings(
  accessToken: string,
): Promise<McpServerSettings> {
  return apiFetch<McpServerSettings>("/admin/mcp", { accessToken });
}

export function setMcpServerEnabled(
  accessToken: string,
  enabled: boolean,
): Promise<McpServerSettings> {
  return apiFetch<McpServerSettings>("/admin/mcp", {
    accessToken,
    method: "PATCH",
    body: { enabled },
  });
}

interface BackendPlatformTenant {
  id: string;
  name: string;
  slug: string;
  auth_org_id: string;
  domain?: string;
  branding: Record<string, unknown>;
  timezone?: string;
  is_active: boolean;
  owner_email?: string | null;
  created_at: string;
  updated_at: string;
}

interface BackendPlatformAuditEvent {
  id: string;
  actor_id: string;
  actor_email?: string | null;
  actor_name?: string | null;
  action: string;
  tenant_id: string | null;
  details: Record<string, unknown>;
  created_at: string;
}

function mapPlatformTenant(row: BackendPlatformTenant): PlatformTenant {
  return {
    id: row.id,
    name: row.name,
    slug: row.slug,
    authOrgId: row.auth_org_id,
    domain: row.domain || "generic",
    branding: row.branding,
    timezone: row.timezone || "UTC",
    isActive: row.is_active,
    ownerEmail: row.owner_email ?? null,
    createdAt: row.created_at,
    updatedAt: row.updated_at,
  };
}

export async function listPlatformTenants(
  accessToken: string,
): Promise<PlatformTenant[]> {
  const rows = await apiFetch<BackendPlatformTenant[]>(
    "/admin/platform/tenants",
    { accessToken },
  );
  return rows.map(mapPlatformTenant);
}

export async function createPlatformTenant(
  accessToken: string,
  input: {
    name: string;
    slug: string;
    authOrgId: string;
    timezone?: string;
    domain?: WorkspaceDomain;
    ownerEmail: string;
    ownerDisplayName?: string;
    ownerPassword: string;
    ownerPasswordConfirm: string;
  },
): Promise<PlatformTenant> {
  const row = await apiFetch<BackendPlatformTenant>(
    "/admin/platform/tenants",
    {
      accessToken,
      method: "POST",
      body: {
        name: input.name,
        slug: input.slug,
        auth_org_id: input.authOrgId,
        timezone: input.timezone || "UTC",
        domain: input.domain || "generic",
        owner_email: input.ownerEmail,
        owner_display_name: input.ownerDisplayName || undefined,
        owner_password: input.ownerPassword,
        owner_password_confirm: input.ownerPasswordConfirm,
      },
    },
  );
  return mapPlatformTenant(row);
}

export async function updatePlatformTenant(
  accessToken: string,
  tenantId: string,
  input: {
    name?: string;
    timezone?: string;
    ownerEmail?: string;
    ownerDisplayName?: string;
    ownerPassword?: string;
    ownerPasswordConfirm?: string;
  },
): Promise<PlatformTenant> {
  const body: Record<string, unknown> = {};
  if (input.name !== undefined) body.name = input.name;
  if (input.timezone !== undefined) body.timezone = input.timezone;
  if (input.ownerEmail !== undefined) body.owner_email = input.ownerEmail;
  if (input.ownerDisplayName !== undefined) {
    body.owner_display_name = input.ownerDisplayName;
  }
  if (input.ownerPassword) {
    body.owner_password = input.ownerPassword;
    body.owner_password_confirm = input.ownerPasswordConfirm;
  }
  const row = await apiFetch<BackendPlatformTenant>(
    `/admin/platform/tenants/${tenantId}`,
    { accessToken, method: "PATCH", body },
  );
  return mapPlatformTenant(row);
}

export async function setPlatformTenantActive(
  accessToken: string,
  tenantId: string,
  isActive: boolean,
): Promise<PlatformTenant> {
  const row = await apiFetch<BackendPlatformTenant>(
    `/admin/platform/tenants/${tenantId}`,
    {
      accessToken,
      method: "PATCH",
      body: { is_active: isActive },
    },
  );
  return mapPlatformTenant(row);
}

export async function enterPlatformTenant(
  accessToken: string,
  tenantId: string,
): Promise<PlatformTenant> {
  const row = await apiFetch<BackendPlatformTenant>(
    `/admin/platform/tenants/${tenantId}/enter`,
    { accessToken, method: "POST" },
  );
  return mapPlatformTenant(row);
}

export interface PlatformCatalogItem {
  id: string;
  name: string;
  slug: string;
  kind: "team" | "workflow";
  status: "draft" | "published";
  domain: string;
}

export async function listPlatformTenantCatalog(
  accessToken: string,
  tenantId: string,
): Promise<PlatformCatalogItem[]> {
  const rows = await apiFetch<
    Array<{
      id: string;
      name: string;
      slug: string;
      kind: "team" | "workflow";
      status: "draft" | "published";
      domain?: string;
    }>
  >(`/admin/platform/tenants/${tenantId}/catalog`, { accessToken });
  return rows.map((row) => ({
    id: row.id,
    name: row.name,
    slug: row.slug,
    kind: row.kind,
    status: row.status,
    domain: row.domain || "generic",
  }));
}

export interface PlatformImportResult {
  agents: Record<string, string>;
  teams: Record<string, string>;
  workflows: Record<string, string>;
  tools: Record<string, string>;
  knowledgeBases: Record<string, string>;
  warnings: string[];
  counts: Record<string, number>;
}

export async function importPlatformTenantResources(
  accessToken: string,
  input: {
    sourceTenantId: string;
    destinationTenantId: string;
    teamIds: string[];
    workflowIds: string[];
  },
): Promise<PlatformImportResult> {
  const row = await apiFetch<{
    agents: Record<string, string>;
    teams: Record<string, string>;
    workflows: Record<string, string>;
    tools: Record<string, string>;
    knowledge_bases: Record<string, string>;
    warnings: string[];
    counts: Record<string, number>;
  }>("/admin/platform/tenants/import", {
    accessToken,
    method: "POST",
    body: {
      source_tenant_id: input.sourceTenantId,
      destination_tenant_id: input.destinationTenantId,
      team_ids: input.teamIds,
      workflow_ids: input.workflowIds,
    },
  });
  return {
    agents: row.agents,
    teams: row.teams,
    workflows: row.workflows,
    tools: row.tools,
    knowledgeBases: row.knowledge_bases,
    warnings: row.warnings,
    counts: row.counts,
  };
}

export async function listPlatformAudit(
  accessToken: string,
): Promise<PlatformAuditEvent[]> {
  const rows = await apiFetch<BackendPlatformAuditEvent[]>(
    "/admin/platform/audit",
    { accessToken },
  );
  return rows.map((row) => ({
    id: row.id,
    actorId: row.actor_id,
    actorEmail: row.actor_email ?? null,
    actorName: row.actor_name ?? null,
    action: row.action,
    tenantId: row.tenant_id,
    details: row.details ?? {},
    createdAt: row.created_at,
  }));
}

function mapBillingPlan(row: {
  id: string;
  scope: "platform" | "tenant";
  slug: string;
  name: string;
  description: string;
  monthly_price_cents: number;
  included_credits_monthly: number;
  credits_per_1k_input_tokens: number;
  credits_per_1k_output_tokens: number;
  credit_pack_credits: number;
  credit_pack_price_cents: number;
  is_active: boolean;
}): BillingPlan {
  return {
    id: row.id,
    scope: row.scope,
    slug: row.slug,
    name: row.name,
    description: row.description,
    monthlyPriceCents: row.monthly_price_cents,
    includedCreditsMonthly: row.included_credits_monthly,
    creditsPer1kInputTokens: row.credits_per_1k_input_tokens,
    creditsPer1kOutputTokens: row.credits_per_1k_output_tokens,
    creditPackCredits: row.credit_pack_credits,
    creditPackPriceCents: row.credit_pack_price_cents,
    isActive: row.is_active,
  };
}

function mapBillingWallet(row: {
  id: string;
  owner_type: "tenant" | "user";
  owner_id: string;
  balance_credits: number;
  allowance_remaining: number;
  available_credits: number;
  plan_id: string | null;
  subscription_status: string;
  period_start: string | null;
  period_end: string | null;
}): BillingWallet {
  return {
    id: row.id,
    ownerType: row.owner_type,
    ownerId: row.owner_id,
    balanceCredits: row.balance_credits,
    allowanceRemaining: row.allowance_remaining,
    availableCredits: row.available_credits,
    planId: row.plan_id,
    subscriptionStatus: row.subscription_status,
    periodStart: row.period_start,
    periodEnd: row.period_end,
  };
}

export async function getTenantBillingWallet(
  accessToken: string,
): Promise<BillingWallet> {
  const row = await apiFetch<Parameters<typeof mapBillingWallet>[0]>(
    "/admin/billing/wallet",
    { accessToken },
  );
  return mapBillingWallet(row);
}

export async function listTenantBillingPlans(
  accessToken: string,
): Promise<BillingPlan[]> {
  const rows = await apiFetch<Parameters<typeof mapBillingPlan>[0][]>(
    "/admin/billing/plans",
    { accessToken },
  );
  return rows.map(mapBillingPlan);
}

export async function upsertTenantBillingPlan(
  accessToken: string,
  slug: string,
  body: {
    name: string;
    description?: string;
    monthlyPriceCents?: number;
    includedCreditsMonthly?: number;
    creditsPer1kInputTokens?: number;
    creditsPer1kOutputTokens?: number;
    creditPackCredits?: number;
    creditPackPriceCents?: number;
    isActive?: boolean;
  },
): Promise<BillingPlan> {
  const row = await apiFetch<Parameters<typeof mapBillingPlan>[0]>(
    `/admin/billing/plans/${encodeURIComponent(slug)}`,
    {
      accessToken,
      method: "PUT",
      body: {
        slug,
        name: body.name,
        description: body.description ?? "",
        monthly_price_cents: body.monthlyPriceCents ?? 0,
        included_credits_monthly: body.includedCreditsMonthly ?? 0,
        credits_per_1k_input_tokens: body.creditsPer1kInputTokens ?? 10,
        credits_per_1k_output_tokens: body.creditsPer1kOutputTokens ?? 30,
        credit_pack_credits: body.creditPackCredits ?? 1000,
        credit_pack_price_cents: body.creditPackPriceCents ?? 1000,
        is_active: body.isActive ?? true,
      },
    },
  );
  return mapBillingPlan(row);
}

export async function grantBillingCredits(
  accessToken: string,
  body: {
    ownerType: "tenant" | "user";
    ownerId: string;
    credits: number;
    description?: string;
  },
): Promise<BillingWallet> {
  const row = await apiFetch<Parameters<typeof mapBillingWallet>[0]>(
    "/admin/billing/grant",
    {
      accessToken,
      method: "POST",
      body: {
        owner_type: body.ownerType,
        owner_id: body.ownerId,
        credits: body.credits,
        description: body.description ?? "Admin credit grant",
      },
    },
  );
  return mapBillingWallet(row);
}

export async function getTenantUserBillingWallet(
  accessToken: string,
  userId: string,
): Promise<BillingWallet> {
  const row = await apiFetch<Parameters<typeof mapBillingWallet>[0]>(
    `/admin/billing/wallets/users/${encodeURIComponent(userId)}`,
    { accessToken },
  );
  return mapBillingWallet(row);
}

export async function purchaseTenantCreditPack(
  accessToken: string,
  body?: { planId?: string | null },
): Promise<{
  wallet: BillingWallet;
  checkoutUrl: string | null;
  status: "completed" | "pending";
}> {
  const row = await apiFetch<{
    wallet: Parameters<typeof mapBillingWallet>[0];
    checkout_url: string | null;
    status: "completed" | "pending";
  }>("/admin/billing/checkout/credit-pack", {
    accessToken,
    method: "POST",
    body: {
      owner_type: "tenant",
      plan_id: body?.planId ?? null,
      success_url: "/admin/billing",
      cancel_url: "/admin/billing",
    },
  });
  return {
    wallet: mapBillingWallet(row.wallet),
    checkoutUrl: row.checkout_url,
    status: row.status,
  };
}

export async function listTenantBillingLedger(
  accessToken: string,
  limit = 50,
): Promise<BillingLedgerEntry[]> {
  const rows = await apiFetch<
    Array<{
      id: string;
      entry_type: string;
      amount_credits: number;
      balance_after: number;
      description: string;
      reference_type: string | null;
      reference_id: string | null;
      created_by: string;
      created_at: string;
    }>
  >(`/admin/billing/ledger?limit=${limit}`, { accessToken });
  return rows.map((row) => ({
    id: row.id,
    entryType: row.entry_type,
    amountCredits: row.amount_credits,
    balanceAfter: row.balance_after,
    description: row.description,
    referenceType: row.reference_type,
    referenceId: row.reference_id,
    createdBy: row.created_by,
    createdAt: row.created_at,
  }));
}

export async function listPlatformBillingPlans(
  accessToken: string,
): Promise<BillingPlan[]> {
  const rows = await apiFetch<Parameters<typeof mapBillingPlan>[0][]>(
    "/admin/platform/billing/plans",
    { accessToken },
  );
  return rows.map(mapBillingPlan);
}

export async function getPlatformTenantWallet(
  accessToken: string,
  tenantId: string,
): Promise<PlatformTenantWallet> {
  const row = await apiFetch<{
    tenant_id: string;
    balance_credits: number;
    allowance_remaining: number;
    available_credits: number;
    subscription_status: string;
    plan_id: string | null;
  }>(`/admin/platform/billing/tenants/${tenantId}/wallet`, { accessToken });
  return {
    tenantId: row.tenant_id,
    balanceCredits: row.balance_credits,
    allowanceRemaining: row.allowance_remaining,
    availableCredits: row.available_credits,
    subscriptionStatus: row.subscription_status,
    planId: row.plan_id,
  };
}

export async function grantPlatformTenantCredits(
  accessToken: string,
  tenantId: string,
  body: { credits: number; description?: string },
): Promise<PlatformTenantWallet> {
  const row = await apiFetch<{
    tenant_id: string;
    balance_credits: number;
    allowance_remaining: number;
    available_credits: number;
    subscription_status: string;
    plan_id: string | null;
  }>(`/admin/platform/billing/tenants/${tenantId}/grant`, {
    accessToken,
    method: "POST",
    body: {
      credits: body.credits,
      description: body.description ?? "Platform credit grant",
    },
  });
  return {
    tenantId: row.tenant_id,
    balanceCredits: row.balance_credits,
    allowanceRemaining: row.allowance_remaining,
    availableCredits: row.available_credits,
    subscriptionStatus: row.subscription_status,
    planId: row.plan_id,
  };
}
