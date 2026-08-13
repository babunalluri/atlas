import { Badge } from "@/components/ui/Badge";
import type { MetricsDashboard as Dashboard } from "@/lib/api/admin";

function percent(value: number) {
  return `${(value * 100).toFixed(1)}%`;
}

function duration(value: number | null) {
  if (value === null) return "—";
  return value < 1000 ? `${value} ms` : `${(value / 1000).toFixed(2)} s`;
}

const NESTED_TOOL_KEYS = ["tool_name", "name", "tool"] as const;

function parseMaybeObject(raw: string): Record<string, unknown> | null {
  const trimmed = raw.trim();
  if (!trimmed.startsWith("{") || !trimmed.endsWith("}")) return null;
  try {
    const json = JSON.parse(trimmed) as unknown;
    if (json && typeof json === "object" && !Array.isArray(json)) {
      return json as Record<string, unknown>;
    }
  } catch {
    // Python dict repr uses single quotes.
  }
  try {
    const json = JSON.parse(
      trimmed
        .replace(/\bNone\b/g, "null")
        .replace(/\bTrue\b/g, "true")
        .replace(/\bFalse\b/g, "false")
        .replace(/'/g, '"'),
    ) as unknown;
    if (json && typeof json === "object" && !Array.isArray(json)) {
      return json as Record<string, unknown>;
    }
  } catch {
    return null;
  }
  return null;
}

export function readableToolName(raw: unknown, depth = 0): string {
  const extracted = extractToolName(raw, depth);
  return extracted ?? "Unknown tool";
}

function extractToolName(value: unknown, depth: number): string | null {
  if (value == null || depth > 4) return null;
  if (typeof value === "object" && !Array.isArray(value)) {
    const record = value as Record<string, unknown>;
    for (const key of NESTED_TOOL_KEYS) {
      const nested = extractToolName(record[key], depth + 1);
      if (nested) return nested;
    }
    return null;
  }
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  if (!trimmed) return null;
  if (
    (trimmed.startsWith("{") && trimmed.endsWith("}")) ||
    trimmed.includes("tool_call_id")
  ) {
    const parsed = parseMaybeObject(trimmed);
    if (parsed) return extractToolName(parsed, depth + 1);
    const named =
      /['"](?:tool_name|name|tool)['"]\s*:\s*['"]([^'"]+)['"]/.exec(trimmed);
    if (named?.[1] && !named[1].startsWith("call_")) return named[1];
    return null;
  }
  if (/^toolcall/i.test(trimmed)) return null;
  return trimmed;
}

export function MetricsDashboard({
  data,
  compact = false,
  embedded = false,
}: {
  data: Dashboard;
  compact?: boolean;
  embedded?: boolean;
}) {
  const showHero = !compact && !embedded;
  const cards = compact
    ? []
    : [
        ["Runs", data.kpis.runs.toLocaleString()],
        ["Success", percent(data.kpis.success_rate)],
        ["Error rate", percent(data.kpis.error_rate)],
        ["P95 latency", duration(data.kpis.latency_p95_ms)],
        ["Sessions", data.kpis.unique_sessions.toLocaleString()],
        ["Approval waits", data.kpis.approval_waits.toLocaleString()],
      ];

  return (
    <div className={showHero ? "space-y-6" : "space-y-4"}>
      {showHero ? (
        <header>
          <p className="text-xs font-semibold uppercase tracking-[0.16em] text-teal">
            Atlas operations
          </p>
          <h1 className="mt-2 font-display text-4xl font-semibold tracking-tight">
            Tenant metrics
          </h1>
          <p className="mt-2 max-w-2xl text-sm text-slate-muted">
            Durable {data.range_days}-day aggregates derived from tenant traces,
            sessions, tool spans, and approval waits.
          </p>
        </header>
      ) : null}

      {compact ? null : (
        <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-6">
          {cards.map(([label, value]) => (
            <div key={label} className="surface-panel rounded-xl p-4">
              <p className="th-label">{label}</p>
              <p className="mt-2 text-xl font-semibold tnum">{value}</p>
            </div>
          ))}
        </section>
      )}

      <div className="grid gap-4 xl:grid-cols-[1.5fr_.7fr]">
        <section className="table-shell rounded-xl">
          <div className="grid grid-cols-[1.3fr_.55fr_.55fr_.55fr_.55fr] gap-3 border-b border-line px-4 py-2.5">
            <span className="th-label">Target</span>
            <span className="th-label">Runs</span>
            <span className="th-label">Success</span>
            <span className="th-label">P95</span>
            <span className="th-label text-right">Waits</span>
          </div>
          {data.top_targets.map((target) => (
            <div
              key={`${target.target_type}:${target.target_id}`}
              className="grid grid-cols-[1.3fr_.55fr_.55fr_.55fr_.55fr] items-center gap-3 border-b border-line/60 px-4 py-3 last:border-0"
            >
              <div className="min-w-0">
                <p className="truncate text-sm font-medium">{target.name}</p>
                <Badge className="mt-1">{target.target_type}</Badge>
              </div>
              <p className="mono-cell">{target.run_count}</p>
              <p className="mono-cell">{percent(target.success_rate)}</p>
              <p className="mono-cell">{duration(target.latency_p95_ms)}</p>
              <p className="mono-cell text-right">{target.approval_waits}</p>
            </div>
          ))}
          {data.top_targets.length === 0 ? (
            <p className="px-4 py-10 text-center text-sm text-slate-muted">
              No tenant traces in this range.
            </p>
          ) : null}
        </section>

        <section className="table-shell rounded-xl">
          <div className="border-b border-line px-4 py-3">
            <p className="th-label">Top tools</p>
          </div>
          {data.top_tools.map((tool, index) => {
            const label = readableToolName(tool.name);
            return (
              <div
                key={`${index}:${tool.name}`}
                className="flex items-center justify-between gap-3 border-b border-line/60 px-4 py-3 last:border-0"
              >
                <p className="min-w-0 truncate text-sm" title={label}>
                  <span className="mr-2 font-mono text-slate-muted">{index + 1}</span>
                  {label}
                </p>
                <Badge tone="info">{tool.count}</Badge>
              </div>
            );
          })}
          {data.top_tools.length === 0 ? (
            <p className="px-4 py-10 text-center text-sm text-slate-muted">
              No tool calls recorded.
            </p>
          ) : null}
        </section>
      </div>

      <section className="table-shell rounded-xl">
        <div className="grid grid-cols-[1fr_.5fr_.5fr_.5fr_.6fr] gap-3 border-b border-line px-4 py-2.5">
          <span className="th-label">UTC day</span>
          <span className="th-label">Runs</span>
          <span className="th-label">Success</span>
          <span className="th-label">Errors</span>
          <span className="th-label text-right">P95 latency</span>
        </div>
        {data.daily.map((day) => (
          <div
            key={day.date}
            className="grid grid-cols-[1fr_.5fr_.5fr_.5fr_.6fr] gap-3 border-b border-line/60 px-4 py-2.5 last:border-0"
          >
            <p className="mono-cell">{new Date(day.date).toISOString().slice(0, 10)}</p>
            <p className="mono-cell">{day.runs}</p>
            <p className="mono-cell">{day.success_count}</p>
            <p className="mono-cell">{day.error_count}</p>
            <p className="mono-cell text-right">{duration(day.latency_p95_ms)}</p>
          </div>
        ))}
      </section>
    </div>
  );
}
