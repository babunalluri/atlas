import { streamAgentRun, streamPublicRun, type StreamRunOptions } from "./sse";
import type {
  ChatMessage,
  ConversationSession,
} from "@/lib/api/types";
import { unpackAccessContext } from "@/lib/auth/access-context";

function agentOsBaseUrl(): string {
  // Server-side (Docker/SSR) must reach the backend by service hostname.
  // Browser clients keep using the public NEXT_PUBLIC_AGENTOS_URL.
  const base =
    (typeof window === "undefined"
      ? process.env.AGENTOS_INTERNAL_URL
      : undefined) ??
    process.env.NEXT_PUBLIC_AGENTOS_URL ??
    "http://localhost:7777";
  return base.replace(/\/$/, "");
}

export function agentOsUrl(path: string): string {
  const normalized = path.startsWith("/") ? path : `/${path}`;
  return `${agentOsBaseUrl()}${normalized}`;
}

const GUEST_ID_KEY = "atlas_guest_id";

/** Stable browser guest id for anonymous public chat (not a credential). */
export function getOrCreateGuestId(): string {
  if (typeof window === "undefined") {
    return `ssr_${Math.random().toString(36).slice(2, 12)}`;
  }
  try {
    const existing = window.localStorage.getItem(GUEST_ID_KEY);
    if (existing && /^[a-zA-Z0-9_-]{8,64}$/.test(existing)) {
      return existing;
    }
    const created = crypto.randomUUID().replace(/-/g, "").slice(0, 32);
    window.localStorage.setItem(GUEST_ID_KEY, created);
    return created;
  } catch {
    return crypto.randomUUID().replace(/-/g, "").slice(0, 32);
  }
}

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

const API_ERROR_MESSAGE_MAX = 400;

function truncateMessage(text: string, max = API_ERROR_MESSAGE_MAX): string {
  const trimmed = text.trim();
  if (trimmed.length <= max) return trimmed;
  return `${trimmed.slice(0, Math.max(0, max - 1))}…`;
}

/**
 * FastAPI/Pydantic 422 bodies include a full `input` snapshot (e.g. entire
 * tool source_code). Surface only loc + msg so UI banners stay readable.
 */
export function summarizeApiErrorBody(
  text: string,
  fallback = "Request failed",
): string {
  if (!text.trim()) return fallback;
  try {
    const parsed: unknown = JSON.parse(text);
    if (typeof parsed === "string") return truncateMessage(parsed);

    if (parsed && typeof parsed === "object") {
      const record = parsed as {
        detail?: unknown;
        message?: unknown;
        error?: unknown;
      };

      if (typeof record.detail === "string") {
        return truncateMessage(record.detail);
      }

      if (Array.isArray(record.detail)) {
        const parts = record.detail
          .map((item) => {
            if (typeof item === "string") return item;
            if (!item || typeof item !== "object") return null;
            const row = item as { msg?: unknown; loc?: unknown };
            const msg = typeof row.msg === "string" ? row.msg : null;
            if (!msg) return null;
            const loc = Array.isArray(row.loc)
              ? row.loc
                  .filter((part) => part !== "body" && typeof part === "string")
                  .join(".")
              : "";
            return loc ? `${msg} (${loc})` : msg;
          })
          .filter((part): part is string => Boolean(part));
        if (parts.length > 0) return truncateMessage(parts.join("; "));
      }

      if (typeof record.message === "string") {
        return truncateMessage(record.message);
      }
      if (typeof record.error === "string") {
        return truncateMessage(record.error);
      }
    }
  } catch {
    // Non-JSON bodies fall through to a truncated raw string.
  }
  return truncateMessage(text);
}

export async function apiFetch<T>(
  path: string,
  options: {
    accessToken: string;
    method?: string;
    body?: unknown;
    signal?: AbortSignal;
  },
): Promise<T> {
  const { accessToken, method = "GET", body, signal } = options;
  const access = unpackAccessContext(accessToken);
  const platformTenantId = path.startsWith("/admin/platform")
    ? undefined
    : access.platformTenantId;
  const response = await fetch(agentOsUrl(path), {
    method,
    signal,
    headers: {
      Authorization: `Bearer ${access.token}`,
      Accept: "application/json",
      ...(platformTenantId
        ? { "X-Platform-Tenant-ID": platformTenantId }
        : {}),
      ...(process.env.NEXT_PUBLIC_DEV_AUTH === "true"
        ? {
            "X-Dev-Tenant-ID":
              process.env.NEXT_PUBLIC_DEV_TENANT_ID ??
              "11111111-1111-1111-1111-111111111111",
            "X-Dev-User-ID":
              process.env.NEXT_PUBLIC_DEV_USER_ID ?? "dev-admin",
            "X-Dev-Role":
              process.env.NEXT_PUBLIC_DEV_ROLE ?? "tenant_admin",
          }
        : {}),
      ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  if (!response.ok) {
    const text = await response.text().catch(() => "");
    const detail = summarizeApiErrorBody(text, response.statusText);
    throw new ApiError(
      `API ${method} ${path} failed (${response.status}): ${detail}`,
      response.status,
    );
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return (await response.json()) as T;
}

export type StreamAgentOptions = Omit<StreamRunOptions, "url" | "body"> & {
  agentConfigId: string;
  message: string;
  sessionId?: string;
  preview?: boolean;
  path?: string;
};

/** Start a streaming agent run. Tenancy is derived server-side from the JWT. */
export function streamConfiguredAgent(
  options: StreamAgentOptions,
): Promise<{ lastEventId?: string }> {
  const {
    agentConfigId,
    message,
    sessionId,
    preview = false,
    path = "/v1/agents/tenant-agent/runs",
    ...rest
  } = options;

  const body = new FormData();
  body.set("message", message);
  body.set("stream", "true");
  body.set("agent_config_id", agentConfigId);
  body.set("session_id", sessionId ?? crypto.randomUUID());
  body.set("preview", String(preview));

  return streamAgentRun({
    ...rest,
    url: agentOsUrl(path),
    body,
  });
}

export type StreamPublicTargetOptions = Omit<
  StreamRunOptions,
  "url" | "body" | "accessToken"
> & {
  tenantSlug: string;
  message: string;
  sessionId?: string;
  guestId?: string;
};

/** Anonymous published-agent run via /public/t/... (no Clerk token). */
export function streamPublicAgent(
  options: StreamPublicTargetOptions & { agentSlug: string },
): Promise<{ lastEventId?: string }> {
  const {
    tenantSlug,
    agentSlug,
    message,
    sessionId,
    guestId = getOrCreateGuestId(),
    ...rest
  } = options;
  const body = new FormData();
  body.set("message", message);
  body.set("stream", "true");
  body.set("session_id", sessionId ?? crypto.randomUUID());
  return streamPublicRun({
    ...rest,
    guestId,
    url: agentOsUrl(`/public/t/${tenantSlug}/agents/${agentSlug}/runs`),
    body,
  });
}

/** Anonymous published-team run via /public/t/... */
export function streamPublicTeam(
  options: StreamPublicTargetOptions & { teamSlug: string },
): Promise<{ lastEventId?: string }> {
  const {
    tenantSlug,
    teamSlug,
    message,
    sessionId,
    guestId = getOrCreateGuestId(),
    ...rest
  } = options;
  const body = new FormData();
  body.set("message", message);
  body.set("stream", "true");
  body.set("session_id", sessionId ?? crypto.randomUUID());
  return streamPublicRun({
    ...rest,
    guestId,
    url: agentOsUrl(`/public/t/${tenantSlug}/teams/${teamSlug}/runs`),
    body,
  });
}

/** Anonymous published-workflow run via /public/t/... */
export function streamPublicWorkflow(
  options: StreamPublicTargetOptions & { workflowSlug: string },
): Promise<{ lastEventId?: string }> {
  const {
    tenantSlug,
    workflowSlug,
    message,
    sessionId,
    guestId = getOrCreateGuestId(),
    ...rest
  } = options;
  const body = new FormData();
  body.set("message", message);
  body.set("stream", "true");
  body.set("session_id", sessionId ?? crypto.randomUUID());
  return streamPublicRun({
    ...rest,
    guestId,
    url: agentOsUrl(`/public/t/${tenantSlug}/workflows/${workflowSlug}/runs`),
    body,
  });
}

export type StreamTeamOptions = Omit<StreamRunOptions, "url" | "body"> & {
  teamConfigId: string;
  message: string;
  sessionId?: string;
  preview?: boolean;
};

/** Start a persisted team run. Team membership is resolved server-side. */
export function streamConfiguredTeam(
  options: StreamTeamOptions,
): Promise<{ lastEventId?: string }> {
  const {
    teamConfigId,
    message,
    sessionId,
    preview = false,
    ...rest
  } = options;
  const body = new FormData();
  body.set("message", message);
  body.set("stream", "true");
  body.set("team_config_id", teamConfigId);
  body.set("session_id", sessionId ?? crypto.randomUUID());
  body.set("preview", String(preview));
  return streamAgentRun({
    ...rest,
    url: agentOsUrl("/v1/teams/tenant-team/runs"),
    body,
  });
}

export type StreamWorkflowOptions = Omit<StreamRunOptions, "url" | "body"> & {
  workflowConfigId: string;
  message: string;
  sessionId?: string;
  preview?: boolean;
};

/** Start a persisted workflow run with server-resolved pinned steps. */
export function streamConfiguredWorkflow(
  options: StreamWorkflowOptions,
): Promise<{ lastEventId?: string }> {
  const {
    workflowConfigId,
    message,
    sessionId,
    preview = false,
    ...rest
  } = options;
  const body = new FormData();
  body.set("message", message);
  body.set("stream", "true");
  body.set("workflow_config_id", workflowConfigId);
  body.set("session_id", sessionId ?? crypto.randomUUID());
  body.set("preview", String(preview));
  return streamAgentRun({
    ...rest,
    url: agentOsUrl("/v1/workflows/tenant-workflow/runs"),
    body,
  });
}

interface BackendSession {
  id: string;
  title: string;
  target_type: "agent" | "team" | "workflow";
  agent_version_id: string | null;
  team_version_id: string | null;
  workflow_version_id: string | null;
  last_run_id: string | null;
  status: ConversationSession["status"];
  updated_at: string;
  history?: { runs?: Array<Record<string, unknown>> };
}

function mapSession(row: BackendSession): ConversationSession {
  return {
    id: row.id,
    title: row.title,
    targetType: row.target_type,
    versionId:
      row.agent_version_id ??
      row.team_version_id ??
      row.workflow_version_id ??
      "",
    lastRunId: row.last_run_id,
    status: row.status,
    pausedForApproval: row.status === "paused",
    updatedAt: row.updated_at,
  };
}

export async function listChatSessions(
  accessToken: string,
  targetType: "agent" | "team" | "workflow",
  targetId: string,
): Promise<ConversationSession[]> {
  const params = new URLSearchParams({
    target_type: targetType,
    target_id: targetId,
  });
  const rows = await apiFetch<BackendSession[]>(`/api/sessions?${params}`, {
    accessToken,
  });
  return rows.map(mapSession);
}

export async function getChatSession(
  accessToken: string,
  sessionId: string,
): Promise<{ session: ConversationSession; messages: ChatMessage[] }> {
  const row = await apiFetch<BackendSession>(
    `/api/sessions/${encodeURIComponent(sessionId)}`,
    { accessToken },
  );
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
    if (userContent) {
      messages.push({
        id: `${String(run.run_id ?? index)}:user`,
        role: "user",
        content: userContent,
        createdAt: new Date(
          Number(run.created_at ?? 0) * 1000,
        ).toISOString(),
        status: "complete",
      });
    }
    const content =
      typeof run.content === "string" ? run.content : "";
    if (content) {
      messages.push({
        id: `${String(run.run_id ?? index)}:assistant`,
        role: "assistant",
        content,
        createdAt: new Date(
          Number(run.created_at ?? 0) * 1000,
        ).toISOString(),
        status:
          String(run.status).toUpperCase() === "PAUSED"
            ? "paused"
            : "complete",
      });
    }
  }
  return { session: mapSession(row), messages };
}

export async function deleteChatSession(
  accessToken: string,
  sessionId: string,
): Promise<void> {
  await apiFetch<void>(`/api/sessions/${encodeURIComponent(sessionId)}`, {
    accessToken,
    method: "DELETE",
  });
}
