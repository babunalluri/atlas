"use client";

import { Link } from "@/i18n/navigation";
import { useEffect, useMemo, useState } from "react";

import {
  activityChannelLabel,
  activityStatusTone,
  activityTargetTypeLabel,
  extractTokenCounts,
  extractTraceError,
  formatActivityTime,
  formatDurationMs,
  formatTraceRunError,
} from "@/lib/activities";
import {
  getTrace,
  type TraceDetail,
  type TraceSummary,
} from "@/lib/api/admin";
import type { ActivityRow, ChatMessage } from "@/lib/api/types";
import { TraceSpanPanel } from "@/components/observability/TraceSpanPanel";
import { Badge } from "@/components/ui/Badge";
import { useAgentOsToken } from "@/lib/auth/token";
import { cn } from "@/lib/utils";

function shortUser(userId: string) {
  if (userId.startsWith("sa:")) return `API ${userId.slice(3, 11)}`;
  if (userId.startsWith("guest:")) {
    const rest = userId.slice("guest:".length);
    if (rest.startsWith("ip:")) return "Guest (anonymous)";
    return `Guest · ${rest.slice(0, 8)}`;
  }
  if (userId.startsWith("pending:") || userId.startsWith("invite:")) {
    return "Pending invite";
  }
  if (userId.length > 16) {
    return `User · ${userId.slice(0, 8)}…`;
  }
  return userId;
}

function displayUser(activity: ActivityRow) {
  return activity.userLabel || shortUser(activity.userId);
}

function MessageTurn({
  message,
  tokens,
  timeZone,
}: {
  message: ChatMessage;
  tokens?: { input: number | null; output: number | null };
  timeZone: string;
}) {
  const isUser = message.role === "user";
  const time = formatActivityTime(message.createdAt, timeZone);
  if (message.role === "system") {
    return (
      <p className="rounded-lg bg-fog px-3 py-2 text-xs text-slate-muted">
        {message.content}
      </p>
    );
  }
  return (
    <div className={cn("flex", isUser ? "justify-end" : "justify-start")}>
      <div
        className={cn(
          "max-w-[92%] rounded-2xl px-3.5 py-2.5 text-sm leading-relaxed",
          isUser
            ? "bg-ink text-canvas"
            : "border border-line bg-raised text-ink",
        )}
      >
        <div className="mb-1.5 flex flex-wrap items-center gap-2">
          <span className="text-[11px] font-semibold uppercase tracking-[0.06em] opacity-70">
            {isUser ? "User" : "Assistant"}
          </span>
          <time
            dateTime={message.createdAt}
            className="mono-cell text-[11px] opacity-70"
          >
            {time.absolute}
          </time>
          {!isUser && tokens?.input != null ? (
            <Badge tone="info">in {tokens.input}</Badge>
          ) : null}
          {!isUser && tokens?.output != null ? (
            <Badge tone="info">out {tokens.output}</Badge>
          ) : null}
          {!isUser && message.status && message.status !== "complete" ? (
            <Badge
              tone={
                message.status === "error"
                  ? "danger"
                  : message.status === "paused"
                    ? "warning"
                    : "neutral"
              }
            >
              {message.status}
            </Badge>
          ) : null}
        </div>
        <p className="whitespace-pre-wrap">{message.content || "—"}</p>
      </div>
    </div>
  );
}

export function TracesDetail({
  activity,
  messages,
  traces,
  initialTrace,
  preferredTraceId = null,
  timeZone = "UTC",
}: {
  activity: ActivityRow;
  messages: ChatMessage[];
  traces: TraceSummary[];
  initialTrace: TraceDetail | null;
  preferredTraceId?: string | null;
  timeZone?: string;
}) {
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [showRawError, setShowRawError] = useState(false);
  const [selectedTraceId, setSelectedTraceId] = useState(
    preferredTraceId ?? initialTrace?.id ?? traces[0]?.id ?? null,
  );
  const [detail, setDetail] = useState<TraceDetail | null>(initialTrace);
  const [loadingTrace, setLoadingTrace] = useState(false);
  const [traceError, setTraceError] = useState<string | null>(null);
  const { getAccessToken } = useAgentOsToken();

  useEffect(() => {
    if (!selectedTraceId) {
      setDetail(null);
      return;
    }
    if (detail?.id === selectedTraceId) return;

    let cancelled = false;
    setLoadingTrace(true);
    setTraceError(null);
    void (async () => {
      try {
        const next = await getTrace(await getAccessToken(), selectedTraceId);
        if (!cancelled) setDetail(next);
      } catch (reason) {
        if (!cancelled) {
          setTraceError(
            reason instanceof Error ? reason.message : "Trace could not be loaded",
          );
          setDetail(null);
        }
      } finally {
        if (!cancelled) setLoadingTrace(false);
      }
    })();

    return () => {
      cancelled = true;
    };
    // detail.id intentionally omitted — reload when selection changes
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedTraceId, getAccessToken]);

  const tokens = useMemo(
    () => extractTokenCounts(detail?.output ?? detail?.metadata),
    [detail],
  );
  const created = formatActivityTime(activity.createdAt, timeZone);
  const lastAssistantIndex = [...messages]
    .map((message, index) => (message.role === "assistant" ? index : -1))
    .filter((index) => index >= 0)
    .at(-1);

  const rawError = extractTraceError(detail);
  const friendlyError = rawError ? formatTraceRunError(rawError) : null;
  const showErrorBanner =
    Boolean(friendlyError) ||
    activity.status === "error" ||
    detail?.status === "error";

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-2 text-sm text-slate-muted">
        <Link href="/admin/traces" className="hover:text-ink">
          Traces
        </Link>
        <span aria-hidden>/</span>
        <span className="mono-cell text-ink">{activity.id.slice(0, 8)}…</span>
      </div>

      <header className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={activityStatusTone(activity.status)}>
            {activity.status}
          </Badge>
          <Badge tone="neutral">
            {activityChannelLabel(activity.channel)}
          </Badge>
        </div>
        <h1 className="mt-2 font-display text-3xl font-semibold tracking-tight md:text-4xl">
          {activity.title}
        </h1>
        <p className="mt-2 font-mono text-xs text-slate-muted">{activity.id}</p>
      </header>

      {activity.status === "paused" ? (
        <div className="rounded-xl border border-amber/40 bg-amber/10 px-4 py-3 text-sm text-ink">
          This run is waiting on a tool approval. Resolve it in{" "}
          <Link
            href="/admin/approvals"
            className="font-medium text-teal hover:underline"
          >
            Approvals
          </Link>
          .
        </div>
      ) : null}

      <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
        {[
          ["User", displayUser(activity)],
          [
            "Target",
            `${activity.personaName} · ${activityTargetTypeLabel(activity.personaType)}`,
          ],
          [activity.scheduleName ? "Schedule" : "Slug", activity.taskName],
          ["Created", `${created.absolute} ${created.zone}`],
          [
            "Tokens in / out",
            tokens.input != null || tokens.output != null
              ? `${tokens.input ?? "—"} / ${tokens.output ?? "—"}`
              : "—",
          ],
          ["Duration", formatDurationMs(detail?.durationMs)],
        ].map(([label, value]) => (
          <div key={label} className="surface-panel rounded-xl px-4 py-3">
            <p className="th-label">{label}</p>
            <p className="mt-1 truncate text-sm font-medium" title={String(value)}>
              {value}
            </p>
          </div>
        ))}
      </section>

      {showErrorBanner ? (
        <section
          className="rounded-2xl border border-rose/25 bg-rose/8 px-4 py-4"
          role="alert"
        >
          <p className="text-xs font-semibold uppercase tracking-[0.08em] text-rose">
            {friendlyError?.title ?? "Run failed"}
          </p>
          <p className="mt-2 text-sm leading-relaxed text-ink">
            {friendlyError?.summary ??
              "This session ended in an error. Open Advanced / Span debug for technical details."}
          </p>
          {friendlyError?.raw ? (
            <div className="mt-3">
              <button
                type="button"
                className="text-xs font-medium text-slate-muted underline-offset-2 hover:text-ink hover:underline"
                onClick={() => setShowRawError((value) => !value)}
              >
                {showRawError ? "Hide provider message" : "Show provider message"}
              </button>
              {showRawError ? (
                <pre className="mt-2 max-h-40 overflow-auto rounded-lg bg-fog/70 p-3 font-mono text-[11px] leading-relaxed text-ink-soft">
                  {friendlyError.raw}
                </pre>
              ) : null}
            </div>
          ) : null}
        </section>
      ) : null}

      <section className="surface-panel rounded-2xl">
        <div className="border-b border-line px-4 py-3">
          <p className="th-label">Conversation</p>
        </div>
        <div className="space-y-4 px-4 py-4">
          {messages.map((message, index) => (
            <MessageTurn
              key={message.id}
              message={message}
              tokens={index === lastAssistantIndex ? tokens : undefined}
              timeZone={timeZone}
            />
          ))}
          {messages.length === 0 ? (
            <p className="py-10 text-center text-sm text-slate-muted">
              No messages stored for this session yet.
            </p>
          ) : null}
        </div>
      </section>

      <section className="table-shell rounded-2xl">
        <button
          type="button"
          className="flex w-full items-center justify-between gap-3 border-b border-line px-4 py-3 text-left"
          onClick={() => setAdvancedOpen((value) => !value)}
        >
          <div>
            <p className="th-label">Advanced / Span debug</p>
            <p className="mt-0.5 text-xs text-slate-muted">
              Span tree, waterfall, and raw run I/O
              {traces.length ? ` · ${traces.length} linked run(s)` : ""}
            </p>
          </div>
          <span className="text-sm text-slate-muted">
            {advancedOpen ? "Hide" : "Show"}
          </span>
        </button>
        {advancedOpen ? (
          <div className="space-y-4 p-4">
            {traces.length > 0 ? (
              <ul className="flex flex-wrap gap-2">
                {traces.map((trace) => {
                  const selected = selectedTraceId === trace.id;
                  return (
                    <li key={trace.id}>
                      <button
                        type="button"
                        onClick={() => setSelectedTraceId(trace.id)}
                        className="inline-flex"
                      >
                        <Badge
                          tone={
                            selected
                              ? "info"
                              : activityStatusTone(trace.status)
                          }
                        >
                          {trace.name} · {trace.status}
                        </Badge>
                      </button>
                    </li>
                  );
                })}
              </ul>
            ) : null}

            {traceError ? (
              <p className="text-sm text-rose">{traceError}</p>
            ) : null}

            {loadingTrace ? (
              <p className="py-6 text-center text-sm text-slate-muted">
                Loading run details…
              </p>
            ) : !detail ? (
              <p className="py-6 text-center text-sm text-slate-muted">
                {traces.length === 0
                  ? "No linked runs for this session yet."
                  : "Select a run to inspect spans."}
              </p>
            ) : (
              <>
                <div className="grid gap-3 md:grid-cols-2">
                  <div>
                    <p className="th-label mb-1.5">Input</p>
                    <pre className="max-h-48 overflow-auto rounded-lg bg-fog/60 p-3 font-mono text-xs">
                      {JSON.stringify(detail.input, null, 2)}
                    </pre>
                  </div>
                  <div>
                    <p className="th-label mb-1.5">Output</p>
                    <pre className="max-h-48 overflow-auto rounded-lg bg-fog/60 p-3 font-mono text-xs">
                      {JSON.stringify(detail.output, null, 2)}
                    </pre>
                  </div>
                </div>

                <TraceSpanPanel
                  key={detail.id}
                  detail={detail}
                  timeZone={timeZone}
                />
              </>
            )}
          </div>
        ) : null}
      </section>
    </div>
  );
}
