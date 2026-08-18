"use client";

import { Link } from "@/i18n/navigation";
import { useMemo, useState } from "react";

import { Badge } from "@/components/ui/Badge";
import { Button, buttonClassName } from "@/components/ui/Button";
import { Input } from "@/components/ui/Field";
import { ChevronLeftIcon, ChevronRightIcon, PencilIcon, PlusIcon, TrashIcon } from "@/components/ui/icons";
import { deleteKnowledgeBase } from "@/lib/api/admin";
import type { KnowledgeSource } from "@/lib/api/types";
import { useAgentOsToken } from "@/lib/auth/token";
import { cn, formatRelative } from "@/lib/utils";

type StatusFilter = "all" | "ready" | "processing" | "failed";

type KnowledgeBaseRow = {
  id: string;
  name: string;
  sourceCount: number;
  readyCount: number;
  latestUpdatedAt: string | null;
};

const PAGE_SIZE = 25;

export function KnowledgeList({
  bases: initialBases,
  sources: initialSources,
}: {
  bases: Array<{ id: string; name: string }>;
  sources: KnowledgeSource[];
}) {
  const { getAccessToken } = useAgentOsToken();
  const [bases, setBases] = useState(initialBases);
  const [sources, setSources] = useState(initialSources);
  const [q, setQ] = useState("");
  const [status, setStatus] = useState<StatusFilter>("all");
  const [page, setPage] = useState(1);
  const [deletingIds, setDeletingIds] = useState<Set<string>>(() => new Set());
  const [error, setError] = useState<string | null>(null);

  const rows = useMemo<KnowledgeBaseRow[]>(() => {
    return bases.map((base) => {
      const baseSources = sources.filter(
        (source) => source.knowledgeBaseId === base.id,
      );
      const latest = baseSources.reduce<string | null>((current, source) => {
        if (!current) return source.updatedAt;
        return source.updatedAt > current ? source.updatedAt : current;
      }, null);
      return {
        id: base.id,
        name: base.name,
        sourceCount: baseSources.length,
        readyCount: baseSources.filter((source) => source.status === "ready")
          .length,
        latestUpdatedAt: latest,
      };
    });
  }, [bases, sources]);

  const filtered = useMemo(() => {
    const query = q.trim().toLowerCase();
    return rows.filter((row) => {
      if (status === "ready" && row.readyCount === 0) return false;
      if (
        status === "processing" &&
        !sources.some(
          (source) =>
            source.knowledgeBaseId === row.id &&
            (source.status === "processing" || source.status === "uploading"),
        )
      ) {
        return false;
      }
      if (
        status === "failed" &&
        !sources.some(
          (source) =>
            source.knowledgeBaseId === row.id && source.status === "failed",
        )
      ) {
        return false;
      }
      if (!query) return true;
      return row.name.toLowerCase().includes(query) || row.id.toLowerCase().includes(query);
    });
  }, [q, rows, sources, status]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const safePage = Math.min(page, totalPages);
  const start = (safePage - 1) * PAGE_SIZE;
  const pageItems = filtered.slice(start, start + PAGE_SIZE);
  const end = Math.min(filtered.length, start + PAGE_SIZE);

  async function onDelete(row: KnowledgeBaseRow) {
    if (
      !window.confirm(
        `Delete “${row.name}”? This removes the knowledge base and its sources. This cannot be undone.`,
      )
    ) {
      return;
    }
    setDeletingIds((current) => new Set(current).add(row.id));
    setError(null);
    try {
      await deleteKnowledgeBase(await getAccessToken(), row.id);
      setBases((prev) => prev.filter((item) => item.id !== row.id));
      setSources((prev) =>
        prev.filter((source) => source.knowledgeBaseId !== row.id),
      );
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Failed to delete knowledge base",
      );
    } finally {
      setDeletingIds((current) => {
        const next = new Set(current);
        next.delete(row.id);
        return next;
      });
    }
  }

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-display text-2xl font-semibold tracking-tight">
            Knowledge
          </h1>
          <p className="mt-0.5 text-sm text-slate-muted">
            Document bases agents can search. Upload, then attach on an agent.
          </p>
        </div>
        <Link
          href="/admin/knowledge/new"
          className={buttonClassName({ variant: "accent" })}
        >
          <PlusIcon />
          Create
        </Link>
      </header>
      {error ? <p className="text-sm text-rose">{error}</p> : null}

      <section className="table-shell rounded-xl">
        <div className="space-y-3 border-b border-line px-4 py-3">
          <div className="flex flex-wrap items-center gap-2">
            <Input
              value={q}
              placeholder="Search knowledge bases…"
              className="min-w-[220px] flex-1"
              onChange={(event) => {
                setQ(event.target.value);
                setPage(1);
              }}
            />
            {(
              [
                ["all", "All"],
                ["ready", "Has ready"],
                ["processing", "Processing"],
                ["failed", "Failed"],
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
                ? "No knowledge bases match"
                : `Showing ${start + 1}–${end} of ${filtered.length} bases`}
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

        <div className="hidden items-center gap-3 border-b border-line px-4 py-2 md:flex">
          <div className="grid min-w-0 flex-1 grid-cols-[1.5fr_0.7fr_0.7fr_0.6fr] gap-3">
            <span className="th-label">Name</span>
            <span className="th-label">Sources</span>
            <span className="th-label">Ready</span>
            <span className="th-label text-right">Updated</span>
          </div>
          <span className="th-label w-auto shrink-0 text-right">Actions</span>
        </div>
        <ul>
          {pageItems.map((row) => (
            <li key={row.id} className="border-b border-line/60 last:border-0">
              <div className="flex items-center gap-3 px-4 py-2.5 transition hover:bg-mist/70">
                <Link
                  href={`/admin/knowledge/${row.id}`}
                  className="grid min-w-0 flex-1 items-center gap-3 md:grid-cols-[1.5fr_0.7fr_0.7fr_0.6fr]"
                >
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium">{row.name}</p>
                    <p className="mono-cell truncate text-slate-muted">{row.id}</p>
                  </div>
                  <p className="text-sm text-slate-muted">{row.sourceCount}</p>
                  <div>
                    <Badge
                      tone={
                        row.readyCount > 0
                          ? "success"
                          : row.sourceCount > 0
                            ? "warning"
                            : "neutral"
                      }
                    >
                      {row.readyCount}/{row.sourceCount}
                    </Badge>
                  </div>
                  <p className="mono-cell text-right text-slate-muted">
                    {row.latestUpdatedAt
                      ? formatRelative(row.latestUpdatedAt)
                      : "—"}
                  </p>
                </Link>
                <div className="flex shrink-0 items-center justify-end gap-0.5">
                  <Link
                    href={`/admin/knowledge/${row.id}`}
                    className={buttonClassName({
                      variant: "ghost",
                      size: "icon",
                    })}
                    aria-label={`Edit ${row.name}`}
                    title="Edit"
                  >
                    <PencilIcon />
                  </Link>
                  <Button
                    size="icon"
                    variant="danger"
                    aria-label={`Delete ${row.name}`}
                    title="Delete"
                    disabled={deletingIds.has(row.id)}
                    onClick={() => void onDelete(row)}
                  >
                    {deletingIds.has(row.id) ? "…" : <TrashIcon />}
                  </Button>
                </div>
              </div>
            </li>
          ))}
          {filtered.length === 0 ? (
            <li className="px-4 py-10 text-center text-sm text-slate-muted">
              {bases.length === 0
                ? "No knowledge bases yet — create one above."
                : "No knowledge bases match this search."}
            </li>
          ) : null}
        </ul>
      </section>
    </div>
  );
}
