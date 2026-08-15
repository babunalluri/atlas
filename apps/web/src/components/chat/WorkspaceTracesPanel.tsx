"use client";

import { useTranslations } from "next-intl";
import { useCallback, useEffect, useState } from "react";

import {
  activityStatusTone,
  activityTargetTypeLabel,
  extractTraceError,
  formatActivityTime,
  formatDurationMs,
  formatTraceRunError,
} from "@/lib/activities";
import { getChatSession } from "@/lib/agentos/client";
import {
  getMyTrace,
  listMyTraces,
  type UserTraceDetail,
  type UserTraceSummary,
} from "@/lib/api/admin";
import type { ChatMessage } from "@/lib/api/types";
import { TraceSpanPanel } from "@/components/observability/TraceSpanPanel";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { CloseIcon } from "@/components/ui/icons";
import { useAgentOsToken } from "@/lib/auth/token";
import { cn } from "@/lib/utils";

function chatLabel(row: UserTraceSummary): string {
  const target = row.targetName || activityTargetTypeLabel(row.targetType);
  return row.sessionTitle ? `${target} · ${row.sessionTitle}` : target;
}

function messagesFromTrace(detail: UserTraceDetail): ChatMessage[] {
  const messages: ChatMessage[] = [];
  const input =
    typeof detail.input.message === "string" ? detail.input.message.trim() : "";
  if (input) {
    messages.push({
      id: `${detail.id}:user`,
      role: "user",
      content: input,
      createdAt: detail.startedAt,
      status: "complete",
    });
  }
  const output =
    typeof detail.output.content === "string" ? detail.output.content.trim() : "";
  if (output) {
    messages.push({
      id: `${detail.id}:assistant`,
      role: "assistant",
      content: output,
      createdAt: detail.endedAt ?? detail.startedAt,
      status: detail.status === "error" ? "error" : "complete",
    });
  }
  return messages;
}

export function WorkspaceTracesButton() {
  const t = useTranslations("common.traces");
  const [open, setOpen] = useState(false);

  return (
    <>
      <Button
        type="button"
        size="sm"
        variant="secondary"
        aria-label={t("open")}
        onClick={() => setOpen(true)}
      >
        {t("open")}
      </Button>
      {open ? <WorkspaceTracesPanel onClose={() => setOpen(false)} /> : null}
    </>
  );
}

export function WorkspaceTracesPanel({ onClose }: { onClose: () => void }) {
  const t = useTranslations("common.traces");
  const tCommon = useTranslations("common");
  const { getAccessToken } = useAgentOsToken();
  const [rows, setRows] = useState<UserTraceSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<UserTraceDetail | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    const token = await getAccessToken();
    setRows(await listMyTraces(token, { limit: 100 }));
  }, [getAccessToken]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    void (async () => {
      try {
        await refresh();
        if (!cancelled) setError(null);
      } catch (reason) {
        if (!cancelled) {
          setError(
            reason instanceof Error ? reason.message : t("loadFailed"),
          );
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [refresh, t]);

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      setMessages([]);
      setDetailError(null);
      return;
    }
    let cancelled = false;
    setDetailLoading(true);
    setDetailError(null);
    void (async () => {
      try {
        const token = await getAccessToken();
        const next = await getMyTrace(token, selectedId);
        let nextMessages = messagesFromTrace(next);
        try {
          const session = await getChatSession(token, next.sessionId);
          if (session.messages.length > 0) nextMessages = session.messages;
        } catch {
          // Session history is optional; trace input/output is enough.
        }
        if (!cancelled) {
          setDetail(next);
          setMessages(nextMessages);
        }
      } catch (reason) {
        if (!cancelled) {
          setDetail(null);
          setMessages([]);
          setDetailError(
            reason instanceof Error ? reason.message : t("detailFailed"),
          );
        }
      } finally {
        if (!cancelled) setDetailLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [getAccessToken, selectedId, t]);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") {
        if (selectedId) setSelectedId(null);
        else onClose();
      }
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose, selectedId]);

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-ink/40">
      <button
        type="button"
        className="absolute inset-0 cursor-default"
        aria-label={t("close")}
        onClick={onClose}
      />
      <aside
        role="dialog"
        aria-label={t("title")}
        className="relative flex h-full w-full max-w-3xl flex-col border-l border-line bg-canvas shadow-xl"
      >
        <header className="flex shrink-0 items-start justify-between gap-3 border-b border-line px-4 py-3">
          <div className="min-w-0">
            {selectedId ? (
              <button
                type="button"
                className="text-xs font-medium text-slate-muted hover:text-ink"
                onClick={() => setSelectedId(null)}
              >
                ← {t("back")}
              </button>
            ) : (
              <p className="text-xs font-semibold uppercase tracking-[0.12em] text-slate-muted">
                {t("open")}
              </p>
            )}
            <h2 className="mt-0.5 font-display text-lg font-semibold tracking-tight">
              {t("title")}
            </h2>
            <p className="mt-0.5 text-xs text-slate-muted">{t("hint")}</p>
          </div>
          <Button
            type="button"
            size="icon"
            variant="ghost"
            aria-label={t("close")}
            onClick={onClose}
          >
            <CloseIcon />
          </Button>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
          {selectedId ? (
            <TraceDetailBody
              loading={detailLoading}
              error={detailError}
              detail={detail}
              messages={messages}
            />
          ) : loading ? (
            <p className="py-10 text-center text-sm text-slate-muted">
              {tCommon("loading")}
            </p>
          ) : error ? (
            <p className="py-10 text-center text-sm text-amber">{error}</p>
          ) : rows.length === 0 ? (
            <p className="py-10 text-center text-sm text-slate-muted">
              {t("empty")}
            </p>
          ) : (
            <TraceList rows={rows} onSelect={setSelectedId} />
          )}
        </div>
      </aside>
    </div>
  );
}

function TraceList({
  rows,
  onSelect,
}: {
  rows: UserTraceSummary[];
  onSelect: (id: string) => void;
}) {
  const t = useTranslations("common.traces");
  return (
    <div className="overflow-hidden rounded-xl border border-line">
      <div className="grid grid-cols-[minmax(140px,1fr)_minmax(160px,1.3fr)_80px] gap-3 border-b border-line px-3 py-2">
        <span className="th-label">{t("time")}</span>
        <span className="th-label">{t("chat")}</span>
        <span className="th-label text-right">{t("status")}</span>
      </div>
      <ul>
        {rows.map((row) => {
          const time = formatActivityTime(row.startedAt);
          return (
            <li key={row.id} className="border-b border-line/60 last:border-0">
              <button
                type="button"
                onClick={() => onSelect(row.id)}
                className="grid w-full grid-cols-[minmax(140px,1fr)_minmax(160px,1.3fr)_80px] items-center gap-3 px-3 py-3 text-left hover:bg-fog/40"
              >
                <p
                  className="mono-cell text-xs text-slate-muted"
                  title={time.absolute}
                >
                  {time.absolute}
                </p>
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium">{chatLabel(row)}</p>
                  {row.error ? (
                    <p className="mt-0.5 truncate text-[11px] text-rose">
                      {row.error}
                    </p>
                  ) : null}
                </div>
                <div className="flex justify-end">
                  <Badge tone={activityStatusTone(row.status)}>{row.status}</Badge>
                </div>
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function TraceDetailBody({
  loading,
  error,
  detail,
  messages,
}: {
  loading: boolean;
  error: string | null;
  detail: UserTraceDetail | null;
  messages: ChatMessage[];
}) {
  const t = useTranslations("common.traces");
  const tCommon = useTranslations("common");
  if (loading) {
    return (
      <p className="py-10 text-center text-sm text-slate-muted">
        {tCommon("loading")}
      </p>
    );
  }
  if (error) {
    return <p className="py-10 text-center text-sm text-rose">{error}</p>;
  }
  if (!detail) {
    return (
      <p className="py-10 text-center text-sm text-slate-muted">{t("empty")}</p>
    );
  }

  const rawError = extractTraceError(detail) ?? detail.error;
  const friendlyError = rawError ? formatTraceRunError(rawError) : null;
  const started = formatActivityTime(detail.startedAt);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={activityStatusTone(detail.status)}>{detail.status}</Badge>
        <Badge tone="neutral">{activityTargetTypeLabel(detail.targetType)}</Badge>
      </div>
      <div>
        <h3 className="font-display text-xl font-semibold tracking-tight">
          {chatLabel(detail)}
        </h3>
        <p className="mt-1 text-xs text-slate-muted">
          {started.absolute} {started.zone}
          {detail.durationMs != null
            ? ` · ${formatDurationMs(detail.durationMs)}`
            : ""}
        </p>
      </div>

      {friendlyError ? (
        <section
          className="rounded-xl border border-rose/25 bg-rose/8 px-4 py-3"
          role="alert"
        >
          <p className="text-xs font-semibold uppercase tracking-[0.08em] text-rose">
            {friendlyError.title}
          </p>
          <p className="mt-1 text-sm leading-relaxed text-ink">
            {friendlyError.summary}
          </p>
        </section>
      ) : null}

      <section className="rounded-xl border border-line">
        <div className="border-b border-line px-4 py-2.5">
          <p className="th-label">{t("conversation")}</p>
        </div>
        <div className="space-y-3 px-4 py-3">
          {messages.map((message) => (
            <UserMessageTurn key={message.id} message={message} />
          ))}
          {messages.length === 0 ? (
            <p className="py-8 text-center text-sm text-slate-muted">
              {t("noMessages")}
            </p>
          ) : null}
        </div>
      </section>

      <section>
        <p className="th-label mb-2">{t("spans")}</p>
        <TraceSpanPanel detail={detail} />
      </section>
    </div>
  );
}

function UserMessageTurn({ message }: { message: ChatMessage }) {
  const t = useTranslations("common.traces");
  const isUser = message.role === "user";
  const time = formatActivityTime(message.createdAt);
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
        <div className="mb-1 flex flex-wrap items-center gap-2">
          <span className="text-[11px] font-semibold uppercase tracking-[0.06em] opacity-70">
            {isUser ? t("user") : t("assistant")}
          </span>
          <time dateTime={message.createdAt} className="mono-cell text-[11px] opacity-70">
            {time.absolute}
          </time>
        </div>
        <p className="whitespace-pre-wrap">{message.content || "—"}</p>
      </div>
    </div>
  );
}
