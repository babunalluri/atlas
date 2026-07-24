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
import { deleteTeam, listTeamCatalog } from "@/lib/api/admin";
import type { CatalogPage, TeamSummary } from "@/lib/api/types";
import { useAgentOsToken } from "@/lib/auth/token";
import { formatRelative } from "@/lib/utils";

export function TeamList({
  initial,
}: {
  initial: CatalogPage<TeamSummary>;
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
          const next = await listTeamCatalog(await getAccessToken(), {
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
              reason instanceof Error ? reason.message : "Failed to load teams",
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

  async function onDelete(team: TeamSummary) {
    if (
      !window.confirm(`Delete “${team.name}”? This cannot be undone.`)
    ) {
      return;
    }
    setDeletingId(team.id);
    setError(null);
    try {
      await deleteTeam(await getAccessToken(), team.id);
      setPageData((prev) => ({
        ...prev,
        items: prev.items.filter((item) => item.id !== team.id),
        total: Math.max(0, prev.total - 1),
      }));
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Failed to delete team",
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
            Teams
          </h1>
          <p className="mt-0.5 text-sm text-slate-muted">
            Group published agents so they can work together.
          </p>
        </div>
        <Link
          href="/admin/teams/new"
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
          noun="teams"
          loading={loading}
          onChange={setQuery}
        />
        <div className="flex items-center gap-3 border-b border-line px-4 py-2">
          <div className="grid min-w-0 flex-1 grid-cols-[1.4fr_0.7fr_0.7fr_0.6fr] gap-3">
            <span className="th-label">Name</span>
            <span className="th-label">Mode</span>
            <span className="th-label">Status</span>
            <span className="th-label text-right">Updated</span>
          </div>
          <span className="th-label w-9 shrink-0 text-right"> </span>
        </div>
        <ul>
          {pageData.items.map((team) => (
            <li key={team.id} className="border-b border-line/60 last:border-0">
              <div className="flex items-center gap-3 px-4 py-2.5 transition hover:bg-mist/70">
                <Link
                  href={`/admin/teams/${team.id}`}
                  className="grid min-w-0 flex-1 grid-cols-[1.4fr_0.7fr_0.7fr_0.6fr] items-center gap-3"
                >
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium">{team.name}</p>
                    <p className="mono-cell truncate text-slate-muted">
                      /{team.slug} · {team.memberCount} agents
                    </p>
                  </div>
                  <p className="text-sm capitalize text-ink-soft">{team.mode}</p>
                  <div className="flex items-center gap-2">
                    <Badge
                      dot
                      tone={team.status === "published" ? "success" : "warning"}
                    >
                      {team.status}
                    </Badge>
                    {team.publishedVersion ? (
                      <span className="mono-cell text-slate-muted">
                        v{team.publishedVersion}
                      </span>
                    ) : null}
                  </div>
                  <p className="mono-cell text-right text-slate-muted">
                    {formatRelative(team.updatedAt)}
                  </p>
                </Link>
                <div className="flex w-9 shrink-0 items-center justify-end">
                  <Button
                    size="icon"
                    variant="danger"
                    aria-label={`Delete ${team.name}`}
                    disabled={deletingId === team.id}
                    onClick={() => void onDelete(team)}
                  >
                    {deletingId === team.id ? "…" : <TrashIcon />}
                  </Button>
                </div>
              </div>
            </li>
          ))}
          {pageData.items.length === 0 ? (
            <li className="px-4 py-10 text-center text-sm text-slate-muted">
              No teams yet — create one above, then add published agents.
            </li>
          ) : null}
        </ul>
      </section>
    </div>
  );
}
