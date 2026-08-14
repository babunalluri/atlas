"use client";

import { Link, useRouter } from "@/i18n/navigation";
import { useEffect, useRef, useState } from "react";

import { Badge } from "@/components/ui/Badge";
import { Button, buttonClassName } from "@/components/ui/Button";
import {
  CatalogControls,
  DEFAULT_CATALOG_QUERY,
  type CatalogQuery,
} from "@/components/ui/CatalogControls";
import {
  CloneIcon,
  HistoryIcon,
  PencilIcon,
  TrashIcon,
} from "@/components/ui/icons";
import {
  VersionHistoryPanel,
  type VersionHistoryItem,
} from "@/components/ui/VersionHistoryPanel";
import {
  cloneAgent,
  deleteAgent,
  getAgentVersion,
  listAgentCatalog,
  listAgentVersions,
  restoreAgentVersion,
} from "@/lib/api/admin";
import type { AgentConfig, AgentSummary, CatalogPage } from "@/lib/api/types";
import { useAgentOsToken } from "@/lib/auth/token";
import { formatRelative } from "@/lib/utils";

function statusTone(status: AgentSummary["status"]) {
  if (status === "published") return "success" as const;
  if (status === "archived") return "neutral" as const;
  return "warning" as const;
}

function toSummary(agent: AgentConfig): AgentSummary {
  return {
    id: agent.id,
    name: agent.name,
    slug: agent.slug,
    status: agent.status,
    model: agent.model,
    domain: agent.domain,
    updatedAt: agent.updatedAt,
    publishedVersion: agent.publishedVersion,
  };
}

export function AgentList({
  initial,
}: {
  initial: CatalogPage<AgentSummary>;
}) {
  const router = useRouter();
  const { getAccessToken } = useAgentOsToken();
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState<CatalogQuery>(DEFAULT_CATALOG_QUERY);
  const [pageData, setPageData] = useState(initial);
  const [loading, setLoading] = useState(false);
  const [deletingIds, setDeletingIds] = useState<Set<string>>(() => new Set());
  const [cloningIds, setCloningIds] = useState<Set<string>>(() => new Set());
  const skipInitialFetch = useRef(true);

  const [versionsAgent, setVersionsAgent] = useState<AgentSummary | null>(null);
  const [versions, setVersions] = useState<VersionHistoryItem[]>([]);
  const [versionBusy, setVersionBusy] = useState(false);
  const [versionError, setVersionError] = useState<string | null>(null);
  const [viewing, setViewing] = useState<{
    version: number;
    instructions: string;
    modelId: string;
    temperature: number;
    memoryMode: string;
  } | null>(null);

  useEffect(() => {
    if (skipInitialFetch.current) {
      skipInitialFetch.current = false;
      return;
    }
    let cancelled = false;
    const handle = window.setTimeout(() => {
      setLoading(true);
      void (async () => {
        try {
          const next = await listAgentCatalog(await getAccessToken(), {
            q: query.q,
            status: query.status,
            page: query.page,
            pageSize: query.pageSize,
          });
          if (cancelled) return;
          setPageData(next);
          setError(null);
        } catch (reason) {
          if (!cancelled) {
            setError(
              reason instanceof Error ? reason.message : "Failed to load agents",
            );
          }
        } finally {
          if (!cancelled) setLoading(false);
        }
      })();
    }, 250);
    return () => {
      cancelled = true;
      window.clearTimeout(handle);
    };
  }, [getAccessToken, query]);

  function closeVersions() {
    setVersionsAgent(null);
    setVersions([]);
    setVersionError(null);
    setViewing(null);
    setVersionBusy(false);
  }

  async function refreshVersions(agent: AgentSummary) {
    setVersionBusy(true);
    setVersionError(null);
    try {
      const rows = await listAgentVersions(await getAccessToken(), agent.id);
      setVersions(
        rows.map((row) => ({
          id: row.id,
          version: row.version,
          status: row.status,
          isLive: row.isLive,
          createdAt: row.createdAt,
          details: [row.modelId],
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

  async function openVersions(agent: AgentSummary) {
    setVersionsAgent(agent);
    setVersions([]);
    setViewing(null);
    setVersionError(null);
    await refreshVersions(agent);
  }

  async function viewVersion(version: VersionHistoryItem) {
    if (!versionsAgent) return;
    setVersionBusy(true);
    setVersionError(null);
    try {
      const detail = await getAgentVersion(
        await getAccessToken(),
        versionsAgent.id,
        version.id,
      );
      setViewing({
        version: detail.version,
        instructions: detail.instructions,
        modelId: detail.modelId,
        temperature: detail.temperature,
        memoryMode: detail.memoryMode,
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
    if (!versionsAgent || version.isLive) return;
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
      const restored = await restoreAgentVersion(
        await getAccessToken(),
        versionsAgent.id,
        version.id,
      );
      const summary = toSummary(restored);
      setPageData((prev) => ({
        ...prev,
        items: prev.items.map((item) =>
          item.id === summary.id ? summary : item,
        ),
      }));
      setVersionsAgent(summary);
      setViewing(null);
      await refreshVersions(summary);
    } catch (reason) {
      setVersionError(
        reason instanceof Error ? reason.message : "Restore failed",
      );
      setVersionBusy(false);
    }
  }

  async function restoreDraft(version: VersionHistoryItem) {
    if (!versionsAgent) return;
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
      const restored = await restoreAgentVersion(
        await getAccessToken(),
        versionsAgent.id,
        version.id,
        { asDraft: true },
      );
      const summary = toSummary(restored);
      setPageData((prev) => ({
        ...prev,
        items: prev.items.map((item) =>
          item.id === summary.id ? summary : item,
        ),
      }));
      closeVersions();
      router.push(`/admin/agents/${restored.id}`);
    } catch (reason) {
      setVersionError(
        reason instanceof Error ? reason.message : "Restore failed",
      );
      setVersionBusy(false);
    }
  }

  async function onClone(agent: AgentSummary) {
    setCloningIds((current) => new Set(current).add(agent.id));
    setError(null);
    try {
      const cloned = await cloneAgent(await getAccessToken(), agent.id);
      router.push(`/admin/agents/${cloned.id}`);
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Failed to clone agent",
      );
      setCloningIds((current) => {
        const next = new Set(current);
        next.delete(agent.id);
        return next;
      });
    }
  }

  async function onDelete(agent: AgentSummary) {
    if (
      !window.confirm(
        `Delete “${agent.name}”? This cannot be undone.`,
      )
    ) {
      return;
    }
    setDeletingIds((current) => new Set(current).add(agent.id));
    setError(null);
    try {
      await deleteAgent(await getAccessToken(), agent.id);
      setPageData((prev) => ({
        ...prev,
        items: prev.items.filter((item) => item.id !== agent.id),
        total: Math.max(0, prev.total - 1),
      }));
      if (versionsAgent?.id === agent.id) {
        closeVersions();
      }
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Failed to delete agent",
      );
    } finally {
      setDeletingIds((current) => {
        const next = new Set(current);
        next.delete(agent.id);
        return next;
      });
    }
  }

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-display text-2xl font-semibold tracking-tight">
            Agents
          </h1>
          <p className="mt-0.5 text-sm text-slate-muted">
            Build one specialist, add tools, then publish.
          </p>
        </div>
        <Link
          href="/admin/agents/new"
          className={buttonClassName({ variant: "accent" })}
        >
          Create
        </Link>
      </header>
      {error ? <p className="text-sm text-rose">{error}</p> : null}

      <section className="table-shell rounded-xl">
        <CatalogControls
          query={query}
          total={pageData.total}
          noun="agents"
          loading={loading}
          onChange={setQuery}
        />
        <div className="hidden items-center gap-3 border-b border-line px-4 py-2 md:flex">
          <div className="grid min-w-0 flex-1 grid-cols-[1.4fr_0.7fr_0.7fr_0.6fr] gap-3">
            <span className="th-label">Name</span>
            <span className="th-label">Model</span>
            <span className="th-label">Status</span>
            <span className="th-label text-right">Updated</span>
          </div>
          <span className="th-label w-auto shrink-0 text-right">Actions</span>
        </div>
        <ul>
          {pageData.items.map((agent) => (
            <li key={agent.id} className="border-b border-line/60 last:border-0">
              <div className="flex items-center gap-3 px-4 py-2.5 transition hover:bg-mist/70">
                <Link
                  href={`/admin/agents/${agent.id}`}
                  className="grid min-w-0 flex-1 items-center gap-3 md:grid-cols-[1.4fr_0.7fr_0.7fr_0.6fr]"
                >
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium">{agent.name}</p>
                    <p className="mono-cell truncate text-slate-muted">
                      /{agent.slug}
                    </p>
                  </div>
                  <p className="mono-cell text-ink-soft">{agent.model}</p>
                  <div className="flex items-center gap-2">
                    <Badge dot tone={statusTone(agent.status)}>
                      {agent.status}
                    </Badge>
                    {agent.publishedVersion ? (
                      <span className="mono-cell text-slate-muted">
                        v{agent.publishedVersion}
                      </span>
                    ) : null}
                  </div>
                  <p className="mono-cell text-right text-slate-muted">
                    {formatRelative(agent.updatedAt)}
                  </p>
                </Link>
                <div className="flex shrink-0 items-center justify-end gap-0.5">
                  <Link
                    href={`/admin/agents/${agent.id}`}
                    className={buttonClassName({
                      variant: "ghost",
                      size: "icon",
                    })}
                    aria-label={`Edit ${agent.name}`}
                    title="Edit"
                  >
                    <PencilIcon />
                  </Link>
                  <Button
                    size="icon"
                    variant="ghost"
                    aria-label={`Versions for ${agent.name}`}
                    title="Versions"
                    onClick={() => void openVersions(agent)}
                  >
                    <HistoryIcon />
                  </Button>
                  <Button
                    size="icon"
                    variant="ghost"
                    aria-label={`Clone ${agent.name}`}
                    title="Clone"
                    disabled={cloningIds.has(agent.id)}
                    onClick={() => void onClone(agent)}
                  >
                    {cloningIds.has(agent.id) ? "…" : <CloneIcon />}
                  </Button>
                  <Button
                    size="icon"
                    variant="danger"
                    aria-label={`Delete ${agent.name}`}
                    title="Delete"
                    disabled={deletingIds.has(agent.id)}
                    onClick={() => void onDelete(agent)}
                  >
                    {deletingIds.has(agent.id) ? "…" : <TrashIcon />}
                  </Button>
                </div>
              </div>
            </li>
          ))}
          {pageData.items.length === 0 ? (
            <li className="px-4 py-10 text-center text-sm text-slate-muted">
              {query.q || query.status !== "all"
                ? "No agents match this search."
                : "No agents yet — create one above."}
            </li>
          ) : null}
        </ul>
      </section>

      {versionsAgent ? (
        <div
          className="fixed inset-0 z-40 flex items-end justify-center p-4 sm:items-center"
          role="dialog"
          aria-modal="true"
          aria-labelledby="agent-versions-title"
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
                  id="agent-versions-title"
                  className="truncate text-sm font-semibold"
                >
                  Versions · {versionsAgent.name}
                </h2>
                <p className="mt-0.5 truncate text-xs text-slate-muted">
                  /{versionsAgent.slug}
                </p>
              </div>
              <Button size="sm" variant="ghost" onClick={closeVersions}>
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
                  if (versionsAgent) void refreshVersions(versionsAgent);
                }}
                onView={(version) => void viewVersion(version)}
                onRestoreLive={(version) => void restoreLive(version)}
                onRestoreDraft={(version) => void restoreDraft(version)}
                onCloseView={() => setViewing(null)}
                viewing={
                  viewing ? (
                    <dl className="grid gap-2 text-sm sm:grid-cols-2">
                      <div>
                        <dt className="text-xs text-slate-muted">Model</dt>
                        <dd>{viewing.modelId}</dd>
                      </div>
                      <div>
                        <dt className="text-xs text-slate-muted">Temperature</dt>
                        <dd>{viewing.temperature}</dd>
                      </div>
                      <div>
                        <dt className="text-xs text-slate-muted">Memory</dt>
                        <dd>{viewing.memoryMode}</dd>
                      </div>
                      <div className="sm:col-span-2">
                        <dt className="text-xs text-slate-muted">Instructions</dt>
                        <dd className="mt-0.5 whitespace-pre-wrap font-mono text-[13px]">
                          {viewing.instructions}
                        </dd>
                      </div>
                    </dl>
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
