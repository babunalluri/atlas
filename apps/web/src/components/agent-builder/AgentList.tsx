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
import { deleteAgent, listAgentCatalog } from "@/lib/api/admin";
import type { AgentSummary, CatalogPage } from "@/lib/api/types";
import { useAgentOsToken } from "@/lib/auth/token";
import { formatRelative } from "@/lib/utils";

function statusTone(status: AgentSummary["status"]) {
  if (status === "published") return "success" as const;
  if (status === "archived") return "neutral" as const;
  return "warning" as const;
}

export function AgentList({
  initial,
}: {
  initial: CatalogPage<AgentSummary>;
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

  async function onDelete(agent: AgentSummary) {
    if (
      !window.confirm(
        `Delete “${agent.name}”? This cannot be undone.`,
      )
    ) {
      return;
    }
    setDeletingId(agent.id);
    setError(null);
    try {
      await deleteAgent(await getAccessToken(), agent.id);
      setPageData((prev) => ({
        ...prev,
        items: prev.items.filter((item) => item.id !== agent.id),
        total: Math.max(0, prev.total - 1),
      }));
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Failed to delete agent",
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
        <div className="flex items-center gap-3 border-b border-line px-4 py-2">
          <div className="grid min-w-0 flex-1 grid-cols-[1.4fr_0.7fr_0.7fr_0.6fr] gap-3">
            <span className="th-label">Name</span>
            <span className="th-label">Model</span>
            <span className="th-label">Status</span>
            <span className="th-label text-right">Updated</span>
          </div>
          <span className="th-label w-9 shrink-0 text-right"> </span>
        </div>
        <ul>
          {pageData.items.map((agent) => (
            <li key={agent.id} className="border-b border-line/60 last:border-0">
              <div className="flex items-center gap-3 px-4 py-2.5 transition hover:bg-mist/70">
                <Link
                  href={`/admin/agents/${agent.id}`}
                  className="grid min-w-0 flex-1 grid-cols-[1.4fr_0.7fr_0.7fr_0.6fr] items-center gap-3"
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
                <div className="flex w-9 shrink-0 items-center justify-end">
                  <Button
                    size="icon"
                    variant="danger"
                    aria-label={`Delete ${agent.name}`}
                    disabled={deletingId === agent.id}
                    onClick={() => void onDelete(agent)}
                  >
                    {deletingId === agent.id ? "…" : <TrashIcon />}
                  </Button>
                </div>
              </div>
            </li>
          ))}
          {pageData.items.length === 0 ? (
            <li className="px-4 py-10 text-center text-sm text-slate-muted">
              No agents yet — create one above.
            </li>
          ) : null}
        </ul>
      </section>
    </div>
  );
}
