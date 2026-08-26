import { streamAgentRun, streamPublicRun, type StreamRunOptions } from "./sse";
import { SseError } from "./types";
import type {
  ChatMessage,
  ConversationSession,
} from "@/lib/api/types";
import { unpackAccessContext } from "@/lib/auth/access-context";
import {
  getAccessToken,
  invalidateAccessTokenCache,
} from "@/lib/auth/token";
import { randomUuid } from "@/lib/random-uuid";

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
    const created = randomUuid().replace(/-/g, "").slice(0, 32);
    window.localStorage.setItem(GUEST_ID_KEY, created);
    return created;
  } catch {
    return randomUuid().replace(/-/g, "").slice(0, 32);
  }
}

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly detail?: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export function formatApiError(
  reason: unknown,
  fallback = "Request failed",
): string {
  if (reason instanceof SseError) {
    const detail = summarizeApiErrorBody(reason.bodyText ?? "", "");
    if (detail) {
      return humanizeApiError(
        reason.status ? `${reason.status}: ${detail}` : detail,
      );
    }
    if (reason.message.trim()) {
      return humanizeApiError(reason.message.trim());
    }
  }
  if (reason instanceof ApiError) {
    if (reason.status > 0 && reason.detail?.trim()) {
      return humanizeApiError(`${reason.status}: ${reason.detail.trim()}`);
    }
    if (reason.message.trim()) {
      return humanizeApiError(reason.message.trim());
    }
  }
  if (reason instanceof Error && reason.message.trim()) {
    return humanizeApiError(reason.message.trim());
  }
  return fallback;
}

function humanizeApiError(message: string): string {
  if (/error-user-attribute-read-only/i.test(message)) {
    // Email, display name, and role travel in one profile write, so none of
    // them stick when the realm rejects it. Only the password applies alone.
    return (
      "That sign-in field is read-only and cannot be changed. Any password you entered was still saved."
    );
  }
  if (
    /^failed to fetch$/i.test(message) ||
    /networkerror when attempting to fetch/i.test(message) ||
    /^load failed$/i.test(message)
  ) {
    return (
      "Could not reach the Atlas API. The browser never received a response " +
      "(backend down, CORS, DNS, or the connection was reset). " +
      "Remote MCP endpoints are called from the Atlas backend, not the browser."
    );
  }
  if (/select at least one reviewed mcp tool/i.test(message)) {
    return (
      "An MCP tool is attached but has no reviewed tools selected " +
      "(enumerate usually failed with 401 / no usable credential). " +
      "Detach that MCP tool from the team and keep the published Python groww_toolkit for Live trading."
    );
  }
  return message;
}

function describeFetchFailure(
  reason: unknown,
  method: string,
  path: string,
): ApiError {
  const target = agentOsUrl(path);
  const raw =
    reason instanceof Error && reason.message.trim()
      ? reason.message.trim()
      : String(reason);
  const aborted =
    (typeof DOMException !== "undefined" &&
      reason instanceof DOMException &&
      reason.name === "AbortError") ||
    (reason instanceof Error && reason.name === "AbortError");
  if (aborted) {
    return new ApiError(
      `Atlas API ${method} ${path} timed out or was cancelled (${target}).`,
      0,
      raw,
    );
  }
  return new ApiError(
    `Could not reach Atlas API ${method} ${path} (${target}). ` +
      "The browser never received a response — backend down, CORS, DNS, or the connection was reset. " +
      "Remote MCP hosts are not called from the browser.",
    0,
    raw,
  );
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
      if (record.error && typeof record.error === "object") {
        const nested = record.error as { message?: unknown };
        if (typeof nested.message === "string" && nested.message.trim()) {
          return truncateMessage(nested.message);
        }
      }
    }
  } catch {
    // Non-JSON bodies fall through to a truncated raw string.
  }
  return truncateMessage(text);
}

async function doApiFetch(
  path: string,
  accessToken: string,
  method: string,
  body: unknown | undefined,
  signal: AbortSignal | undefined,
): Promise<Response> {
  const access = unpackAccessContext(accessToken);
  const platformTenantId = path.startsWith("/admin/platform")
    ? undefined
    : access.platformTenantId;
  return fetch(agentOsUrl(path), {
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
  let response: Response;
  try {
    response = await doApiFetch(path, accessToken, method, body, signal);
  } catch (reason) {
    throw describeFetchFailure(reason, method, path);
  }

  // Mid-session JWT expiry: drop cache, refresh once, retry.
  if (response.status === 401 && typeof window !== "undefined") {
    invalidateAccessTokenCache();
    try {
      const fresh = await getAccessToken();
      if (fresh && fresh !== accessToken) {
        try {
          response = await doApiFetch(path, fresh, method, body, signal);
        } catch (reason) {
          throw describeFetchFailure(reason, method, path);
        }
      }
    } catch {
      // fall through to original 401
    }
  }

  if (!response.ok) {
    const text = await response.text().catch(() => "");
    const detail = summarizeApiErrorBody(text, response.statusText);
    throw new ApiError(
      `API ${method} ${path} failed (${response.status}): ${detail}`,
      response.status,
      detail,
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
  body.set("background", "true");
  body.set("agent_config_id", agentConfigId);
  body.set("session_id", sessionId ?? randomUuid());
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
  body.set("background", "true");
  body.set("session_id", sessionId ?? randomUuid());
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
  body.set("background", "true");
  body.set("session_id", sessionId ?? randomUuid());
  return streamPublicRun({
    ...rest,
    guestId,
    url: agentOsUrl(`/public/t/${tenantSlug}/workflows/${workflowSlug}/runs`),
    body,
  });
}

export type IdentityStatus = {
  verified: boolean;
  endUserId: string | null;
  email: string | null;
  displayName: string | null;
  metadata: Record<string, unknown> | null;
};

async function publicJsonFetch<T>(
  path: string,
  options: {
    method?: string;
    body?: unknown;
    guestId: string;
    signal?: AbortSignal;
  },
): Promise<T> {
  const { method = "GET", body, guestId, signal } = options;
  const response = await fetch(agentOsUrl(path), {
    method,
    signal,
    headers: {
      Accept: "application/json",
      "X-Guest-Id": guestId,
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
      detail,
    );
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

export async function getPublicIdentityStatus(options: {
  tenantSlug: string;
  sessionId: string;
  guestId?: string;
  signal?: AbortSignal;
}): Promise<IdentityStatus> {
  const guestId = options.guestId ?? getOrCreateGuestId();
  const params = new URLSearchParams({ session_id: options.sessionId });
  const row = await publicJsonFetch<{
    verified: boolean;
    end_user_id?: string | null;
    email?: string | null;
    display_name?: string | null;
    metadata?: Record<string, unknown> | null;
  }>(`/public/t/${options.tenantSlug}/identity/status?${params}`, {
    guestId,
    signal: options.signal,
  });
  return {
    verified: row.verified,
    endUserId: row.end_user_id ?? null,
    email: row.email ?? null,
    displayName: row.display_name ?? null,
    metadata: row.metadata ?? null,
  };
}

export async function requestPublicIdentityChallenge(options: {
  tenantSlug: string;
  sessionId: string;
  email: string;
  guestId?: string;
  signal?: AbortSignal;
}): Promise<{ email: string; expiresAt: string; debugCode: string | null }> {
  const guestId = options.guestId ?? getOrCreateGuestId();
  const row = await publicJsonFetch<{
    email: string;
    expires_at: string;
    debug_code?: string | null;
  }>(`/public/t/${options.tenantSlug}/identity/challenge`, {
    method: "POST",
    guestId,
    signal: options.signal,
    body: {
      email: options.email,
      session_id: options.sessionId,
    },
  });
  return {
    email: row.email,
    expiresAt: row.expires_at,
    debugCode: row.debug_code ?? null,
  };
}

export async function verifyPublicIdentity(options: {
  tenantSlug: string;
  sessionId: string;
  email: string;
  code: string;
  guestId?: string;
  signal?: AbortSignal;
}): Promise<IdentityStatus> {
  const guestId = options.guestId ?? getOrCreateGuestId();
  const row = await publicJsonFetch<{
    verified: boolean;
    end_user_id: string;
    email: string;
    display_name: string;
    metadata: Record<string, unknown>;
  }>(`/public/t/${options.tenantSlug}/identity/verify`, {
    method: "POST",
    guestId,
    signal: options.signal,
    body: {
      email: options.email,
      code: options.code,
      session_id: options.sessionId,
    },
  });
  return {
    verified: row.verified,
    endUserId: row.end_user_id,
    email: row.email,
    displayName: row.display_name,
    metadata: row.metadata,
  };
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
  body.set("background", "true");
  body.set("team_config_id", teamConfigId);
  body.set("session_id", sessionId ?? randomUuid());
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
  body.set("background", "true");
  body.set("workflow_config_id", workflowConfigId);
  body.set("session_id", sessionId ?? randomUuid());
  body.set("preview", String(preview));
  return streamAgentRun({
    ...rest,
    url: agentOsUrl("/v1/workflows/tenant-workflow/runs"),
    body,
  });
}

/** Cancel an in-flight Atlas /v1 run. */
export async function cancelConfiguredRun(options: {
  accessToken: string;
  kind: "agent" | "team" | "workflow";
  runId: string;
  sessionId: string;
  configId: string;
}): Promise<void> {
  const { accessToken, kind, runId, sessionId, configId } = options;
  const path =
    kind === "agent"
      ? `/v1/agents/tenant-agent/runs/${runId}/cancel`
      : kind === "team"
        ? `/v1/teams/tenant-team/runs/${runId}/cancel`
        : `/v1/workflows/tenant-workflow/runs/${runId}/cancel`;
  const body = new FormData();
  body.set("session_id", sessionId);
  if (kind === "agent") body.set("agent_config_id", configId);
  if (kind === "team") body.set("team_config_id", configId);
  if (kind === "workflow") body.set("workflow_config_id", configId);
  const response = await fetch(agentOsUrl(path), {
    method: "POST",
    headers: {
      Authorization: `Bearer ${accessToken}`,
    },
    body,
  });
  if (!response.ok) {
    throw new Error(`Cancel failed (${response.status})`);
  }
}

/** Cancel an in-flight public guest run. */
export async function cancelPublicRun(options: {
  tenantSlug: string;
  kind: "team" | "workflow";
  slug: string;
  runId: string;
  sessionId: string;
  guestId?: string;
}): Promise<void> {
  const {
    tenantSlug,
    kind,
    slug,
    runId,
    sessionId,
    guestId = getOrCreateGuestId(),
  } = options;
  const path =
    kind === "team"
      ? `/public/t/${tenantSlug}/teams/${slug}/runs/${runId}/cancel`
      : `/public/t/${tenantSlug}/workflows/${slug}/runs/${runId}/cancel`;
  const body = new FormData();
  body.set("session_id", sessionId);
  const response = await fetch(agentOsUrl(path), {
    method: "POST",
    headers: {
      "X-Guest-Id": guestId,
    },
    body,
  });
  if (!response.ok) {
    throw new Error(`Cancel failed (${response.status})`);
  }
}

/** Resume a background /v1 run after disconnect. */
export function resumeConfiguredRun(
  options: Omit<StreamRunOptions, "url" | "body"> & {
    kind: "agent" | "team" | "workflow";
    runId: string;
    sessionId: string;
    configId: string;
    lastEventId?: string;
  },
): Promise<{ lastEventId?: string }> {
  const { kind, runId, sessionId, configId, lastEventId, ...rest } = options;
  const path =
    kind === "agent"
      ? `/v1/agents/tenant-agent/runs/${runId}/resume`
      : kind === "team"
        ? `/v1/teams/tenant-team/runs/${runId}/resume`
        : `/v1/workflows/tenant-workflow/runs/${runId}/resume`;
  const body = new FormData();
  body.set("session_id", sessionId);
  if (kind === "agent") body.set("agent_config_id", configId);
  if (kind === "team") body.set("team_config_id", configId);
  if (kind === "workflow") body.set("workflow_config_id", configId);
  if (lastEventId != null && lastEventId !== "") {
    body.set("last_event_index", lastEventId);
  }
  return streamAgentRun({
    ...rest,
    lastEventId,
    url: agentOsUrl(path),
    body,
  });
}

/** Resume a background public guest run after disconnect. */
export function resumePublicRun(
  options: Omit<StreamRunOptions, "url" | "body" | "accessToken"> & {
    tenantSlug: string;
    kind: "team" | "workflow";
    slug: string;
    runId: string;
    sessionId: string;
    guestId?: string;
    lastEventId?: string;
  },
): Promise<{ lastEventId?: string }> {
  const {
    tenantSlug,
    kind,
    slug,
    runId,
    sessionId,
    guestId = getOrCreateGuestId(),
    lastEventId,
    ...rest
  } = options;
  const path =
    kind === "team"
      ? `/public/t/${tenantSlug}/teams/${slug}/runs/${runId}/resume`
      : `/public/t/${tenantSlug}/workflows/${slug}/runs/${runId}/resume`;
  const body = new FormData();
  body.set("session_id", sessionId);
  if (lastEventId != null && lastEventId !== "") {
    body.set("last_event_index", lastEventId);
  }
  return streamPublicRun({
    ...rest,
    guestId,
    lastEventId,
    url: agentOsUrl(path),
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
