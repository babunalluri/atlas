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
  cloneWorkflow,
  deleteWorkflow,
  getWorkflowVersion,
  listWorkflowCatalog,
  listWorkflowVersions,
  restoreWorkflowVersion,
} from "@/lib/api/admin";
import type {
  CatalogPage,
  WorkflowConfig,
  WorkflowMode,
  WorkflowStep,
  WorkflowSummary,
} from "@/lib/api/types";
import { useAgentOsToken } from "@/lib/auth/token";
import { formatRelative } from "@/lib/utils";

function toSummary(workflow: WorkflowConfig): WorkflowSummary {
  return {
    id: workflow.id,
    name: workflow.name,
    slug: workflow.slug,
    mode: workflow.mode,
    status: workflow.status,
    stepCount: workflow.steps.length,
    publishedVersion: workflow.publishedVersion,
    domain: workflow.domain,
    updatedAt: workflow.updatedAt,
  };
}

export function WorkflowList({
  initial,
}: {
  initial: CatalogPage<WorkflowSummary>;
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

  const [versionsWorkflow, setVersionsWorkflow] =
    useState<WorkflowSummary | null>(null);
  const [versions, setVersions] = useState<VersionHistoryItem[]>([]);
  const [versionBusy, setVersionBusy] = useState(false);
  const [versionError, setVersionError] = useState<string | null>(null);
  const [viewing, setViewing] = useState<{
    version: number;
    mode: WorkflowMode;
    steps: WorkflowStep[];
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
          const next = await listWorkflowCatalog(await getAccessToken(), {
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
              reason instanceof Error
                ? reason.message
                : "Failed to load workflows",
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
    setVersionsWorkflow(null);
    setVersions([]);
    setVersionError(null);
    setViewing(null);
    setVersionBusy(false);
  }

  async function refreshVersions(workflow: WorkflowSummary) {
    setVersionBusy(true);
    setVersionError(null);
    try {
      const rows = await listWorkflowVersions(
        await getAccessToken(),
        workflow.id,
      );
      setVersions(
        rows.map((row) => ({
          id: row.id,
          version: row.version,
          status: row.status,
          isLive: row.isLive,
          createdAt: row.createdAt,
          details: [row.mode, `${row.stepCount} steps`],
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

  async function openVersions(workflow: WorkflowSummary) {
    setVersionsWorkflow(workflow);
    setVersions([]);
    setViewing(null);
    setVersionError(null);
    await refreshVersions(workflow);
  }

  async function viewVersion(version: VersionHistoryItem) {
    if (!versionsWorkflow) return;
    setVersionBusy(true);
    setVersionError(null);
    try {
      const detail = await getWorkflowVersion(
        await getAccessToken(),
        versionsWorkflow.id,
        version.id,
      );
      setViewing({
        version: detail.version,
        mode: detail.mode,
        steps: detail.steps,
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
    if (!versionsWorkflow || version.isLive) return;
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
      const restored = await restoreWorkflowVersion(
        await getAccessToken(),
        versionsWorkflow.id,
        version.id,
      );
      const summary = toSummary(restored);
      setPageData((prev) => ({
        ...prev,
        items: prev.items.map((item) =>
          item.id === summary.id ? summary : item,
        ),
      }));
      setVersionsWorkflow(summary);
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
    if (!versionsWorkflow) return;
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
      const restored = await restoreWorkflowVersion(
        await getAccessToken(),
        versionsWorkflow.id,
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
      router.push(`/admin/workflows/${restored.id}`);
    } catch (reason) {
      setVersionError(
        reason instanceof Error ? reason.message : "Restore failed",
      );
      setVersionBusy(false);
    }
  }

  async function onClone(workflow: WorkflowSummary) {
    setCloningIds((current) => new Set(current).add(workflow.id));
    setError(null);
    try {
      const cloned = await cloneWorkflow(await getAccessToken(), workflow.id);
      router.push(`/admin/workflows/${cloned.id}`);
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Failed to clone workflow",
      );
      setCloningIds((current) => {
        const next = new Set(current);
        next.delete(workflow.id);
        return next;
      });
    }
  }

  async function onDelete(workflow: WorkflowSummary) {
    if (
      !window.confirm(
        `Delete “${workflow.name}”? This cannot be undone.`,
      )
    ) {
      return;
    }
    setDeletingIds((current) => new Set(current).add(workflow.id));
    setError(null);
    try {
      await deleteWorkflow(await getAccessToken(), workflow.id);
      setPageData((prev) => ({
        ...prev,
        items: prev.items.filter((item) => item.id !== workflow.id),
        total: Math.max(0, prev.total - 1),
      }));
      if (versionsWorkflow?.id === workflow.id) {
        closeVersions();
      }
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Failed to delete workflow",
      );
    } finally {
      setDeletingIds((current) => {
        const next = new Set(current);
        next.delete(workflow.id);
        return next;
      });
    }
  }

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-display text-2xl font-semibold tracking-tight">
            Workflows
          </h1>
          <p className="mt-0.5 text-sm text-slate-muted">
            Connect teams and agents into a step-by-step flow users can run.
          </p>
        </div>
        <Link
          href="/admin/workflows/new"
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
          noun="workflows"
          loading={loading}
          onChange={setQuery}
        />
        <div className="hidden items-center gap-3 border-b border-line px-4 py-2 md:flex">
          <div className="grid min-w-0 flex-1 grid-cols-[1.5fr_0.7fr_0.7fr_0.6fr] gap-3">
            <span className="th-label">Name</span>
            <span className="th-label">Mode</span>
            <span className="th-label">Status</span>
            <span className="th-label text-right">Updated</span>
          </div>
          <span className="th-label w-auto shrink-0 text-right">Actions</span>
        </div>
        <ul>
          {pageData.items.map((workflow) => (
            <li
              key={workflow.id}
              className="border-b border-line/60 last:border-0"
            >
              <div className="flex items-center gap-3 px-4 py-2.5 transition hover:bg-mist/70">
                <Link
                  href={`/admin/workflows/${workflow.id}`}
                  className="grid min-w-0 flex-1 items-center gap-3 md:grid-cols-[1.5fr_0.7fr_0.7fr_0.6fr]"
                >
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium">
                      {workflow.name}
                    </p>
                    <p className="mono-cell truncate text-slate-muted">
                      {workflow.stepCount} step
                      {workflow.stepCount === 1 ? "" : "s"}
                    </p>
                  </div>
                  <p className="text-sm capitalize text-ink-soft">
                    {workflow.mode}
                  </p>
                  <Badge
                    dot
                    tone={
                      workflow.status === "published" ? "success" : "warning"
                    }
                  >
                    {workflow.status}
                  </Badge>
                  <p className="mono-cell text-right text-slate-muted">
                    {formatRelative(workflow.updatedAt)}
                  </p>
                </Link>
                <div className="flex shrink-0 items-center justify-end gap-0.5">
                  <Link
                    href={`/admin/workflows/${workflow.id}`}
                    className={buttonClassName({
                      variant: "ghost",
                      size: "icon",
                    })}
                    aria-label={`Edit ${workflow.name}`}
                    title="Edit"
                  >
                    <PencilIcon />
                  </Link>
                  <Button
                    size="icon"
                    variant="ghost"
                    aria-label={`Versions for ${workflow.name}`}
                    title="Versions"
                    onClick={() => void openVersions(workflow)}
                  >
                    <HistoryIcon />
                  </Button>
                  <Button
                    size="icon"
                    variant="ghost"
                    aria-label={`Clone ${workflow.name}`}
                    title="Clone"
                    disabled={cloningIds.has(workflow.id)}
                    onClick={() => void onClone(workflow)}
                  >
                    {cloningIds.has(workflow.id) ? "…" : <CloneIcon />}
                  </Button>
                  <Button
                    size="icon"
                    variant="danger"
                    aria-label={`Delete ${workflow.name}`}
                    title="Delete"
                    disabled={deletingIds.has(workflow.id)}
                    onClick={() => void onDelete(workflow)}
                  >
                    {deletingIds.has(workflow.id) ? "…" : <TrashIcon />}
                  </Button>
                </div>
              </div>
            </li>
          ))}
          {pageData.items.length === 0 ? (
            <li className="px-4 py-10 text-center text-sm text-slate-muted">
              {query.q || query.status !== "all"
                ? "No workflows match this search."
                : "No workflows yet — create one above."}
            </li>
          ) : null}
        </ul>
      </section>

      {versionsWorkflow ? (
        <div
          className="fixed inset-0 z-40 flex items-end justify-center p-4 sm:items-center"
          role="dialog"
          aria-modal="true"
          aria-labelledby="workflow-versions-title"
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
                  id="workflow-versions-title"
                  className="truncate text-sm font-semibold"
                >
                  Versions · {versionsWorkflow.name}
                </h2>
                <p className="mt-0.5 truncate text-xs text-slate-muted">
                  /{versionsWorkflow.slug}
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
                  if (versionsWorkflow) void refreshVersions(versionsWorkflow);
                }}
                onView={(version) => void viewVersion(version)}
                onRestoreLive={(version) => void restoreLive(version)}
                onRestoreDraft={(version) => void restoreDraft(version)}
                onCloseView={() => setViewing(null)}
                viewing={
                  viewing ? (
                    <dl className="grid gap-2 text-sm sm:grid-cols-2">
                      <div>
                        <dt className="text-xs text-slate-muted">Mode</dt>
                        <dd>{viewing.mode}</dd>
                      </div>
                      <div className="sm:col-span-2">
                        <dt className="mb-1 text-xs text-slate-muted">Steps</dt>
                        <dd>
                          <ul className="space-y-1">
                            {viewing.steps.map((step, index) => (
                              <li
                                key={`${step.targetConfigId}-${index}`}
                                className="text-sm"
                              >
                                {index + 1}. {step.name}{" "}
                                <span className="text-xs text-slate-muted">
                                  ({step.targetType}
                                  {step.targetName
                                    ? ` · ${step.targetName}`
                                    : ""}
                                  )
                                </span>
                              </li>
                            ))}
                          </ul>
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
