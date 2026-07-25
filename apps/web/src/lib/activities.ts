import type {
  ActivityChannel,
  ActivityRow,
  AdminSession,
  AgentSummary,
  TeamSummary,
  WorkflowSummary,
} from "@/lib/api/types";
import type { AgentSchedule } from "@/lib/api/admin";

type NamedTarget = { id: string; name: string; slug: string };

function targetLookup(
  agents: AgentSummary[],
  teams: TeamSummary[],
  workflows: WorkflowSummary[],
): Map<string, NamedTarget> {
  const map = new Map<string, NamedTarget>();
  for (const agent of agents) {
    map.set(`agent:${agent.id}`, {
      id: agent.id,
      name: agent.name,
      slug: agent.slug,
    });
  }
  for (const team of teams) {
    map.set(`team:${team.id}`, {
      id: team.id,
      name: team.name,
      slug: team.slug,
    });
  }
  for (const workflow of workflows) {
    map.set(`workflow:${workflow.id}`, {
      id: workflow.id,
      name: workflow.name,
      slug: workflow.slug,
    });
  }
  return map;
}

function scheduleSessionIndex(schedules: AgentSchedule[]) {
  const bySession = new Map<string, { scheduleName: string }>();
  for (const schedule of schedules) {
    for (const run of schedule.runs ?? []) {
      if (!run.session_id) continue;
      bySession.set(run.session_id, { scheduleName: schedule.name });
    }
  }
  return bySession;
}

export function classifyActivityChannel(
  session: AdminSession,
  scheduledSessions: Map<string, { scheduleName: string }>,
): ActivityChannel {
  if (scheduledSessions.has(session.id)) return "scheduled";
  if (session.userId.startsWith("sa:")) return "api";
  return "live_chat";
}

export function buildActivityRows(input: {
  sessions: AdminSession[];
  schedules: AgentSchedule[];
  agents: AgentSummary[];
  teams: TeamSummary[];
  workflows: WorkflowSummary[];
}): ActivityRow[] {
  const names = targetLookup(input.agents, input.teams, input.workflows);
  const scheduled = scheduleSessionIndex(input.schedules);

  return input.sessions
    .map((session) => {
      const target = names.get(`${session.targetType}:${session.targetId}`);
      const scheduleHit = scheduled.get(session.id);
      const channel = classifyActivityChannel(session, scheduled);
      const personaName =
        target?.name ??
        `${session.targetType} ${session.targetId.slice(0, 8) || "—"}`;
      const taskName =
        scheduleHit?.scheduleName ??
        target?.slug ??
        target?.name ??
        session.targetType;

      return {
        id: session.id,
        title: session.title?.trim() || "Untitled session",
        userId: session.userId,
        personaName,
        personaType: session.targetType,
        taskName,
        status: session.status,
        channel,
        createdAt: session.createdAt,
        updatedAt: session.updatedAt,
        lastRunId: session.lastRunId ?? null,
        targetId: session.targetId,
        scheduleName: scheduleHit?.scheduleName ?? null,
      } satisfies ActivityRow;
    })
    .sort(
      (a, b) =>
        new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime(),
    );
}

export function activityStatusTone(
  status: ActivityRow["status"],
): "success" | "danger" | "warning" | "neutral" | "info" {
  if (status === "completed") return "success";
  if (status === "error" || status === "cancelled") return "danger";
  if (status === "paused" || status === "running") return "warning";
  if (status === "active") return "info";
  return "neutral";
}

const CHANNEL_LABELS: Record<ActivityChannel, string> = {
  live_chat: "Chat",
  scheduled: "Schedules",
  api: "Public API",
  email: "Email",
};

export function activityChannelLabel(channel: ActivityChannel): string {
  return CHANNEL_LABELS[channel] ?? channel;
}

export function activityTargetTypeLabel(
  type: ActivityRow["personaType"],
): string {
  if (type === "agent") return "Agent";
  if (type === "team") return "Team";
  return "Workflow";
}

export function formatActivityTime(iso: string): {
  absolute: string;
  zone: string;
} {
  const date = new Date(iso);
  const absolute = new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "medium",
  }).format(date);
  const zone =
    new Intl.DateTimeFormat(undefined, { timeZoneName: "short" })
      .formatToParts(date)
      .find((part) => part.type === "timeZoneName")?.value ?? "local";
  return { absolute, zone };
}

export function formatDurationMs(value: number | null | undefined): string {
  if (value == null) return "—";
  if (value < 1_000) return `${value} ms`;
  return `${(value / 1_000).toFixed(value < 10_000 ? 2 : 1)} s`;
}

export function extractTokenCounts(
  source: Record<string, unknown> | null | undefined,
): { input: number | null; output: number | null } {
  if (!source) return { input: null, output: null };
  const metrics =
    source.metrics && typeof source.metrics === "object"
      ? (source.metrics as Record<string, unknown>)
      : source;
  const input = Number(
    metrics.input_tokens ?? metrics.prompt_tokens ?? Number.NaN,
  );
  const output = Number(
    metrics.output_tokens ?? metrics.completion_tokens ?? Number.NaN,
  );
  return {
    input: Number.isFinite(input) ? input : null,
    output: Number.isFinite(output) ? output : null,
  };
}

/** Pull the primary run error string from trace output / spans. */
export function extractTraceError(source: {
  output?: Record<string, unknown> | null;
  spans?: Array<{ error: string | null }>;
} | null | undefined): string | null {
  if (!source) return null;
  const fromOutput = source.output?.error;
  if (typeof fromOutput === "string" && fromOutput.trim()) return fromOutput;
  const spanError = source.spans?.find((span) => span.error?.trim())?.error;
  return spanError?.trim() || null;
}

function unwrapNestedErrorMessage(raw: string): string {
  const trimmed = raw.trim();
  // "Error code: 413 - {'error': {'message': '...'}}" (Python/repr or JSON)
  const afterDash = trimmed.match(/^Error code:\s*\d+\s*-\s*(.+)$/is)?.[1];
  const candidate = (afterDash ?? trimmed).trim();

  const messageMatch = candidate.match(
    /['"]message['"]\s*:\s*['"]([\s\S]*?)['"]\s*(?:,|})/i,
  );
  if (messageMatch?.[1]) {
    return messageMatch[1].replace(/\\n/g, "\n").replace(/\\'/g, "'").replace(/\\"/g, '"');
  }

  try {
    const jsonish = candidate
      .replace(/'/g, '"')
      .replace(/\bNone\b/g, "null")
      .replace(/\bTrue\b/g, "true")
      .replace(/\bFalse\b/g, "false");
    const parsed = JSON.parse(jsonish) as Record<string, unknown>;
    const nested = parsed.error;
    if (nested && typeof nested === "object") {
      const msg = (nested as Record<string, unknown>).message;
      if (typeof msg === "string") return msg;
    }
    if (typeof parsed.message === "string") return parsed.message;
  } catch {
    /* not JSON */
  }
  return trimmed;
}

export type FriendlyRunError = {
  title: string;
  summary: string;
  raw: string;
};

/** Map provider RunError blobs (Groq TPM, etc.) into readable copy. */
export function formatTraceRunError(raw: string): FriendlyRunError {
  const message = unwrapNestedErrorMessage(raw);
  const tpm =
    /tokens per minute\s*\(TPM\)/i.test(message) ||
    (/rate_limit_exceeded/i.test(raw) && /TPM|tokens per minute/i.test(raw));

  if (tpm) {
    const limit = message.match(/Limit\s+(\d[\d,]*)/i)?.[1]?.replace(/,/g, "");
    const requested = message
      .match(/Requested\s+(\d[\d,]*)/i)?.[1]
      ?.replace(/,/g, "");
    const model =
      message.match(/model ['"]([^'"]+)['"]/i)?.[1] ??
      message.match(/model `([^`]+)`/i)?.[1];
    const quota =
      limit && requested
        ? ` This request used about ${requested} tokens against a ${limit} TPM limit.`
        : "";
    return {
      title: "Model rate limit exceeded",
      summary:
        `The model provider rejected this run because it exceeded the tokens-per-minute (TPM) quota` +
        (model ? ` for ${model}` : "") +
        `.${quota} Try fewer tools, a shorter prompt, a higher-limit model, or retry after the limit resets.`,
      raw,
    };
  }

  if (/rate_limit|429|too many requests/i.test(message)) {
    return {
      title: "Rate limited",
      summary:
        "The provider rate-limited this run. Wait a moment and retry, or switch to a model with higher capacity.",
      raw,
    };
  }

  if (/context.?length|maximum context|too many tokens|Request too large/i.test(message)) {
    return {
      title: "Request too large",
      summary:
        "The prompt, history, or tool schemas were too large for the model. Shorten instructions, drop tools, or use a larger-context model.",
      raw,
    };
  }

  if (/api.?key|authentication|unauthorized|invalid.?key/i.test(message)) {
    return {
      title: "Credential problem",
      summary:
        "The model provider rejected the API key. Check Credentials for this provider and try again.",
      raw,
    };
  }

  const short =
    message.length > 280 ? `${message.slice(0, 277).trimEnd()}…` : message;
  return {
    title: "Run failed",
    summary: short || "This run ended with an error.",
    raw,
  };
}
