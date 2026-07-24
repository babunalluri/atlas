import {
  MOCK_AGENT_DETAIL,
  MOCK_AGENTS,
  MOCK_APPROVALS,
  MOCK_INGESTION,
  MOCK_PUBLIC_SURFACE,
} from "./mocks";
import type {
  AgentConfig,
  AgentDraftInput,
  AgentSummary,
  AvailableWorkflow,
  AdminSession,
  ApprovalRequest,
  CatalogPage,
  KnowledgeBaseSummary,
  KnowledgeSearchResult,
  KnowledgeSource,
  PlatformAuditEvent,
  PlatformTenant,
  PublicAgentSurface,
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
  ToolBinding,
  ToolCapability,
  ToolDefinition,
  ToolValidation,
  UserMemory,
  WorkflowConfig,
  WorkflowAssignments,
  WorkflowDraftInput,
  WorkflowSummary,
} from "./types";
import { TOOL_CATALOG } from "./types";
import {
  buildPublicApiRunCatalog,
  teamStepsFromPublished,
  type PublicApiCatalogLoad,
  type PublicApiTeamOption,
} from "@/lib/api/public-api-catalog";
import { agentOsUrl, apiFetch } from "@/lib/agentos/client";
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
  return modelId.replace(/^(openai|anthropic|groq):/, "") as AgentConfig["model"];
}

function backendModel(modelId: AgentDraftInput["model"]): string {
  if (modelId.startsWith("claude-")) {
    return `anthropic:${modelId}`;
  }
  if (modelId.startsWith("llama-") || modelId === "gpt-oss-120b") {
    return `groq:${modelId}`;
  }
  return `openai:${modelId}`;
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
  };
}

function mapTeam(raw: BackendTeam): TeamConfig {
  const editable = raw.draft ?? raw.published;
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

export function listAgents(accessToken: string): Promise<AgentSummary[]> {
  return withFallback(
    async () => {
      const rows = await apiFetch<BackendAgent[]>("/admin/agents", {
        accessToken,
      });
      return rows.map((row) => {
        const agent = mapAgent(row);
        return {
          id: agent.id,
          name: agent.name,
          slug: agent.slug,
          status: agent.status,
          model: agent.model,
          updatedAt: agent.updatedAt,
          publishedVersion: agent.publishedVersion,
        };
      });
    },
    MOCK_AGENTS,
  );
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
      publishedVersion: item.published_version,
      updatedAt: item.updated_at,
    })),
    total: row.total,
    page: row.page,
    pageSize: row.page_size,
  };
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
      updatedAt: new Date().toISOString(),
    },
  );
}

export async function listTeams(
  accessToken: string,
): Promise<TeamSummary[]> {
  const rows = await apiFetch<BackendTeam[]>("/admin/teams", { accessToken });
  return rows.map((row) => {
    const team = mapTeam(row);
    return {
      id: team.id,
      name: team.name,
      slug: team.slug,
      status: team.status,
      mode: team.mode,
      memberCount: team.members.length,
      publishedVersion: team.publishedVersion,
      updatedAt: team.updatedAt,
    };
  });
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
        name: draft.name,
        description: draft.description,
        instructions: draft.instructions,
        mode: draft.mode,
        model_id: backendModel(draft.model),
        temperature: draft.temperature,
        member_config_ids: draft.memberConfigIds,
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
  const rows = await apiFetch<BackendWorkflow[]>("/admin/workflows", { accessToken });
  return rows.map((row) => {
    const workflow = mapWorkflow(row);
    return {
      id: workflow.id,
      name: workflow.name,
      slug: workflow.slug,
      mode: workflow.mode,
      status: workflow.status,
      stepCount: workflow.steps.length,
      publishedVersion: workflow.publishedVersion,
      updatedAt: workflow.updatedAt,
    };
  });
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

interface BackendTenantUser {
  id: string;
  user_id: string;
  display_name: string;
  email: string | null;
  role: "tenant_admin" | "end_user";
  is_active: boolean;
  workflow_ids: string[];
  created_at: string;
  updated_at: string;
}

function mapTenantUser(row: BackendTenantUser): TenantUser {
  return {
    id: row.id,
    userId: row.user_id,
    displayName: row.display_name,
    email: row.email,
    role: row.role,
    isActive: row.is_active,
    workflowIds: row.workflow_ids,
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
        user_id: input.userId,
        display_name: input.displayName,
        email: input.email,
        role: input.role,
        is_active: input.isActive,
        workflow_ids: input.workflowIds,
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
        ...(input.role !== undefined ? { role: input.role } : {}),
        ...(input.isActive !== undefined ? { is_active: input.isActive } : {}),
        ...(input.workflowIds !== undefined
          ? { workflow_ids: input.workflowIds }
          : {}),
      },
    }),
  );
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
  const response = await fetch(
    agentOsUrl(`/admin/knowledge/bases/${knowledgeBaseId}/upload`),
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${accessToken}`,
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
  return {
    id: row.id,
    knowledgeBaseId,
    name: String(row.metadata.filename ?? file.name),
    mimeType: String(row.metadata.content_type ?? file.type),
    byteSize: Number(row.metadata.bytes ?? file.size),
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
): Promise<AdminSession[]> {
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
      last_run_id: string | null;
      status: AdminSession["status"];
      updated_at: string;
    }>
  >("/api/sessions?all_users=true", { accessToken });
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
    lastRunId: row.last_run_id,
    status: row.status,
    pausedForApproval: row.status === "paused",
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
    updatedAt:
      typeof row.updated_at === "string" ? row.updated_at : null,
  }));
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

export async function listTraces(accessToken: string): Promise<TraceSummary[]> {
  const rows = await apiFetch<BackendTraceSummary[]>("/api/admin/traces", {
    accessToken,
  });
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

export function getPublicChatSurface(
  tenantSlug: string,
  agentSlug: string,
): Promise<PublicAgentSurface> {
  const live = () =>
    apiFetch<PublicAgentSurface>(
      `/public/t/${tenantSlug}/agents/${agentSlug}`,
      { accessToken: "public" },
    );
  if (!mocksEnabled()) {
    return live();
  }
  return withFallback(live, {
    ...MOCK_PUBLIC_SURFACE,
    tenant: { ...MOCK_PUBLIC_SURFACE.tenant, slug: tenantSlug },
    agent: { ...MOCK_PUBLIC_SURFACE.agent, slug: agentSlug },
  });
}

export async function getPublicTenantBranding(
  tenantSlug: string,
): Promise<TenantBranding> {
  const result = await apiFetch<{
    slug: string;
    name: string;
    branding: Record<string, unknown>;
  }>(`/public/tenants/${tenantSlug}`, { accessToken: "public" });
  return {
    name: result.name,
    slug: result.slug,
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
  branding: Record<string, unknown>;
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
  input: { name: string; slug: string },
): Promise<WorkspaceInfo & { clerk_org_id: string; is_active: boolean }> {
  return apiFetch("/admin/onboarding/workspace", {
    accessToken,
    method: "POST",
    body: input,
  });
}

export interface EvalCase {
  key: string;
  name: string;
  input: string;
  expected_output: string;
  evaluator: "exact" | "contains" | "regex";
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
  target_type: "agent" | "team" | "workflow";
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
  target_type: "agent" | "team" | "workflow";
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
    target_type: "agent" | "team" | "workflow";
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
  target_type: "agent" | "team" | "workflow";
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
  clerk_org_id: string;
  branding: Record<string, unknown>;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

interface BackendPlatformAuditEvent {
  id: string;
  actor_id: string;
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
    clerkOrgId: row.clerk_org_id,
    branding: row.branding,
    isActive: row.is_active,
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
  input: { name: string; slug: string; clerkOrgId: string },
): Promise<PlatformTenant> {
  const row = await apiFetch<BackendPlatformTenant>(
    "/admin/platform/tenants",
    {
      accessToken,
      method: "POST",
      body: {
        name: input.name,
        slug: input.slug,
        clerk_org_id: input.clerkOrgId,
      },
    },
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
    action: row.action,
    tenantId: row.tenant_id,
    details: row.details,
    createdAt: row.created_at,
  }));
}
