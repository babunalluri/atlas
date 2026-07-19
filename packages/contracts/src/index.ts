/**
 * Shared API contracts. Prefer regenerating from OpenAPI via scripts/generate-contracts.sh.
 */
export type { components, operations, paths } from "./openapi";

export interface AgentVersionDto {
  id: string;
  version: number;
  status: "draft" | "published" | "archived" | string;
  instructions: string;
  model_id: string;
  temperature: number;
  memory_mode: "none" | "session" | "persistent_user" | string;
  created_at: string;
}

export interface AgentConfigDto {
  id: string;
  slug: string;
  name: string;
  description: string | null;
  published_version_id: string | null;
  updated_at: string;
  tools: Array<{
    tool_key: "web_search" | "rest_read" | "rest_mutate";
    config: Record<string, unknown>;
    credential_id?: string | null;
  }>;
  knowledge_base_id?: string | null;
  draft?: AgentVersionDto | null;
  published?: AgentVersionDto | null;
}

export interface ApprovalDto {
  id: string;
  tool_name: string;
  status: string;
  redacted_arguments: Record<string, unknown>;
  resolved_by: string | null;
  decision_reason: string | null;
  session_id: string | null;
  run_id: string | null;
  continuation_error: string | null;
  expires_at: string | null;
  created_at: string;
}

export type AgentStreamEventDto =
  | { event: "RunContent"; content: string }
  | { event: "RunError"; error: string }
  | { event: "RunCompleted" }
  | {
      event: "RunPaused";
      run_id: string;
      session_id: string;
      approval_ids: string[];
      requirements: Array<Record<string, unknown>>;
    }
  | { event: string; [key: string]: unknown };
