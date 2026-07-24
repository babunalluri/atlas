"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { Badge } from "@/components/ui/Badge";
import { Button, buttonClassName } from "@/components/ui/Button";
import {
  CatalogControls,
  DEFAULT_CATALOG_QUERY,
  type CatalogQuery,
} from "@/components/ui/CatalogControls";
import { TrashIcon } from "@/components/ui/icons";
import { deleteWorkflow, listWorkflowCatalog } from "@/lib/api/admin";
import type { CatalogPage, WorkflowSummary } from "@/lib/api/types";
import { useAgentOsToken } from "@/lib/auth/token";
import { formatRelative } from "@/lib/utils";

export function WorkflowList({
  initial,
}: {
  initial: CatalogPage<WorkflowSummary>;
}) {
  const { getAccessToken } = useAgentOsToken();
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState<CatalogQuery>(DEFAULT_CATALOG_QUERY);
  const [pageData, setPageData] = useState(initial);
  const [loading, setLoading] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const skipInitialFetch = useRef(true);

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

  async function onDelete(workflow: WorkflowSummary) {
    if (
      !window.confirm(
        `Delete “${workflow.name}”? This cannot be undone.`,
      )
    ) {
      return;
    }
    setDeletingId(workflow.id);
    setError(null);
    try {
      await deleteWorkflow(await getAccessToken(), workflow.id);
      setPageData((prev) => ({
        ...prev,
        items: prev.items.filter((item) => item.id !== workflow.id),
        total: Math.max(0, prev.total - 1),
      }));
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Failed to delete workflow",
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
        <div className="flex items-center gap-3 border-b border-line px-4 py-2">
          <div className="grid min-w-0 flex-1 grid-cols-[1.5fr_0.7fr_0.7fr_0.6fr] gap-3">
            <span className="th-label">Name</span>
            <span className="th-label">Mode</span>
            <span className="th-label">Status</span>
            <span className="th-label text-right">Updated</span>
          </div>
          <span className="th-label w-9 shrink-0 text-right"> </span>
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
                  className="grid min-w-0 flex-1 grid-cols-[1.5fr_0.7fr_0.7fr_0.6fr] items-center gap-3"
                >
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium">
                      {workflow.name}
                    </p>
                    <p className="mono-cell truncate text-slate-muted">
                      /{workflow.slug} · {workflow.stepCount} steps
                      {workflow.publishedVersion
                        ? ` · v${workflow.publishedVersion}`
                        : ""}
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
                <div className="flex w-9 shrink-0 items-center justify-end">
                  <Button
                    size="icon"
                    variant="danger"
                    aria-label={`Delete ${workflow.name}`}
                    disabled={deletingId === workflow.id}
                    onClick={() => void onDelete(workflow)}
                  >
                    {deletingId === workflow.id ? "…" : <TrashIcon />}
                  </Button>
                </div>
              </div>
            </li>
          ))}
          {pageData.items.length === 0 ? (
            <li className="px-4 py-10 text-center text-sm text-slate-muted">
              No workflows yet — create one above.
            </li>
          ) : null}
        </ul>
      </section>
    </div>
  );
}
