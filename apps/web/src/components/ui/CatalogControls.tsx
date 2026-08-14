"use client";

import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Field";
import { cn } from "@/lib/utils";

export type CatalogStatusFilter = "all" | "published" | "draft";

export type CatalogQuery = {
  q: string;
  status: CatalogStatusFilter;
  page: number;
  pageSize: number;
};

export const DEFAULT_CATALOG_QUERY: CatalogQuery = {
  q: "",
  status: "all",
  page: 1,
  pageSize: 25,
};

const STATUS_OPTIONS: Array<{ value: CatalogStatusFilter; label: string }> = [
  { value: "all", label: "All" },
  { value: "published", label: "Published" },
  { value: "draft", label: "Draft" },
];

export function CatalogControls({
  query,
  total,
  noun,
  onChange,
  loading = false,
}: {
  query: CatalogQuery;
  total: number;
  noun: string;
  onChange: (next: CatalogQuery) => void;
  loading?: boolean;
}) {
  const totalPages = Math.max(1, Math.ceil(total / query.pageSize));
  const start = total === 0 ? 0 : (query.page - 1) * query.pageSize + 1;
  const end = Math.min(total, query.page * query.pageSize);

  return (
    <div className="space-y-3 border-b border-line px-4 py-3">
      <div className="flex flex-wrap items-center gap-2">
        <Input
          value={query.q}
          placeholder={`Search ${noun}…`}
          className="min-w-[220px] flex-1"
          onChange={(event) =>
            onChange({ ...query, q: event.target.value, page: 1 })
          }
        />
        <div className="flex flex-wrap gap-1">
          {STATUS_OPTIONS.map((option) => (
            <button
              key={option.value}
              type="button"
              onClick={() =>
                onChange({ ...query, status: option.value, page: 1 })
              }
              className={cn(
                "rounded-md border px-2.5 py-1.5 text-xs font-medium transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ink/20 focus-visible:ring-offset-1 focus-visible:ring-offset-canvas",
                query.status === option.value
                  ? "border-line-strong bg-mist text-ink"
                  : "border-transparent bg-raised text-slate-muted hover:bg-mist",
              )}
            >
              {option.label}
            </button>
          ))}
        </div>
      </div>
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-slate-muted">
        <p>
          {loading
            ? `Loading ${noun}…`
            : total === 0
              ? `No ${noun} match`
              : `Showing ${start}–${end} of ${total} ${noun}`}
        </p>
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            variant="secondary"
            disabled={loading || query.page <= 1}
            onClick={() => onChange({ ...query, page: query.page - 1 })}
          >
            Previous
          </Button>
          <span className="mono-cell">
            {query.page} / {totalPages}
          </span>
          <Button
            size="sm"
            variant="secondary"
            disabled={loading || query.page >= totalPages}
            onClick={() => onChange({ ...query, page: query.page + 1 })}
          >
            Next
          </Button>
        </div>
      </div>
    </div>
  );
}
