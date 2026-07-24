"use client";

import Link from "next/link";
import { useMemo, useState } from "react";

import { Badge } from "@/components/ui/Badge";
import { Button, buttonClassName } from "@/components/ui/Button";
import { Input } from "@/components/ui/Field";
import { TrashIcon } from "@/components/ui/icons";
import { deleteToolDefinition } from "@/lib/api/admin";
import type { ToolDefinition } from "@/lib/api/types";
import { useAgentOsToken } from "@/lib/auth/token";
import { cn, formatRelative } from "@/lib/utils";

type ToolFamily = "api" | "python" | "mcp" | "legacy";
type StatusFilter = "all" | "active" | "inactive";

const PAGE_SIZE = 25;

const FAMILY_KINDS: Record<ToolFamily, ReadonlyArray<ToolDefinition["kind"]>> = {
  api: ["http", "openapi"],
  python: ["tenant_python"],
  mcp: ["mcp"],
  legacy: ["python_toolkit", "custom_python"],
};

const FAMILY_CREATE_KIND: Record<Exclude<ToolFamily, "legacy">, ToolDefinition["kind"]> =
  {
    api: "http",
    python: "tenant_python",
    mcp: "mcp",
  };

const PRIMARY_TABS: Array<{ id: Exclude<ToolFamily, "legacy">; label: string }> =
  [
    { id: "api", label: "API Tools" },
    { id: "python", label: "Python Tools" },
    { id: "mcp", label: "MCP Tools" },
  ];

function kindLabel(tool: ToolDefinition): string {
  switch (tool.kind) {
    case "http":
      return tool.httpMethod ?? "HTTP";
    case "openapi":
      return "OpenAPI";
    case "tenant_python":
      return "Editable Python";
    case "mcp":
      return "MCP";
    case "python_toolkit":
      return "Python Toolkit";
    case "custom_python":
      return "Custom Python";
    default:
      return String(tool.kind).replaceAll("_", " ");
  }
}

function truncate(text: string, max = 120): string {
  const trimmed = text.trim();
  if (trimmed.length <= max) return trimmed;
  return `${trimmed.slice(0, max - 1)}…`;
}

export function ToolList({ tools: initialTools }: { tools: ToolDefinition[] }) {
  const { getAccessToken } = useAgentOsToken();
  const [tools, setTools] = useState(initialTools);
  const [error, setError] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [family, setFamily] = useState<ToolFamily>("api");
  const [q, setQ] = useState("");
  const [status, setStatus] = useState<StatusFilter>("all");
  const [page, setPage] = useState(1);

  const legacyCount = useMemo(
    () =>
      tools.filter((tool) => FAMILY_KINDS.legacy.includes(tool.kind)).length,
    [tools],
  );

  const tabs = useMemo(() => {
    const rows: Array<{ id: ToolFamily; label: string }> = [...PRIMARY_TABS];
    if (legacyCount > 0) {
      rows.push({ id: "legacy", label: "Legacy" });
    }
    return rows;
  }, [legacyCount]);

  const filtered = useMemo(() => {
    const query = q.trim().toLowerCase();
    const kinds = FAMILY_KINDS[family];
    return tools.filter((tool) => {
      if (!kinds.includes(tool.kind)) return false;
      if (status === "active" && !tool.active) return false;
      if (status === "inactive" && tool.active) return false;
      if (!query) return true;
      const haystack = [tool.name, tool.slug, tool.description]
        .join(" ")
        .toLowerCase();
      return haystack.includes(query);
    });
  }, [family, q, status, tools]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const safePage = Math.min(page, totalPages);
  const start = (safePage - 1) * PAGE_SIZE;
  const pageItems = filtered.slice(start, start + PAGE_SIZE);
  const end = Math.min(filtered.length, start + PAGE_SIZE);

  const createHref =
    family === "legacy"
      ? "/admin/tools/new?kind=http"
      : `/admin/tools/new?kind=${FAMILY_CREATE_KIND[family]}`;

  async function onDelete(tool: ToolDefinition) {
    if (!window.confirm(`Delete “${tool.name}”? This cannot be undone.`)) {
      return;
    }
    setDeletingId(tool.id);
    setError(null);
    try {
      await deleteToolDefinition(await getAccessToken(), tool.id);
      setTools((prev) => prev.filter((item) => item.id !== tool.id));
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Failed to delete tool",
      );
    } finally {
      setDeletingId(null);
    }
  }

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-display text-2xl font-semibold tracking-tight">
            Tools
          </h1>
          <p className="mt-0.5 text-sm text-slate-muted">
            API, Python, and MCP capabilities agents can call.
          </p>
        </div>
        <Link
          href={createHref}
          className={buttonClassName({ variant: "accent" })}
        >
          + Create
        </Link>
      </header>
      {error ? <p className="text-sm text-rose">{error}</p> : null}

      <div
        role="tablist"
        aria-label="Tool family"
        className="flex flex-wrap gap-1 border-b border-line"
      >
        {tabs.map((tab) => {
          const selected = family === tab.id;
          return (
            <button
              key={tab.id}
              type="button"
              role="tab"
              aria-selected={selected}
              onClick={() => {
                setFamily(tab.id);
                setPage(1);
              }}
              className={cn(
                "-mb-px border-b-2 px-3 py-2 text-sm font-medium transition",
                selected
                  ? "border-teal text-ink"
                  : "border-transparent text-slate-muted hover:text-ink",
              )}
            >
              {tab.label}
            </button>
          );
        })}
      </div>

      <section className="table-shell rounded-xl">
        <div className="space-y-3 border-b border-line px-4 py-3">
          <div className="flex flex-wrap items-center gap-2">
            <Input
              value={q}
              placeholder="Search by name or slug…"
              className="min-w-[220px] flex-1"
              onChange={(event) => {
                setQ(event.target.value);
                setPage(1);
              }}
            />
            {(
              [
                ["all", "All"],
                ["active", "Active"],
                ["inactive", "Inactive"],
              ] as const
            ).map(([value, label]) => (
              <button
                key={value}
                type="button"
                onClick={() => {
                  setStatus(value);
                  setPage(1);
                }}
                className={cn(
                  "rounded-md border px-2.5 py-1.5 text-xs font-medium transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ink/20 focus-visible:ring-offset-1 focus-visible:ring-offset-canvas",
                  status === value
                    ? "border-line-strong bg-mist text-ink"
                    : "border-transparent bg-raised text-slate-muted hover:bg-mist",
                )}
              >
                {label}
              </button>
            ))}
          </div>
          <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-slate-muted">
            <p>
              {filtered.length === 0
                ? "No tools match"
                : `Showing ${start + 1}–${end} of ${filtered.length} tools`}
            </p>
            <div className="flex items-center gap-2">
              <Button
                size="sm"
                variant="secondary"
                disabled={safePage <= 1}
                onClick={() => setPage((current) => Math.max(1, current - 1))}
              >
                Previous
              </Button>
              <span className="mono-cell">
                {safePage} / {totalPages}
              </span>
              <Button
                size="sm"
                variant="secondary"
                disabled={safePage >= totalPages}
                onClick={() =>
                  setPage((current) => Math.min(totalPages, current + 1))
                }
              >
                Next
              </Button>
            </div>
          </div>
        </div>

        <div className="hidden items-center gap-3 border-b border-line px-4 py-2.5 md:flex">
          <div className="grid min-w-0 flex-1 grid-cols-[1.6fr_0.7fr_0.55fr] gap-3">
            <span className="th-label">Name</span>
            <span className="th-label">Kind</span>
            <span className="th-label">Status</span>
          </div>
          <span className="th-label w-20 shrink-0 text-right">Actions</span>
        </div>
        <ul>
          {pageItems.map((tool) => (
            <li key={tool.id} className="border-b border-line/60 last:border-0">
              <div className="flex items-center gap-3 px-4 py-2.5 transition hover:bg-mist/70">
                <Link
                  href={`/admin/tools/${tool.id}`}
                  className="grid min-w-0 flex-1 items-center gap-3 md:grid-cols-[1.6fr_0.7fr_0.55fr]"
                >
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium">{tool.name}</p>
                    <p className="mt-0.5 line-clamp-2 text-xs text-slate-muted">
                      {tool.description?.trim()
                        ? truncate(tool.description)
                        : `/${tool.slug}`}
                    </p>
                  </div>
                  <div>
                    <Badge tone="info">{kindLabel(tool)}</Badge>
                  </div>
                  <div className="flex items-center gap-2">
                    <Badge dot tone={tool.active ? "success" : "neutral"}>
                      {tool.active ? "active" : "inactive"}
                    </Badge>
                    <span className="mono-cell hidden text-slate-muted lg:inline">
                      {formatRelative(tool.updatedAt)}
                    </span>
                  </div>
                </Link>
                <div className="flex w-20 shrink-0 items-center justify-end gap-1">
                  <Link
                    href={`/admin/tools/${tool.id}`}
                    className={buttonClassName({
                      variant: "secondary",
                      size: "sm",
                    })}
                  >
                    Edit
                  </Link>
                  <Button
                    size="icon"
                    variant="danger"
                    aria-label={`Delete ${tool.name}`}
                    disabled={deletingId === tool.id}
                    onClick={() => void onDelete(tool)}
                  >
                    {deletingId === tool.id ? "…" : <TrashIcon />}
                  </Button>
                </div>
              </div>
            </li>
          ))}
          {filtered.length === 0 ? (
            <li className="px-5 py-10 text-center text-sm text-slate-muted">
              {tools.length === 0
                ? "No reusable tools yet."
                : family === "legacy"
                  ? "No legacy tools."
                  : "No tools in this category."}
            </li>
          ) : null}
        </ul>
      </section>
    </div>
  );
}
