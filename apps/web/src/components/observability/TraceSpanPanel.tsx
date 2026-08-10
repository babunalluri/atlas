"use client";

import { useMemo, useState } from "react";

import { Badge } from "@/components/ui/Badge";
import type { TraceDetail, TraceSpan } from "@/lib/api/admin";
import {
  ACTIVITY_DISPLAY_TIMEZONE,
  ACTIVITY_DISPLAY_ZONE_LABEL,
} from "@/lib/activities";
import { cn } from "@/lib/utils";

function duration(value: number | null) {
  if (value === null) return "running";
  if (value < 1_000) return `${value} ms`;
  return `${(value / 1_000).toFixed(value < 10_000 ? 2 : 1)} s`;
}

function formatAbsolute(iso: string): string {
  return new Intl.DateTimeFormat("en-US", {
    dateStyle: "short",
    timeStyle: "medium",
    timeZone: ACTIVITY_DISPLAY_TIMEZONE,
  }).format(new Date(iso));
}

function depth(span: TraceSpan, spans: TraceSpan[]) {
  const byId = new Map(spans.map((item) => [item.id, item]));
  const seen = new Set<string>();
  let parent = span.parentSpanId;
  let value = 0;
  while (parent && !seen.has(parent) && value < 8) {
    seen.add(parent);
    value += 1;
    parent = byId.get(parent)?.parentSpanId ?? null;
  }
  return value;
}

function statusTone(status: string): "success" | "danger" | "warning" | "neutral" {
  if (status === "completed") return "success";
  if (status === "error" || status === "cancelled") return "danger";
  if (status === "paused") return "warning";
  return "neutral";
}

/** Span waterfall + inspector for a single loaded trace detail. */
export function TraceSpanPanel({ detail }: { detail: TraceDetail }) {
  const [selectedSpan, setSelectedSpan] = useState<TraceSpan | null>(
    detail.spans[0] ?? null,
  );
  const bounds = useMemo(() => {
    const start = new Date(detail.startedAt).getTime();
    const end = detail.endedAt
      ? new Date(detail.endedAt).getTime()
      : Math.max(Date.now(), start + 1);
    return { start, width: Math.max(1, end - start) };
  }, [detail]);

  // Keep selection in sync when the loaded trace changes.
  const activeSpan =
    selectedSpan && detail.spans.some((span) => span.id === selectedSpan.id)
      ? selectedSpan
      : (detail.spans[0] ?? null);

  return (
    <div className="surface-panel min-w-0 overflow-hidden rounded-2xl">
      <div className="flex flex-col justify-between gap-3 border-b border-line px-5 py-4 md:flex-row md:items-center">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h2 className="truncate font-display text-xl font-semibold">
              {detail.name}
            </h2>
            <Badge tone={statusTone(detail.status)}>{detail.status}</Badge>
          </div>
          <p className="mt-1 truncate font-mono text-xs text-slate-muted">
            {detail.sessionId} · {detail.runId ?? detail.id}
          </p>
        </div>
        <div className="text-left md:text-right">
          <p className="font-mono text-lg font-semibold">
            {duration(detail.durationMs)}
          </p>
          <time
            dateTime={detail.startedAt}
            className="text-xs text-slate-muted"
          >
            {formatAbsolute(detail.startedAt)} {ACTIVITY_DISPLAY_ZONE_LABEL}
          </time>
        </div>
      </div>

      <div className="grid min-h-[420px] lg:grid-cols-[minmax(0,1fr)_300px]">
        <div className="min-w-0 border-b border-line lg:border-b-0 lg:border-r">
          <div className="grid grid-cols-[minmax(200px,0.85fr)_minmax(220px,1.15fr)] border-b border-line bg-fog/25 px-4 py-2">
            <span className="th-label">Span tree</span>
            <span className="th-label">Waterfall</span>
          </div>
          <div className="max-h-[520px] overflow-auto">
            {detail.spans.map((span) => {
              const start =
                ((new Date(span.startedAt).getTime() - bounds.start) /
                  bounds.width) *
                100;
              const width = Math.max(
                0.8,
                ((span.durationMs ?? 0) / bounds.width) * 100,
              );
              return (
                <button
                  key={span.id}
                  type="button"
                  onClick={() => setSelectedSpan(span)}
                  className={cn(
                    "grid w-full grid-cols-[minmax(200px,0.85fr)_minmax(220px,1.15fr)] border-b border-line/70 px-4 py-2.5 text-left",
                    activeSpan?.id === span.id
                      ? "bg-teal/8"
                      : "hover:bg-fog/25",
                  )}
                >
                  <div
                    className="min-w-0"
                    style={{ paddingLeft: `${depth(span, detail.spans) * 16}px` }}
                  >
                    <div className="flex items-center gap-2">
                      <span
                        className={cn(
                          "h-2 w-2 shrink-0 rounded-full",
                          span.status === "error" ? "bg-rose" : "bg-teal",
                        )}
                      />
                      <span className="truncate text-sm font-medium">
                        {span.name}
                      </span>
                    </div>
                    <span className="mt-0.5 block pl-4 font-mono text-[10px] uppercase tracking-wide text-slate-muted">
                      {span.kind}
                    </span>
                  </div>
                  <div className="flex items-center gap-2">
                    <div className="relative h-5 flex-1 overflow-hidden rounded bg-fog/45">
                      <span
                        className={cn(
                          "absolute top-1 h-3 rounded-sm",
                          span.status === "error"
                            ? "bg-rose"
                            : span.kind === "run"
                              ? "bg-teal"
                              : "bg-info",
                        )}
                        style={{
                          left: `${Math.max(0, Math.min(99, start))}%`,
                          width: `${Math.min(100, width)}%`,
                        }}
                      />
                    </div>
                    <span className="w-16 text-right font-mono text-[11px] text-slate-muted">
                      {duration(span.durationMs)}
                    </span>
                  </div>
                </button>
              );
            })}
            {detail.spans.length === 0 ? (
              <p className="px-4 py-10 text-center text-sm text-slate-muted">
                No spans recorded for this run.
              </p>
            ) : null}
          </div>
        </div>

        <aside className="min-w-0 p-4">
          <p className="th-label">Span inspector</p>
          {activeSpan ? (
            <div className="mt-3 space-y-4">
              <div>
                <p className="break-words text-sm font-semibold">
                  {activeSpan.name}
                </p>
                <p className="mt-1 font-mono text-xs text-slate-muted">
                  {activeSpan.id}
                </p>
              </div>
              {activeSpan.error ? (
                <div className="rounded-lg border border-rose/30 bg-rose/10 p-3 text-xs text-rose">
                  {activeSpan.error}
                </div>
              ) : null}
              {[
                ["Input", activeSpan.input],
                ["Output", activeSpan.output],
                ["Attributes", activeSpan.attributes],
              ].map(([label, value]) => (
                <div key={label as string}>
                  <p className="th-label mb-1.5">{label as string}</p>
                  <pre className="max-h-48 overflow-auto rounded-lg bg-ink p-3 font-mono text-[11px] leading-relaxed text-canvas">
                    {JSON.stringify(value, null, 2)}
                  </pre>
                </div>
              ))}
            </div>
          ) : (
            <p className="mt-3 text-sm text-slate-muted">Select a span.</p>
          )}
        </aside>
      </div>
    </div>
  );
}
