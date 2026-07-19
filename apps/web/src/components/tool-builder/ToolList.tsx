"use client";

import Link from "next/link";

import { Badge } from "@/components/ui/Badge";
import type { ToolDefinition } from "@/lib/api/types";
import { formatRelative } from "@/lib/utils";

export function ToolList({ tools }: { tools: ToolDefinition[] }) {
  return (
    <div className="space-y-8">
      <section className="surface-panel relative overflow-hidden rounded-2xl p-6 md:p-8">
        <div className="pointer-events-none absolute inset-0 grid-noise opacity-60" />
        <div className="relative flex flex-wrap items-end justify-between gap-6">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.16em] text-teal">
              Reusable integrations
            </p>
            <h1 className="mt-2 font-display text-4xl font-semibold tracking-tight">
              Build tools once. Attach them anywhere.
            </h1>
            <p className="mt-3 max-w-2xl text-sm text-slate-muted">
              Build tenant-scoped HTTP, reviewed OpenAPI, allowlisted Python
              toolkit, and remote MCP integrations. Credentials stay server-side.
            </p>
          </div>
          <Link
            href="/admin/tools/new"
            className="rounded-lg bg-teal px-4 py-2.5 text-sm font-semibold text-white"
          >
            Create tool
          </Link>
        </div>
      </section>

      <section className="table-shell rounded-xl">
        <div className="hidden grid-cols-[1.4fr_0.7fr_1fr_0.6fr] gap-3 border-b border-line px-4 py-2.5 md:grid">
          <span className="th-label">Tool</span>
          <span className="th-label">Kind</span>
          <span className="th-label">Endpoint</span>
          <span className="th-label">Status</span>
        </div>
        <ul>
          {tools.map((tool) => (
            <li key={tool.id} className="border-b border-line/60 last:border-0">
              <Link
                href={`/admin/tools/${tool.id}`}
                className="grid items-center gap-3 px-4 py-2.5 transition hover:bg-mist/70 md:grid-cols-[1.4fr_0.7fr_1fr_0.6fr]"
              >
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium">{tool.name}</p>
                  <p className="mono-cell truncate text-slate-muted">/{tool.slug}</p>
                </div>
                <p className="mono-cell font-medium">
                  {tool.kind === "http"
                    ? tool.httpMethod ?? "HTTP"
                    : tool.kind.replace("_", " ")}
                </p>
                <p className="mono-cell truncate text-slate-muted">
                  {tool.kind === "http"
                    ? `${tool.baseUrl ?? ""}${tool.path ?? ""}`
                    : `${Object.keys(tool.config).length} provider settings`}
                </p>
                <div className="flex items-center gap-2">
                  <Badge dot tone={tool.active ? "success" : "neutral"}>
                    {tool.active ? "active" : "inactive"}
                  </Badge>
                  <span className="mono-cell text-slate-muted">
                    {formatRelative(tool.updatedAt)}
                  </span>
                </div>
              </Link>
            </li>
          ))}
          {tools.length === 0 ? (
            <li className="px-5 py-12 text-center text-sm text-slate-muted">
              No reusable tools yet.
            </li>
          ) : null}
        </ul>
      </section>
    </div>
  );
}
