/** Shared frontend API types (mirrors packages/contracts + admin schemas). */

export type TenantRole = "platform_admin" | "tenant_admin" | "end_user";

export interface PlatformTenant {
  id: string;
  name: string;
  slug: string;
  clerkOrgId: string;
  branding: Record<string, unknown>;
  isActive: boolean;
  createdAt: string;
  updatedAt: string;
}

export interface PlatformAuditEvent {
  id: string;
  actorId: string;
  action: string;
  tenantId: string | null;
  details: Record<string, unknown>;
  createdAt: string;
}
export type AgentStatus = "draft" | "published" | "archived";
export type TeamMode = "route" | "coordinate";
export type WorkflowMode = "sequential" | "parallel";
export type MemoryMode = "session" | "persistent";

export type ModelId =
  | "gpt-4.1"
  | "gpt-4.1-mini"
  | "claude-sonnet-4"
  | "claude-haiku"
  | "llama-3.3-70b"
  | "llama-3.1-8b"
  | "gpt-oss-120b";

export type ToolKind = "web_search" | "rest_read" | "rest_mutate";

export type IngestionStatus =
  | "pending"
  | "uploading"
  | "processing"
  | "ready"
  | "failed";

export type ApprovalStatus = "pending" | "approved" | "rejected" | "expired";

export interface RunRequest {
  message: string;
  session_id?: string;
  stream: true;
  factory_input: {
    agent_config_id: string;
    preview?: boolean;
  };
}

export interface TenantBranding {
  name: string;
  slug: string;
  primaryColor: string;
  accentColor: string;
  logoUrl?: string | null;
  tagline?: string | null;
}

export interface ToolBinding {
  id: string;
  kind: ToolKind;
  label: string;
  enabled: boolean;
  config: Record<string, unknown>;
  requiresApproval: boolean;
  definitionId?: string;
}

export interface ToolDefinition {
  id: string;
  name: string;
  slug: string;
  description: string;
  kind: "http" | "openapi" | "python_toolkit" | "custom_python" | "mcp";
  httpMethod: "GET" | "POST" | "PUT" | "PATCH" | "DELETE" | null;
  baseUrl: string | null;
  path: string | null;
  requestSchema: Record<string, unknown>;
  responseDescription: string;
  responseSchema: Record<string, unknown> | null;
  headers: Record<string, string>;
  config: Record<string, unknown>;
  credentialId: string | null;
  approvalRequired: boolean;
  active: boolean;
  connectionStatus: "unvalidated" | "connected" | "failed";
  lastValidatedAt: string | null;
  lastValidationError: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface ToolCapability {
  name: string;
  description: string;
  approvalRequired: boolean;
  inputSchema: Record<string, unknown>;
}

export interface ToolValidation {
  ok: boolean;
  message: string;
  capabilities: ToolCapability[];
}

export interface KnowledgeSource {
  id: string;
  knowledgeBaseId?: string;
  name: string;
  mimeType: string;
  byteSize: number;
  status: IngestionStatus;
  errorMessage?: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface KnowledgeBaseSummary {
  id: string;
  name: string;
  sources: KnowledgeSource[];
}

export interface KnowledgeSearchResult {
  id: string;
  content: string;
  score: number;
  sourceId: string;
  metadata: Record<string, unknown>;
}

export interface AgentSummary {
  id: string;
  name: string;
  slug: string;
  status: AgentStatus;
  model: ModelId;
  updatedAt: string;
  publishedVersion?: number | null;
}

export interface CatalogPage<T> {
  items: T[];
  total: number;
  page: number;
  pageSize: number;
}

export interface AgentConfig {
  id: string;
  name: string;
  slug: string;
  description: string;
  instructions: string;
  model: ModelId;
  temperature: number;
  memoryMode: MemoryMode;
  status: AgentStatus;
  tools: ToolBinding[];
  knowledgeBaseId: string | null;
  knowledgeBase?: KnowledgeBaseSummary | null;
  draftVersion: number;
  publishedVersion: number | null;
  updatedAt: string;
}

export interface AgentDraftInput {
  name: string;
  slug: string;
  description: string;
  instructions: string;
  model: ModelId;
  temperature: number;
  memoryMode: MemoryMode;
  tools: ToolBinding[];
  knowledgeBaseId: string | null;
}

export interface TeamMember {
  agentConfigId: string;
  agentVersionId: string;
  position: number;
  name: string;
  slug: string;
  version: number;
  status: AgentStatus;
}

export interface TeamConfig {
  id: string;
  name: string;
  slug: string;
  description: string;
  instructions: string;
  mode: TeamMode;
  model: ModelId;
  temperature: number;
  status: AgentStatus;
  members: TeamMember[];
  draftVersion: number;
  publishedVersion: number | null;
  updatedAt: string;
}

export interface TeamSummary {
  id: string;
  name: string;
  slug: string;
  status: AgentStatus;
  mode: TeamMode;
  memberCount: number;
  publishedVersion: number | null;
  updatedAt: string;
}

export interface TeamDraftInput {
  name: string;
  description: string;
  instructions: string;
  mode: TeamMode;
  model: ModelId;
  temperature: number;
  memberConfigIds: string[];
}

export interface WorkflowStep {
  id?: string;
  name: string;
  targetType: "agent" | "team";
  targetConfigId: string;
  targetVersionId?: string;
  targetName?: string;
  targetSlug?: string;
  targetVersion?: number;
  targetStatus?: AgentStatus;
  conditionExpression?: string | null;
}

export interface WorkflowConfig {
  id: string;
  name: string;
  slug: string;
  description: string;
  mode: WorkflowMode;
  status: AgentStatus;
  steps: WorkflowStep[];
  draftVersion: number;
  publishedVersion: number | null;
  updatedAt: string;
}

export interface WorkflowSummary {
  id: string;
  name: string;
  slug: string;
  mode: WorkflowMode;
  status: AgentStatus;
  stepCount: number;
  publishedVersion: number | null;
  updatedAt: string;
}

export interface WorkflowDraftInput {
  name: string;
  description: string;
  mode: WorkflowMode;
  steps: WorkflowStep[];
}

export interface ApprovalRequest {
  id: string;
  agentName: string;
  agentId: string;
  toolLabel: string;
  toolKind: ToolKind;
  summary: string;
  argumentsPreview: Record<string, unknown>;
  status: ApprovalStatus;
  requestedBy: string;
  createdAt: string;
  sessionId?: string | null;
  runId?: string | null;
  continuationError?: string | null;
}

export interface ConversationSession {
  id: string;
  title: string;
  targetType: "agent" | "team" | "workflow";
  versionId: string;
  updatedAt: string;
  pausedForApproval: boolean;
  status: "active" | "running" | "paused" | "completed" | "error" | "cancelled";
  lastRunId?: string | null;
}

export interface AdminSession extends ConversationSession {
  userId: string;
  targetId: string;
}

export interface UserMemory {
  id: string;
  userId: string;
  memory: string;
  updatedAt?: string | null;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  createdAt: string;
  status?: "streaming" | "complete" | "error" | "cancelled" | "paused";
  toolSpans?: Array<{
    id: string;
    name: string;
    status: "running" | "done" | "error" | "awaiting_approval";
  }>;
  pendingApproval?: boolean;
}

export interface PublicAgentSurface {
  tenant: TenantBranding;
  agent: {
    id: string;
    name: string;
    slug: string;
    description: string;
    welcomeMessage: string;
  };
}

export interface PublicTeamSurface {
  tenant: TenantBranding;
  team: {
    id: string;
    name: string;
    slug: string;
    description: string;
    welcomeMessage: string;
  };
}

export interface PublicWorkflowSurface {
  tenant: TenantBranding;
  workflow: {
    id: string;
    name: string;
    slug: string;
    description: string;
    welcomeMessage: string;
  };
}

export interface AvailableWorkflow {
  id: string;
  name: string;
  slug: string;
  description: string;
}

export interface WorkflowAssignments {
  workflowId: string;
  userIds: string[];
}

export interface TenantUser {
  id: string;
  userId: string;
  displayName: string;
  email: string | null;
  role: "tenant_admin" | "end_user";
  isActive: boolean;
  workflowIds: string[];
  createdAt: string;
  updatedAt: string;
}

export interface TenantUserInput {
  userId: string;
  displayName: string;
  email?: string | null;
  role: "tenant_admin" | "end_user";
  isActive: boolean;
  workflowIds: string[];
}

export const ALLOWED_MODELS: Array<{ id: ModelId; label: string }> = [
  { id: "gpt-4.1", label: "GPT-4.1" },
  { id: "gpt-4.1-mini", label: "GPT-4.1 Mini" },
  { id: "claude-sonnet-4", label: "Claude Sonnet 4" },
  { id: "claude-haiku", label: "Claude Haiku" },
  { id: "llama-3.3-70b", label: "Groq Llama 3.3 70B" },
  { id: "llama-3.1-8b", label: "Groq Llama 3.1 8B" },
  { id: "gpt-oss-120b", label: "Groq GPT-OSS 120B" },
];

export const TOOL_CATALOG: Array<{
  kind: ToolKind;
  label: string;
  description: string;
  requiresApproval: boolean;
}> = [
  {
    kind: "web_search",
    label: "Web search",
    description: "Search the public web for current information.",
    requiresApproval: false,
  },
  {
    kind: "rest_read",
    label: "REST read",
    description: "Call allowlisted GET/HEAD endpoints.",
    requiresApproval: false,
  },
  {
    kind: "rest_mutate",
    label: "REST mutate",
    description: "Call allowlisted mutating endpoints with admin approval.",
    requiresApproval: true,
  },
];
