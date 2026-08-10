import type {
  AgentConfig,
  AgentSummary,
  ApprovalRequest,
  KnowledgeSource,
} from "@/lib/api/types";

export const MOCK_AGENTS: AgentSummary[] = [
  {
    id: "agt_support",
    name: "Support Concierge",
    slug: "support-concierge",
    status: "published",
    model: "claude-sonnet-4",
    updatedAt: "2026-07-18T16:20:00.000Z",
    publishedVersion: 3,
  },
  {
    id: "agt_ops",
    name: "Ops Briefing",
    slug: "ops-briefing",
    status: "draft",
    model: "gpt-4.1",
    updatedAt: "2026-07-19T01:05:00.000Z",
    publishedVersion: null,
  },
];

export const MOCK_AGENT_DETAIL: AgentConfig = {
  id: "agt_support",
  name: "Support Concierge",
  slug: "support-concierge",
  description: "Answers product questions with the tenant knowledge base.",
  instructions:
    "You are a precise customer support concierge. Prefer knowledge-base evidence, cite source titles, and escalate mutating actions for approval. Never invent policy.",
  model: "claude-sonnet-4",
  temperature: 0.3,
  memoryMode: "persistent",
  status: "published",
  tools: [
    {
      id: "tool_web",
      kind: "web_search",
      label: "Web search",
      enabled: true,
      config: {},
      requiresApproval: false,
    },
    {
      id: "tool_rest_read",
      kind: "rest_read",
      label: "REST read",
      enabled: true,
      config: { base_host: "api.example.com" },
      requiresApproval: false,
    },
    {
      id: "tool_rest_mutate",
      kind: "rest_mutate",
      label: "REST mutate",
      enabled: true,
      config: { base_host: "api.example.com" },
      requiresApproval: true,
    },
  ],
  knowledgeBaseId: "kb_main",
  frameworkAdapter: "agno",
  guardrails: {
    promptInjection: true,
    piiDetection: false,
    openaiModeration: false,
  },
  knowledgeBase: {
    id: "kb_main",
    name: "Product docs",
    sources: [
      {
        id: "src_1",
        name: "billing-faq.pdf",
        mimeType: "application/pdf",
        byteSize: 482_112,
        status: "ready",
        createdAt: "2026-07-10T10:00:00.000Z",
        updatedAt: "2026-07-10T10:04:00.000Z",
      },
      {
        id: "src_2",
        name: "returns-policy.md",
        mimeType: "text/markdown",
        byteSize: 12_480,
        status: "processing",
        createdAt: "2026-07-19T06:40:00.000Z",
        updatedAt: "2026-07-19T06:41:00.000Z",
      },
      {
        id: "src_3",
        name: "legacy-notes.docx",
        mimeType:
          "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        byteSize: 90_112,
        status: "failed",
        errorMessage: "Unsupported embedded object",
        createdAt: "2026-07-12T08:00:00.000Z",
        updatedAt: "2026-07-12T08:01:00.000Z",
      },
    ],
  },
  draftVersion: 4,
  publishedVersion: 3,
  updatedAt: "2026-07-18T16:20:00.000Z",
};

export const MOCK_APPROVALS: ApprovalRequest[] = [
  {
    id: "apr_1",
    agentId: "agt_support",
    agentName: "Support Concierge",
    toolLabel: "REST mutate",
    toolKind: "rest_mutate",
    summary: "Create refund for order ORD-2041",
    argumentsPreview: {
      method: "POST",
      path: "/v1/refunds",
      amount: 49.0,
      currency: "USD",
    },
    status: "pending",
    requestedBy: "user_end_42",
    createdAt: "2026-07-19T01:12:00.000Z",
    sessionId: "sess_9",
    runId: "run_77",
  },
  {
    id: "apr_2",
    agentId: "agt_ops",
    agentName: "Ops Briefing",
    toolLabel: "REST mutate",
    toolKind: "rest_mutate",
    summary: "Rotate webhook secret for connector stripe",
    argumentsPreview: {
      method: "PUT",
      path: "/v1/connectors/stripe/secret",
    },
    status: "approved",
    requestedBy: "user_admin_1",
    createdAt: "2026-07-18T20:02:00.000Z",
  },
];

export const MOCK_INGESTION: KnowledgeSource[] =
  MOCK_AGENT_DETAIL.knowledgeBase?.sources ?? [];
