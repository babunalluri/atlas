"use client";

import { useMemo, useState } from "react";

import { Button } from "@/components/ui/Button";
import { RefreshIcon } from "@/components/ui/icons";
import type { OptionsScreenerRow, OptionsScreenerSnapshot } from "@/lib/api/admin";
import { cn } from "@/lib/utils";

type HeatMetric = "pcr" | "oi_pct_chg" | "iv_chg" | "atm_iv";

function cellColor(metric: HeatMetric, value: number | null): string {
  if (value == null || Number.isNaN(value)) return "bg-fog/40 text-slate-muted";
  if (metric === "pcr") {
    if (value >= 1.2) return "bg-rose/25 text-rose";
    if (value <= 0.8) return "bg-teal/25 text-teal";
    return "bg-raised text-ink";
  }
  if (metric === "atm_iv") {
    if (value >= 25) return "bg-rose/20 text-rose";
    if (value <= 12) return "bg-teal/20 text-teal";
    return "bg-raised text-ink";
  }
  // oi_pct_chg / iv_chg — signed
  if (value > 2) return "bg-rose/25 text-rose";
  if (value < -2) return "bg-teal/25 text-teal";
  if (value > 0) return "bg-rose/10 text-ink";
  if (value < 0) return "bg-teal/10 text-ink";
  return "bg-raised text-ink";
}

function formatCell(metric: HeatMetric, value: number | null) {
  if (value == null || Number.isNaN(value)) return "—";
  if (metric === "pcr" || metric === "atm_iv") return value.toFixed(2);
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(1)}%`;
}

export function OptionsLabHeatmapPanel({
  snapshot,
  loading,
  onRefresh,
  onSelectUnderlying,
}: {
  snapshot: OptionsScreenerSnapshot | null;
  loading?: boolean;
  onRefresh: () => void;
  onSelectUnderlying: (row: OptionsScreenerRow) => void;
}) {
  const [metric, setMetric] = useState<HeatMetric>("pcr");
  const rows = useMemo(() => {
    const list = [...(snapshot?.rows ?? [])].filter((r) => !r.error);
    list.sort((a, b) => a.underlying_label.localeCompare(b.underlying_label));
    return list;
  }, [snapshot?.rows]);

  return (
    <div className="flex flex-col gap-3 pt-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-slate-muted">
          Equity / index F&amp;O heat — click a cell to open the chain.
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <div className="inline-flex rounded-md border border-line bg-canvas/60 p-0.5 text-xs">
            {(
              [
                ["pcr", "PCR"],
                ["oi_pct_chg", "OI Δ"],
                ["iv_chg", "IV Δ"],
                ["atm_iv", "ATM IV"],
              ] as const
            ).map(([id, label]) => (
              <button
                key={id}
                type="button"
                onClick={() => setMetric(id)}
                className={cn(
                  "rounded px-2 py-1 font-medium",
                  metric === id ? "bg-raised text-ink shadow-sm" : "text-slate-muted",
                )}
              >
                {label}
              </button>
            ))}
          </div>
          <Button
            type="button"
            size="sm"
            variant="secondary"
            icon={<RefreshIcon />}
            disabled={loading}
            onClick={onRefresh}
          >
            Refresh
          </Button>
        </div>
      </div>

      {rows.length === 0 ? (
        <p className="py-10 text-center text-sm text-slate-muted">
          {loading ? "Loading screener…" : "No screener rows — refresh or switch Mock."}
        </p>
      ) : (
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 md:grid-cols-4">
          {rows.map((row) => {
            const value = row[metric];
            return (
              <button
                key={row.underlying_symbol}
                type="button"
                onClick={() => onSelectUnderlying(row)}
                className={cn(
                  "rounded-lg border border-line px-2 py-2 text-left transition hover:ring-1 hover:ring-teal/40",
                  cellColor(metric, value),
                )}
              >
                <p className="truncate text-xs font-semibold">{row.underlying_label}</p>
                <p className="mt-1 text-sm tabular-nums font-semibold">
                  {formatCell(metric, value)}
                </p>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
