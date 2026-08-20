"use client";

import { useMemo, useState } from "react";

import type { OptionsOiChartRow } from "@/lib/api/admin";
import { cn } from "@/lib/utils";

const CHART_HEIGHT = 420;
const BAR_H = 14;

function barWidth(value: number | null | undefined, max: number) {
  if (value == null || max <= 0) return 0;
  return Math.max(0, (Math.abs(value) / max) * 100);
}

function oiBarClass(
  mode: "level" | "change",
  side: "ce" | "pe",
  value: number | null | undefined,
) {
  if (mode === "level") {
    return side === "ce" ? "bg-teal/80" : "bg-rose/80";
  }
  if (value == null || value === 0) {
    return side === "ce" ? "bg-teal/30" : "bg-rose/30";
  }
  if (value > 0) {
    return side === "ce" ? "bg-teal/80" : "bg-rose/80";
  }
  return side === "ce"
    ? "border border-teal/70 bg-teal/20"
    : "border border-rose/70 bg-rose/20";
}

export function OptionsLabOiChart({
  rows,
  spot,
}: {
  rows: OptionsOiChartRow[];
  spot: number | null;
}) {
  const [mode, setMode] = useState<"level" | "change">("level");

  const maxVal = useMemo(() => {
    let max = 1;
    for (const row of rows) {
      const ce = mode === "level" ? row.ce_oi : row.ce_oi_chg;
      const pe = mode === "level" ? row.pe_oi : row.pe_oi_chg;
      max = Math.max(max, Math.abs(ce ?? 0), Math.abs(pe ?? 0));
    }
    return max;
  }, [mode, rows]);

  const rowHeight = rows.length > 0 ? CHART_HEIGHT / rows.length : BAR_H + 4;

  if (rows.length === 0) {
    return (
      <p className="py-12 text-center text-sm text-slate-muted">
        No OI data — load the chain first.
      </p>
    );
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-slate-muted">
          {mode === "level" ? "Open interest by strike" : "OI change vs session baseline"}
          {spot != null ? ` · spot ${spot.toLocaleString()}` : ""}
        </p>
        <div className="inline-flex rounded-md border border-line bg-canvas/60 p-0.5 text-xs">
          {(
            [
              ["level", "OI"],
              ["change", "Δ OI"],
            ] as const
          ).map(([id, label]) => (
            <button
              key={id}
              type="button"
              onClick={() => setMode(id)}
              className={cn(
                "rounded px-2 py-1 font-medium transition",
                mode === id ? "bg-raised text-ink shadow-sm" : "text-slate-muted",
              )}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {mode === "change" ? (
        <div className="mb-2 flex flex-wrap gap-3 text-[10px] text-slate-muted">
          <span className="inline-flex items-center gap-1">
            <span className="inline-block h-2 w-3 rounded-sm bg-teal/80" /> CE writing
          </span>
          <span className="inline-flex items-center gap-1">
            <span className="inline-block h-2 w-3 rounded-sm border border-teal/70 bg-teal/20" /> CE
            unwind
          </span>
          <span className="inline-flex items-center gap-1">
            <span className="inline-block h-2 w-3 rounded-sm bg-rose/80" /> PE writing
          </span>
          <span className="inline-flex items-center gap-1">
            <span className="inline-block h-2 w-3 rounded-sm border border-rose/70 bg-rose/20" /> PE
            unwind
          </span>
        </div>
      ) : null}

      <div
        className="relative overflow-auto rounded-lg border border-line bg-canvas/40"
        style={{ minHeight: CHART_HEIGHT }}
      >
        <div className="sticky top-0 z-10 grid grid-cols-[1fr_auto_1fr] border-b border-line bg-raised/95 px-2 py-1 text-[10px] font-semibold uppercase tracking-wide text-slate-muted backdrop-blur">
          <span className="text-right text-teal">Call OI</span>
          <span className="w-14 text-center">Strike</span>
          <span className="text-rose">Put OI</span>
        </div>
        {rows.map((row) => {
          const ceVal = mode === "level" ? row.ce_oi : row.ce_oi_chg;
          const peVal = mode === "level" ? row.pe_oi : row.pe_oi_chg;
          const cePct = barWidth(ceVal, maxVal);
          const pePct = barWidth(peVal, maxVal);
          return (
            <div
              key={row.strike}
              className={cn(
                "grid grid-cols-[1fr_auto_1fr] items-center gap-1 border-b border-line/50 px-2",
                row.is_atm && "bg-teal/10",
              )}
              style={{ minHeight: rowHeight }}
            >
              <div className="flex justify-end pr-1">
                <div
                  className={cn("h-2.5 rounded-l", oiBarClass(mode, "ce", ceVal))}
                  style={{ width: `${cePct}%`, minWidth: ceVal ? 2 : 0 }}
                  title={ceVal != null ? String(ceVal) : undefined}
                />
                <span className="ml-1 min-w-[2.5rem] text-right text-[10px] tabular-nums text-slate-muted">
                  {formatOiCompact(ceVal)}
                </span>
              </div>
              <div className="w-14 text-center text-[11px] font-semibold tabular-nums text-ink">
                {row.strike}
              </div>
              <div className="flex pl-1">
                <span className="mr-1 min-w-[2.5rem] text-[10px] tabular-nums text-slate-muted">
                  {formatOiCompact(peVal)}
                </span>
                <div
                  className={cn("h-2.5 rounded-r", oiBarClass(mode, "pe", peVal))}
                  style={{ width: `${pePct}%`, minWidth: peVal ? 2 : 0 }}
                  title={peVal != null ? String(peVal) : undefined}
                />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function formatOiCompact(value: number | null | undefined) {
  if (value == null) return "—";
  const abs = Math.abs(value);
  const sign = value < 0 ? "−" : value > 0 ? "+" : "";
  if (abs >= 1_000_000) return `${sign}${(abs / 1_000_000).toFixed(1)}M`;
  if (abs >= 1_000) return `${sign}${(abs / 1_000).toFixed(0)}K`;
  return `${sign}${Math.round(abs)}`;
}
