"use client";

import { useMemo, useState, type ReactNode } from "react";

import { Button } from "@/components/ui/Button";
import {
  ArrowDownIcon,
  ArrowUpIcon,
  ChartLineIcon,
  ExternalLinkIcon,
  HistoryIcon,
  LayersIcon,
  RefreshIcon,
} from "@/components/ui/icons";
import type { OptionsScreenerRow, OptionsScreenerSnapshot } from "@/lib/api/admin";
import { cn } from "@/lib/utils";

type HeatMetric = "pcr" | "oi_pct_chg" | "iv_chg" | "atm_iv";

const METRICS: {
  id: HeatMetric;
  label: string;
  icon: ReactNode;
  legend: string;
}[] = [
  {
    id: "pcr",
    label: "PCR",
    icon: <LayersIcon />,
    legend: "Teal ≤0.8 put-light · rose ≥1.2 put-heavy",
  },
  {
    id: "oi_pct_chg",
    label: "OI Δ",
    icon: <ArrowUpIcon />,
    legend: "Teal OI down · rose OI up (session %)",
  },
  {
    id: "iv_chg",
    label: "IV Δ",
    icon: <ChartLineIcon />,
    legend: "Teal IV down · rose IV up (session %)",
  },
  {
    id: "atm_iv",
    label: "ATM IV",
    icon: <HistoryIcon />,
    legend: "Teal ≤12% · rose ≥25% ATM IV",
  },
];

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

function cellToneLabel(metric: HeatMetric, value: number | null): string | null {
  if (value == null || Number.isNaN(value)) return null;
  if (metric === "pcr") {
    if (value >= 1.2) return "put-heavy";
    if (value <= 0.8) return "put-light";
    return "neutral";
  }
  if (metric === "atm_iv") {
    if (value >= 25) return "rich IV";
    if (value <= 12) return "cheap IV";
    return "mid IV";
  }
  if (value > 2) return "up strong";
  if (value < -2) return "down strong";
  if (value > 0) return "up";
  if (value < 0) return "down";
  return "flat";
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
  const active = METRICS.find((m) => m.id === metric) ?? METRICS[0];
  const rows = useMemo(() => {
    const list = [...(snapshot?.rows ?? [])].filter((r) => !r.error);
    list.sort((a, b) => a.underlying_label.localeCompare(b.underlying_label));
    return list;
  }, [snapshot?.rows]);

  const fetchedLabel =
    snapshot?.fetched_at != null
      ? new Date(snapshot.fetched_at * 1000).toLocaleTimeString()
      : null;

  return (
    <div className="mt-3 flex min-h-0 flex-1 flex-col gap-3">
      <div className="flex flex-col gap-2">
        <p className="text-sm text-slate-muted">
          Equity / index F&amp;O heat — click a cell to open the chain · refreshed{" "}
          {fetchedLabel ?? "…"}
        </p>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex flex-wrap gap-1">
            {METRICS.map((m) => (
              <Button
                key={m.id}
                type="button"
                size="sm"
                variant={metric === m.id ? "primary" : "secondary"}
                icon={m.icon}
                onClick={() => setMetric(m.id)}
              >
                {m.label}
              </Button>
            ))}
          </div>
          <Button
            type="button"
            size="sm"
            variant="secondary"
            className="shrink-0"
            icon={<RefreshIcon />}
            disabled={loading}
            onClick={onRefresh}
          >
            Refresh
          </Button>
        </div>
        <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-slate-muted">
          <span className="inline-flex items-center gap-1">
            <span className="inline-block size-2.5 rounded-sm bg-teal/40" aria-hidden />
            cooler
          </span>
          <span className="inline-flex items-center gap-1">
            <span className="inline-block size-2.5 rounded-sm bg-fog" aria-hidden />
            mid
          </span>
          <span className="inline-flex items-center gap-1">
            <span className="inline-block size-2.5 rounded-sm bg-rose/40" aria-hidden />
            hotter
          </span>
          <span>· {active.legend}</span>
        </p>
      </div>

      {rows.length === 0 ? (
        <p className="py-10 text-center text-sm text-slate-muted">
          {loading ? "Loading screener…" : "No screener rows — refresh or switch Mock."}
        </p>
      ) : (
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 md:grid-cols-4">
          {rows.map((row) => {
            const value = row[metric];
            const tone = cellToneLabel(metric, value);
            const signed = metric === "oi_pct_chg" || metric === "iv_chg";
            return (
              <button
                key={row.underlying_symbol}
                type="button"
                onClick={() => onSelectUnderlying(row)}
                title={`Open ${row.underlying_label} chain`}
                className={cn(
                  "group rounded-lg border border-line px-2.5 py-2 text-left transition",
                  "hover:border-teal/50 hover:ring-1 hover:ring-teal/35",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal/40",
                  cellColor(metric, value),
                )}
              >
                <div className="flex items-start justify-between gap-1">
                  <p className="truncate text-xs font-semibold">{row.underlying_label}</p>
                  <ExternalLinkIcon className="mt-0.5 opacity-0 transition group-hover:opacity-70" />
                </div>
                <div className="mt-1 flex items-center gap-0.5">
                  {signed && value != null && value > 0 ? <ArrowUpIcon /> : null}
                  {signed && value != null && value < 0 ? <ArrowDownIcon /> : null}
                  <p className="text-sm font-semibold tabular-nums">
                    {formatCell(metric, value)}
                  </p>
                </div>
                {tone ? (
                  <p className="mt-0.5 text-[10px] uppercase tracking-wide opacity-70">{tone}</p>
                ) : null}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
