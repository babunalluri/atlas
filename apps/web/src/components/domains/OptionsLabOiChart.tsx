"use client";

import { useEffect, useMemo, useState } from "react";

import type { OptionsOiChartRow } from "@/lib/api/admin";
import { cn } from "@/lib/utils";

const ROW_H = 24;
const MULTI_MAX = 6;

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
    return side === "ce" ? "bg-teal/70" : "bg-rose/70";
  }
  if (value == null || value === 0) {
    return side === "ce" ? "bg-teal/25" : "bg-rose/25";
  }
  if (value > 0) {
    return side === "ce" ? "bg-teal/70" : "bg-rose/70";
  }
  return side === "ce"
    ? "border border-teal/60 bg-teal/15"
    : "border border-rose/60 bg-rose/15";
}

function formatOiCompact(value: number | null | undefined) {
  if (value == null) return "—";
  const abs = Math.abs(value);
  const sign = value < 0 ? "−" : value > 0 ? "+" : "";
  if (abs >= 1_000_000) return `${sign}${(abs / 1_000_000).toFixed(1)}M`;
  if (abs >= 1_000) return `${sign}${(abs / 1_000).toFixed(0)}K`;
  return `${sign}${Math.round(abs)}`;
}

function defaultMultiStrikes(rows: OptionsOiChartRow[]): number[] {
  const atm = rows.find((r) => r.is_atm)?.strike;
  if (atm == null) {
    return rows.slice(0, MULTI_MAX).map((r) => r.strike);
  }
  const sorted = [...rows].sort(
    (a, b) => Math.abs(a.strike - atm) - Math.abs(b.strike - atm),
  );
  return sorted.slice(0, Math.min(5, sorted.length)).map((r) => r.strike);
}

export function OptionsLabOiChart({
  rows,
  spot,
  fill = false,
}: {
  rows: OptionsOiChartRow[];
  spot: number | null;
  /** Fill parent height; rows scroll at fixed height (desk analysis pane). */
  fill?: boolean;
}) {
  const [mode, setMode] = useState<"level" | "change" | "multi">("level");
  const [selected, setSelected] = useState<number[]>([]);

  const strikeKey = useMemo(
    () => rows.map((r) => r.strike).join(","),
    [rows],
  );

  useEffect(() => {
    setSelected(defaultMultiStrikes(rows));
    // Reset pins when the strike ladder changes (underlying / wings), not on every quote tick.
    // eslint-disable-next-line react-hooks/exhaustive-deps -- strikeKey captures ladder identity
  }, [strikeKey]);

  const displayRows = useMemo(() => {
    if (mode !== "multi") return rows;
    const set = new Set(selected);
    return rows.filter((r) => set.has(r.strike));
  }, [mode, rows, selected]);

  const maxVal = useMemo(() => {
    let max = 1;
    const source = mode === "multi" ? displayRows : rows;
    const valueMode = mode === "change" ? "change" : "level";
    for (const row of source) {
      const ce = valueMode === "level" ? row.ce_oi : row.ce_oi_chg;
      const pe = valueMode === "level" ? row.pe_oi : row.pe_oi_chg;
      max = Math.max(max, Math.abs(ce ?? 0), Math.abs(pe ?? 0));
    }
    return max;
  }, [displayRows, mode, rows]);

  if (rows.length === 0) {
    return (
      <p className="py-12 text-center text-sm text-slate-muted">
        No OI data — load the chain first.
      </p>
    );
  }

  function toggleStrike(strike: number) {
    setSelected((prev) => {
      if (prev.includes(strike)) {
        return prev.length <= 1 ? prev : prev.filter((s) => s !== strike);
      }
      if (prev.length >= MULTI_MAX) return prev;
      return [...prev, strike].sort((a, b) => a - b);
    });
  }

  const valueMode: "level" | "change" = mode === "change" ? "change" : "level";

  return (
    <div className={cn("flex flex-col", fill ? "h-full min-h-0" : "min-h-0 flex-1")}>
      <div className="mb-2 flex shrink-0 flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-slate-muted">
          {mode === "level"
            ? "Open interest by strike"
            : mode === "change"
              ? "OI change vs session baseline"
              : "Multi-strike OI (click strikes to pin)"}
          {spot != null ? ` · spot ${spot.toLocaleString()}` : ""}
        </p>
        <div className="inline-flex rounded-md border border-line bg-canvas/60 p-0.5 text-sm">
          {(
            [
              ["level", "OI"],
              ["change", "Δ OI"],
              ["multi", "Multi"],
            ] as const
          ).map(([id, label]) => (
            <button
              key={id}
              type="button"
              onClick={() => setMode(id)}
              className={cn(
                "rounded px-2.5 py-1 font-medium transition",
                mode === id ? "bg-raised text-ink shadow-sm" : "text-slate-muted",
              )}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {mode === "multi" ? (
        <div className="mb-2 flex shrink-0 flex-wrap gap-1">
          {rows.map((row) => {
            const on = selected.includes(row.strike);
            return (
              <button
                key={row.strike}
                type="button"
                onClick={() => toggleStrike(row.strike)}
                className={cn(
                  "rounded border px-1.5 py-0.5 text-xs tabular-nums",
                  on
                    ? "border-teal/50 bg-teal/15 font-semibold text-ink"
                    : "border-line text-slate-muted hover:bg-fog/50",
                  row.is_atm && "ring-1 ring-teal/40",
                )}
              >
                {row.strike}
              </button>
            );
          })}
        </div>
      ) : null}

      {mode === "change" ? (
        <div className="mb-2 flex shrink-0 flex-wrap gap-3 text-xs text-slate-muted">
          <span className="inline-flex items-center gap-1">
            <span className="inline-block h-1.5 w-3 rounded-sm bg-teal/70" /> CE writing
          </span>
          <span className="inline-flex items-center gap-1">
            <span className="inline-block h-1.5 w-3 rounded-sm border border-teal/60 bg-teal/15" />{" "}
            CE unwind
          </span>
          <span className="inline-flex items-center gap-1">
            <span className="inline-block h-1.5 w-3 rounded-sm bg-rose/70" /> PE writing
          </span>
          <span className="inline-flex items-center gap-1">
            <span className="inline-block h-1.5 w-3 rounded-sm border border-rose/60 bg-rose/15" />{" "}
            PE unwind
          </span>
        </div>
      ) : null}

      <div
        className={cn(
          "relative min-h-0 overflow-auto rounded-lg border border-line bg-canvas/40",
          fill ? "flex-1" : "max-h-[28rem]",
        )}
      >
        <div className="sticky top-0 z-10 grid grid-cols-[1fr_5.5rem_1fr] border-b border-line bg-raised/95 px-3 py-2 backdrop-blur">
          <span className="th-label text-right text-teal">Call OI</span>
          <span className="th-label text-center">Strike</span>
          <span className="th-label text-rose">Put OI</span>
        </div>
        {displayRows.map((row) => {
          const ceVal = valueMode === "level" ? row.ce_oi : row.ce_oi_chg;
          const peVal = valueMode === "level" ? row.pe_oi : row.pe_oi_chg;
          const cePct = barWidth(ceVal, maxVal);
          const pePct = barWidth(peVal, maxVal);
          return (
            <div
              key={row.strike}
              className={cn(
                "grid grid-cols-[1fr_5.5rem_1fr] items-center gap-2 border-b border-line/50 px-3",
                row.is_atm && "bg-teal/10",
                mode === "multi" && "py-0.5",
              )}
              style={{ minHeight: mode === "multi" ? ROW_H + 8 : ROW_H }}
            >
              <div className="flex min-w-0 items-center justify-end gap-2">
                <span className="w-12 shrink-0 text-right text-xs tabular-nums text-slate-muted">
                  {formatOiCompact(ceVal)}
                </span>
                <div className="flex h-1.5 min-w-0 flex-1 items-center justify-end">
                  <div
                    className={cn("h-full rounded-sm", oiBarClass(valueMode, "ce", ceVal))}
                    style={{ width: `${cePct}%`, minWidth: ceVal ? 2 : 0 }}
                    title={ceVal != null ? String(ceVal) : undefined}
                  />
                </div>
              </div>
              <div className="text-center text-sm font-semibold tabular-nums text-ink">
                {row.strike}
                {row.is_atm ? (
                  <span className="ml-1 text-xs font-medium text-teal">ATM</span>
                ) : null}
              </div>
              <div className="flex min-w-0 items-center gap-2">
                <div className="flex h-1.5 min-w-0 flex-1 items-center">
                  <div
                    className={cn("h-full rounded-sm", oiBarClass(valueMode, "pe", peVal))}
                    style={{ width: `${pePct}%`, minWidth: peVal ? 2 : 0 }}
                    title={peVal != null ? String(peVal) : undefined}
                  />
                </div>
                <span className="w-12 shrink-0 text-xs tabular-nums text-slate-muted">
                  {formatOiCompact(peVal)}
                </span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
