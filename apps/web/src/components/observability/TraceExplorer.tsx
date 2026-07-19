"use client";

import { useMemo, useState } from "react";

import { Badge } from "@/components/ui/Badge";
import {
  getTrace,
  type TraceDetail,
  type TraceSpan,
  type TraceSummary,
} from "@/lib/api/admin";
import { useAgentOsToken } from "@/lib/auth/token";
import { cn, formatRelative } from "@/lib/utils";

function duration(value: number | null) {
  if (value === null) return "running";
  if (value < 1_000) return `${value} ms`;
  return `${(value / 1_000).toFixed(value < 10_000 ? 2 : 1)} s`;
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

export function TraceExplorer({
  initialTraces,
  initialDetail,
}: {
  initialTraces: TraceSummary[];
  initialDetail: TraceDetail | null;
}) {
  const [selectedId, setSelectedId] = useState(initialDetail?.id ?? null);
  const [detail, setDetail] = useState(initialDetail);
  const [status, setStatus] = useState("all");
  const [target, setTarget] = useState("all");
  const [selectedSpan, setSelectedSpan] = useState<TraceSpan | null>(
    initialDetail?.spans[0] ?? null,
  );
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { getAccessToken } = useAgentOsToken();

  const traces = initialTraces.filter(
    (trace) =>
      (status === "all" || trace.status === status) &&
      (target === "all" || trace.targetType === target),
  );
  const bounds = useMemo(() => {
    if (!detail) return { start: 0, width: 1 };
    const start = new Date(detail.startedAt).getTime();
    const end = detail.endedAt
      ? new Date(detail.endedAt).getTime()
      : Math.max(Date.now(), start + 1);
    return { start, width: Math.max(1, end - start) };
  }, [detail]);

  async function selectTrace(traceId: string) {
    setSelectedId(traceId);
    setLoading(true);
    setError(null);
    try {
      const next = await getTrace(await getAccessToken(), traceId);
      setDetail(next);
      setSelectedSpan(next.spans[0] ?? null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Trace could not be loaded");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-6">
      <header className="flex flex-col justify-between gap-4 lg:flex-row lg:items-end">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.14em] text-teal">
            Observability
          </p>
          <h1 className="font-display text-4xl font-semibold tracking-tight">
            Trace explorer
          </h1>
          <p className="mt-2 max-w-2xl text-sm text-slate-muted">
            Inspect tenant-scoped agent and team runs as span trees and timing
            waterfalls.
          </p>
        </div>
        <div className="flex gap-2">
          <select
            aria-label="Filter by status"
            value={status}
            onChange={(event) => setStatus(event.target.value)}
            className="rounded-lg border border-line bg-raised px-3 py-2 text-sm"
          >
            <option value="all">All statuses</option>
            <option value="completed">Completed</option>
            <option value="running">Running</option>
            <option value="paused">Paused</option>
            <option value="error">Errors</option>
          </select>
          <select
            aria-label="Filter by target"
            value={target}
            onChange={(event) => setTarget(event.target.value)}
            className="rounded-lg border border-line bg-raised px-3 py-2 text-sm"
          >
            <option value="all">Agents + teams</option>
            <option value="agent">Agents</option>
            <option value="team">Teams</option>
          </select>
        </div>
      </header>

      {error ? <p className="text-sm text-rose">{error}</p> : null}

      <div className="grid min-h-[620px] gap-4 xl:grid-cols-[340px_minmax(0,1fr)]">
        <aside className="table-shell rounded-2xl">
          <div className="border-b border-line px-4 py-3">
            <p className="th-label">{traces.length} recent traces</p>
          </div>
          <div className="max-h-[720px] overflow-y-auto">
            {traces.map((trace) => (
              <button
                key={trace.id}
                type="button"
                onClick={() => void selectTrace(trace.id)}
                className={cn(
                  "w-full border-b border-line px-4 py-3 text-left transition last:border-0",
                  selectedId === trace.id
                    ? "bg-teal/10"
                    : "hover:bg-fog/40",
                )}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate text-sm font-semibold">{trace.name}</span>
                  <Badge tone={statusTone(trace.status)}>{trace.status}</Badge>
                </div>
                <div className="mt-2 flex items-center justify-between text-xs text-slate-muted">
                  <span className="font-mono">{trace.targetType}</span>
                  <span className="tnum">{duration(trace.durationMs)}</span>
                </div>
                <p className="mt-1 truncate font-mono text-[11px] text-slate-muted">
                  {trace.runId ?? trace.id}
                </p>
                <p className="mt-1 text-[11px] text-slate-muted">
                  {trace.spanCount} spans · {formatRelative(trace.startedAt)}
                </p>
              </button>
            ))}
            {traces.length === 0 ? (
              <p className="px-4 py-12 text-center text-sm text-slate-muted">
                No traces match these filters.
              </p>
            ) : null}
          </div>
        </aside>

        <section className="surface-panel min-w-0 rounded-2xl">
          {detail ? (
            <>
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
                  <p className="text-xs text-slate-muted">
                    {new Date(detail.startedAt).toLocaleString()}
                  </p>
                </div>
              </div>

              <div className="grid min-h-[540px] lg:grid-cols-[minmax(0,1fr)_300px]">
                <div className="min-w-0 border-b border-line lg:border-b-0 lg:border-r">
                  <div className="grid grid-cols-[minmax(200px,0.85fr)_minmax(220px,1.15fr)] border-b border-line bg-fog/25 px-4 py-2">
                    <span className="th-label">Span tree</span>
                    <span className="th-label">Waterfall</span>
                  </div>
                  <div className="max-h-[610px] overflow-auto">
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
                            selectedSpan?.id === span.id
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
                            <p className="mt-0.5 pl-4 font-mono text-[10px] uppercase tracking-wide text-slate-muted">
                              {span.kind}
                            </p>
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
                  </div>
                </div>

                <aside className="min-w-0 p-4">
                  <p className="th-label">Span inspector</p>
                  {selectedSpan ? (
                    <div className="mt-3 space-y-4">
                      <div>
                        <p className="break-words text-sm font-semibold">
                          {selectedSpan.name}
                        </p>
                        <p className="mt-1 font-mono text-xs text-slate-muted">
                          {selectedSpan.id}
                        </p>
                      </div>
                      {selectedSpan.error ? (
                        <div className="rounded-lg border border-rose/30 bg-rose/10 p-3 text-xs text-rose">
                          {selectedSpan.error}
                        </div>
                      ) : null}
                      {[
                        ["Input", selectedSpan.input],
                        ["Output", selectedSpan.output],
                        ["Attributes", selectedSpan.attributes],
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
            </>
          ) : (
            <div className="flex min-h-[620px] items-center justify-center text-sm text-slate-muted">
              {loading ? "Loading trace…" : "Run an agent or team to create a trace."}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
