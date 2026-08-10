/** Shared frontend API types (mirrors packages/contracts + admin schemas). */

export type TenantRole = "platform_admin" | "tenant_admin" | "end_user";

export interface PlatformTenant {
  id: string;
  name: string;
  slug: string;
  authOrgId: string;
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
  | "gpt-oss-120b"
  | "kimi-k2.5"
  | "kimi-k2"
  | "kimi-latest"
  | "nvidia-llama-3.3-70b"
  | "nvidia-llama-3.1-8b"
  | "nvidia-nemotron-70b"
  | "gemini-2.5-flash"
  | "gemini-2.5-pro"
  | "gemini-2.0-flash";

export type ModelProvider =
  | "openai"
  | "anthropic"
  | "groq"
  | "moonshot"
  | "nvidia"
  | "gemini";

export const MODEL_PROVIDER_LABELS: Record<ModelProvider, string> = {
  openai: "OpenAI",
  anthropic: "Anthropic",
  groq: "Groq",
  moonshot: "Kimi",
  nvidia: "NVIDIA",
  gemini: "Gemini",
};

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
  kind:
    | "http"
    | "openapi"
    | "python_toolkit"
    | "custom_python"
    | "tenant_python"
    | "mcp";
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
  publishedVersionId?: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface SandboxPythonPackage {
  id: string;
  name: string;
  version: string;
  sha256: string;
  active: boolean;
  createdAt: string;
  updatedAt: string;
}

export interface TenantPythonTemplate {
  key: string;
  label: string;
  source_code: string;
  capabilities: Array<{
    name: string;
    description: string;
    mutating: boolean;
    input_schema: Record<string, unknown>;
  }>;
  settings: Record<string, unknown>;
  dependencies: Array<{ name: string; version: string }>;
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

export interface AgentGuardrails {
  promptInjection: boolean;
  piiDetection: boolean;
  openaiModeration: boolean;
}

export type FrameworkAdapter =
  | "agno"
  | "langgraph"
  | "dspy"
  | "claude_agent_sdk"
  | "antigravity";

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
  frameworkAdapter: FrameworkAdapter;
  guardrails: AgentGuardrails;
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
  frameworkAdapter: FrameworkAdapter;
  guardrails: AgentGuardrails;
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
  tools: ToolBinding[];
  members: TeamMember[];
  draftVersion: number;
  publishedVersion: number | null;
  updatedAt: string;
}

export interface TeamVersionSummary {
  id: string;
  version: number;
  status: AgentStatus;
  mode: TeamMode;
  memberCount: number;
  isLive: boolean;
  createdAt: string;
}

export interface TeamVersionDetail {
  id: string;
  version: number;
  status: AgentStatus;
  instructions: string;
  mode: TeamMode;
  model: ModelId;
  temperature: number;
  members: TeamMember[];
  createdAt: string;
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
  tools: ToolBinding[];
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
  /** Human-readable actor (membership display name, Guest, API, …). */
  userLabel?: string | null;
  targetId: string;
  createdAt: string;
}

export type ActivityChannel = "live_chat" | "scheduled" | "api" | "email";

export interface ActivityRow {
  id: string;
  title: string;
  userId: string;
  userLabel: string;
  personaName: string;
  personaType: "agent" | "team" | "workflow";
  taskName: string;
  status: AdminSession["status"];
  channel: ActivityChannel;
  createdAt: string;
  updatedAt: string;
  lastRunId: string | null;
  targetId: string;
  scheduleName: string | null;
}

export interface UserMemory {
  id: string;
  userId: string;
  memory: string;
  topics?: string[];
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

export interface PublicWorkflowTeamStep {
  id: string;
  name: string;
  slug: string;
  stepName: string;
}

export interface PublicWorkflowSurface {
  tenant: TenantBranding;
  workflow: {
    id: string;
    name: string;
    slug: string;
    description: string;
    welcomeMessage: string;
    teams?: PublicWorkflowTeamStep[];
  };
}

export interface AvailableWorkflow {
  id: string;
  name: string;
  slug: string;
  description: string;
}

export interface AvailableTeam {
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
  phone: string | null;
  role: "tenant_admin" | "end_user";
  isActive: boolean;
  invitePending?: boolean;
  temporaryPassword?: string | null;
  signInUrl?: string | null;
  workflowIds: string[];
  teamIds: string[];
  createdAt: string;
  updatedAt: string;
}

export interface TenantUserInput {
  userId?: string;
  displayName: string;
  email: string;
  phone?: string;
  role: "tenant_admin" | "end_user";
  isActive: boolean;
  workflowIds: string[];
  teamIds: string[];
}

export type NotificationAudience = "user" | "all";

export interface UserNotification {
  id: string;
  batchId: string;
  title: string;
  body: string;
  audience: NotificationAudience;
  createdBy: string;
  readAt: string | null;
  createdAt: string;
}

export interface NotificationBatch {
  batchId: string;
  title: string;
  body: string;
  audience: NotificationAudience;
  createdBy: string;
  recipientCount: number;
  createdAt: string;
}

export interface NotificationSendResult {
  batchId: string;
  audience: NotificationAudience;
  recipientCount: number;
  title: string;
}

export interface BillingPlan {
  id: string;
  scope: "platform" | "tenant";
  slug: string;
  name: string;
  description: string;
  monthlyPriceCents: number;
  includedCreditsMonthly: number;
  creditsPer1kInputTokens: number;
  creditsPer1kOutputTokens: number;
  creditPackCredits: number;
  creditPackPriceCents: number;
  isActive: boolean;
}

export interface BillingWallet {
  id: string;
  ownerType: "tenant" | "user";
  ownerId: string;
  balanceCredits: number;
  allowanceRemaining: number;
  availableCredits: number;
  planId: string | null;
  subscriptionStatus: string;
  periodStart: string | null;
  periodEnd: string | null;
}

export interface BillingLedgerEntry {
  id: string;
  entryType: string;
  amountCredits: number;
  balanceAfter: number;
  description: string;
  referenceType: string | null;
  referenceId: string | null;
  createdBy: string;
  createdAt: string;
}

export interface PlatformTenantWallet {
  tenantId: string;
  balanceCredits: number;
  allowanceRemaining: number;
  availableCredits: number;
  subscriptionStatus: string;
  planId: string | null;
}

/** Verified public customer (OTP / inbound email), not Clerk staff. */
export interface EndCustomer {
  id: string;
  email: string;
  displayName: string;
  emailVerifiedAt: string | null;
  isActive: boolean;
  metadata: Record<string, unknown>;
  createdAt: string;
  updatedAt: string;
}

export const ALLOWED_MODELS: Array<{
  id: ModelId;
  label: string;
  provider: ModelProvider;
}> = [
  { id: "gpt-4.1", label: "GPT-4.1", provider: "openai" },
  { id: "gpt-4.1-mini", label: "GPT-4.1 Mini", provider: "openai" },
  { id: "claude-sonnet-4", label: "Claude Sonnet 4", provider: "anthropic" },
  { id: "claude-haiku", label: "Claude Haiku", provider: "anthropic" },
  { id: "llama-3.3-70b", label: "Groq Llama 3.3 70B", provider: "groq" },
  { id: "llama-3.1-8b", label: "Groq Llama 3.1 8B", provider: "groq" },
  { id: "gpt-oss-120b", label: "Groq GPT-OSS 120B", provider: "groq" },
  { id: "kimi-k2.5", label: "Kimi K2.5", provider: "moonshot" },
  { id: "kimi-k2", label: "Kimi K2", provider: "moonshot" },
  { id: "kimi-latest", label: "Kimi Latest", provider: "moonshot" },
  {
    id: "nvidia-llama-3.3-70b",
    label: "NVIDIA Llama 3.3 70B",
    provider: "nvidia",
  },
  {
    id: "nvidia-llama-3.1-8b",
    label: "NVIDIA Llama 3.1 8B",
    provider: "nvidia",
  },
  {
    id: "nvidia-nemotron-70b",
    label: "NVIDIA Nemotron 70B",
    provider: "nvidia",
  },
  { id: "gemini-2.5-flash", label: "Gemini 2.5 Flash", provider: "gemini" },
  { id: "gemini-2.5-pro", label: "Gemini 2.5 Pro", provider: "gemini" },
  { id: "gemini-2.0-flash", label: "Gemini 2.0 Flash", provider: "gemini" },
];

export function providerForModel(modelId: ModelId | string): ModelProvider {
  const known = ALLOWED_MODELS.find((model) => model.id === modelId);
  if (known) return known.provider;
  if (typeof modelId === "string") {
    if (modelId.startsWith("claude-")) return "anthropic";
    if (modelId.startsWith("kimi-")) return "moonshot";
    if (modelId.startsWith("nvidia-")) return "nvidia";
    if (modelId.startsWith("gemini-")) return "gemini";
    if (modelId.startsWith("llama-") || modelId === "gpt-oss-120b") {
      return "groq";
    }
  }
  return "openai";
}

export function toBackendModelId(modelId: ModelId): string {
  return `${providerForModel(modelId)}:${modelId}`;
}

/** Providers that have at least one tenant credential usable for LLM models. */
export function modelProvidersWithCredentials(
  credentials: Array<{ provider: string }>,
): Set<ModelProvider> {
  const providers = new Set<ModelProvider>();
  for (const credential of credentials) {
    if (
      credential.provider === "openai" ||
      credential.provider === "anthropic" ||
      credential.provider === "groq" ||
      credential.provider === "moonshot" ||
      credential.provider === "nvidia" ||
      credential.provider === "gemini"
    ) {
      providers.add(credential.provider);
    }
  }
  return providers;
}

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
