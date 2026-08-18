"use client";

import { Link, useRouter } from "@/i18n/navigation";
import { useMemo, useState } from "react";

import { Badge } from "@/components/ui/Badge";
import { Button, buttonClassName } from "@/components/ui/Button";
import { Input } from "@/components/ui/Field";
import {
  CloneIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  CloseIcon,
  HistoryIcon,
  PencilIcon,
  PlusIcon,
  SearchIcon,
  TrashIcon,
} from "@/components/ui/icons";
import {
  VersionHistoryPanel,
  type VersionHistoryItem,
} from "@/components/ui/VersionHistoryPanel";
import {
  cloneToolDefinition,
  deleteToolDefinition,
  getToolVersion,
  listToolVersions,
  restoreToolVersion,
} from "@/lib/api/admin";
import type { ToolDefinition } from "@/lib/api/types";
import { useAgentOsToken } from "@/lib/auth/token";
import { cn, formatRelative } from "@/lib/utils";

type ToolFamily = "api" | "python" | "mcp" | "toolkits";
type StatusFilter = "all" | "active" | "inactive";

type ViewingSource = {
  version: number;
  status: string;
  sourceCode: string;
};

const PAGE_SIZE = 25;

const FAMILY_KINDS: Record<ToolFamily, ReadonlyArray<ToolDefinition["kind"]>> = {
  api: ["http", "openapi"],
  python: ["tenant_python"],
  mcp: ["mcp"],
  toolkits: ["python_toolkit", "custom_python"],
};

const FAMILY_CREATE_KIND: Record<
  Exclude<ToolFamily, "toolkits">,
  ToolDefinition["kind"]
> = {
  api: "http",
  python: "tenant_python",
  mcp: "mcp",
};

const PRIMARY_TABS: Array<{ id: ToolFamily; label: string }> = [
  { id: "api", label: "API Tools" },
  { id: "python", label: "Python Tools" },
  { id: "mcp", label: "MCP Tools" },
  { id: "toolkits", label: "Toolkits" },
];

function kindLabel(tool: ToolDefinition): string {
  switch (tool.kind) {
    case "http":
      return tool.httpMethod ?? "HTTP";
    case "openapi":
      return "OpenAPI";
    case "tenant_python":
      return "Python Tools";
    case "mcp":
      return "MCP Tools";
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
  const router = useRouter();
  const { getAccessToken } = useAgentOsToken();
  const [tools, setTools] = useState(initialTools);
  const [error, setError] = useState<string | null>(null);
  const [deletingIds, setDeletingIds] = useState<Set<string>>(() => new Set());
  const [cloningIds, setCloningIds] = useState<Set<string>>(() => new Set());
  const [family, setFamily] = useState<ToolFamily>("api");
  const [q, setQ] = useState("");
  const [status, setStatus] = useState<StatusFilter>("all");
  const [page, setPage] = useState(1);

  const [versionsTool, setVersionsTool] = useState<ToolDefinition | null>(null);
  const [versions, setVersions] = useState<VersionHistoryItem[]>([]);
  const [versionBusy, setVersionBusy] = useState(false);
  const [versionError, setVersionError] = useState<string | null>(null);
  const [viewingSource, setViewingSource] = useState<ViewingSource | null>(
    null,
  );

  const tabs = PRIMARY_TABS;

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
    family === "toolkits"
      ? "/admin/integrations"
      : `/admin/tools/new?family=${family}&kind=${FAMILY_CREATE_KIND[family]}`;

  const createLabel = family === "toolkits" ? "Browse catalog" : "Create";

  async function onClone(tool: ToolDefinition) {
    setCloningIds((current) => new Set(current).add(tool.id));
    setError(null);
    try {
      const cloned = await cloneToolDefinition(await getAccessToken(), tool.id);
      router.push(`/admin/tools/${cloned.id}`);
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Failed to clone tool",
      );
      setCloningIds((current) => {
        const next = new Set(current);
        next.delete(tool.id);
        return next;
      });
    }
  }

  async function onDelete(tool: ToolDefinition) {
    if (!window.confirm(`Delete “${tool.name}”? This cannot be undone.`)) {
      return;
    }
    setDeletingIds((current) => new Set(current).add(tool.id));
    setError(null);
    try {
      await deleteToolDefinition(await getAccessToken(), tool.id);
      setTools((prev) => prev.filter((item) => item.id !== tool.id));
      if (versionsTool?.id === tool.id) {
        closeVersions();
      }
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Failed to delete tool",
      );
    } finally {
      setDeletingIds((current) => {
        const next = new Set(current);
        next.delete(tool.id);
        return next;
      });
    }
  }

  function closeVersions() {
    setVersionsTool(null);
    setVersions([]);
    setVersionError(null);
    setViewingSource(null);
    setVersionBusy(false);
  }

  async function refreshVersions(tool: ToolDefinition) {
    setVersionBusy(true);
    setVersionError(null);
    try {
      const rows = await listToolVersions(await getAccessToken(), tool.id);
      const liveId = tool.publishedVersionId ?? null;
      setVersions(
        rows.map((row) => ({
          id: row.id,
          version: row.version,
          status: row.status,
          isLive: liveId !== null && row.id === liveId,
          createdAt: row.createdAt,
        })),
      );
    } catch (reason) {
      setVersionError(
        reason instanceof Error ? reason.message : "Failed to load versions",
      );
    } finally {
      setVersionBusy(false);
    }
  }

  async function openVersions(tool: ToolDefinition) {
    setVersionsTool(tool);
    setVersions([]);
    setViewingSource(null);
    setVersionError(null);
    await refreshVersions(tool);
  }

  async function viewVersion(version: VersionHistoryItem) {
    if (!versionsTool) return;
    setVersionBusy(true);
    setVersionError(null);
    try {
      const detail = await getToolVersion(
        await getAccessToken(),
        versionsTool.id,
        version.id,
      );
      setViewingSource({
        version: detail.version,
        status: detail.status,
        sourceCode: detail.sourceCode,
      });
    } catch (reason) {
      setVersionError(
        reason instanceof Error ? reason.message : "Failed to load version",
      );
    } finally {
      setVersionBusy(false);
    }
  }

  async function restoreLive(version: VersionHistoryItem) {
    if (!versionsTool || version.isLive) return;
    if (
      !window.confirm(
        `Make v${version.version} the live published version? Current live stays available in history.`,
      )
    ) {
      return;
    }
    setVersionBusy(true);
    setVersionError(null);
    try {
      const restored = await restoreToolVersion(
        await getAccessToken(),
        versionsTool.id,
        version.id,
      );
      setTools((prev) =>
        prev.map((item) => (item.id === restored.id ? restored : item)),
      );
      setVersionsTool(restored);
      setViewingSource(null);
      await refreshVersions(restored);
    } catch (reason) {
      setVersionError(
        reason instanceof Error ? reason.message : "Restore failed",
      );
      setVersionBusy(false);
    }
  }

  async function restoreDraft(version: VersionHistoryItem) {
    if (!versionsTool) return;
    if (
      !window.confirm(
        `Clone v${version.version} into a draft for editing? Live published version will not change until you publish.`,
      )
    ) {
      return;
    }
    setVersionBusy(true);
    setVersionError(null);
    try {
      const restored = await restoreToolVersion(
        await getAccessToken(),
        versionsTool.id,
        version.id,
        { asDraft: true },
      );
      setTools((prev) =>
        prev.map((item) => (item.id === restored.id ? restored : item)),
      );
      closeVersions();
      router.push(`/admin/tools/${restored.id}`);
    } catch (reason) {
      setVersionError(
        reason instanceof Error ? reason.message : "Restore failed",
      );
      setVersionBusy(false);
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
            API, Python, MCP, and built-in toolkits agents can call. Enable
            catalog toolkits, then attach them like any other tool.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {family !== "toolkits" ? (
            <Link
              href="/admin/integrations"
              className={buttonClassName({ variant: "secondary" })}
            >
              <SearchIcon />
              Toolkit catalog
            </Link>
          ) : null}
          <Link
            href={createHref}
            className={buttonClassName({ variant: "accent" })}
          >
            {family === "toolkits" ? <SearchIcon /> : <PlusIcon />}
            {createLabel}
          </Link>
        </div>
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
                icon={<ChevronLeftIcon />}
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
                icon={<ChevronRightIcon />}
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
          <span className="th-label hidden w-auto shrink-0 text-right md:block">
            Actions
          </span>
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
                <div className="flex shrink-0 items-center justify-end gap-0.5">
                  <Link
                    href={`/admin/tools/${tool.id}`}
                    className={buttonClassName({
                      variant: "ghost",
                      size: "icon",
                    })}
                    aria-label={`Edit ${tool.name}`}
                    title="Edit"
                  >
                    <PencilIcon />
                  </Link>
                  <Button
                    size="icon"
                    variant="ghost"
                    aria-label={`Versions for ${tool.name}`}
                    title="Versions"
                    onClick={() => void openVersions(tool)}
                  >
                    <HistoryIcon />
                  </Button>
                  <Button
                    size="icon"
                    variant="ghost"
                    aria-label={`Clone ${tool.name}`}
                    title="Clone"
                    disabled={cloningIds.has(tool.id)}
                    onClick={() => void onClone(tool)}
                  >
                    {cloningIds.has(tool.id) ? "…" : <CloneIcon />}
                  </Button>
                  <Button
                    size="icon"
                    variant="danger"
                    aria-label={`Delete ${tool.name}`}
                    title="Delete"
                    disabled={deletingIds.has(tool.id)}
                    onClick={() => void onDelete(tool)}
                  >
                    {deletingIds.has(tool.id) ? "…" : <TrashIcon />}
                  </Button>
                </div>
              </div>
            </li>
          ))}
          {filtered.length === 0 ? (
            <li className="px-5 py-10 text-center text-sm text-slate-muted">
              {tools.length === 0
                ? "No reusable tools yet."
                : family === "toolkits"
                  ? "No toolkit tools yet — enable one from the catalog."
                  : "No tools in this category."}
            </li>
          ) : null}
        </ul>
      </section>

      {versionsTool ? (
        <div
          className="fixed inset-0 z-40 flex items-end justify-center p-4 sm:items-center"
          role="dialog"
          aria-modal="true"
          aria-labelledby="tool-versions-title"
        >
          <button
            type="button"
            className="absolute inset-0 bg-ink/40"
            aria-label="Close version history"
            onClick={closeVersions}
          />
          <div className="relative z-10 flex max-h-[85vh] w-full max-w-2xl flex-col overflow-hidden rounded-xl border border-line bg-canvas shadow-lg">
            <div className="flex items-start justify-between gap-3 border-b border-line px-4 py-3">
              <div className="min-w-0">
                <h2
                  id="tool-versions-title"
                  className="truncate text-sm font-semibold"
                >
                  Versions · {versionsTool.name}
                </h2>
                <p className="mt-0.5 truncate text-xs text-slate-muted">
                  /{versionsTool.slug}
                </p>
              </div>
              <Button size="sm" variant="ghost" icon={<CloseIcon />} onClick={closeVersions}>
                Close
              </Button>
            </div>
            <div className="space-y-3 overflow-y-auto p-4">
              {versionError ? (
                <p className="text-sm text-rose">{versionError}</p>
              ) : null}
              <VersionHistoryPanel
                versions={versions}
                busy={versionBusy}
                onRefresh={() => {
                  if (versionsTool) void refreshVersions(versionsTool);
                }}
                onView={(version) => void viewVersion(version)}
                onRestoreLive={(version) => void restoreLive(version)}
                onRestoreDraft={(version) => void restoreDraft(version)}
                onCloseView={() => setViewingSource(null)}
                viewing={
                  viewingSource ? (
                    <div className="space-y-2 text-sm">
                      <div className="flex flex-wrap gap-2">
                        <Badge
                          tone={
                            viewingSource.status === "published"
                              ? "success"
                              : viewingSource.status === "validated"
                                ? "info"
                                : "warning"
                          }
                        >
                          v{viewingSource.version} · {viewingSource.status}
                        </Badge>
                      </div>
                      <pre className="max-h-80 overflow-auto whitespace-pre-wrap rounded-md border border-line bg-canvas/80 p-3 font-mono text-[12px] leading-relaxed">
                        {viewingSource.sourceCode || "(empty)"}
                      </pre>
                    </div>
                  ) : null
                }
              />
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
