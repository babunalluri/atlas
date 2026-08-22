"use client";

import { useMemo, useState } from "react";

import { Button } from "@/components/ui/Button";
import {
  BuildingIcon,
  ChartLineIcon,
  ExternalLinkIcon,
  HistoryIcon,
  LayersIcon,
  RefreshIcon,
} from "@/components/ui/icons";
import type { OptionsScreenerRow, OptionsScreenerSnapshot } from "@/lib/api/admin";
import { cn } from "@/lib/utils";

const EMPTY_SCREENER_ROWS: OptionsScreenerRow[] = [];

type SortKey =
  | "label"
  | "pcr"
  | "atm_iv"
  | "oi_pct_chg"
  | "iv_chg"
  | "straddle"
  | "max_pain";

function formatNum(value: number | null | undefined, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  if (Number.isInteger(value)) return value.toLocaleString();
  return value.toFixed(digits);
}

function formatPct(value: number | null | undefined) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(2)}%`;
}

function formatOi(value: number | null | undefined) {
  if (value === null || value === undefined) return "—";
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(2)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}K`;
  return String(Math.round(value));
}

function parseOptionalNumber(raw: string): number | null {
  const trimmed = raw.trim();
  if (!trimmed) return null;
  const value = Number(trimmed);
  return Number.isFinite(value) ? value : null;
}

function sortRows(rows: OptionsScreenerRow[], key: SortKey, desc: boolean) {
  const factor = desc ? -1 : 1;
  return [...rows].sort((a, b) => {
    if (key === "label") {
      return factor * a.underlying_label.localeCompare(b.underlying_label);
    }
    const av = a[key];
    const bv = b[key];
    if (av == null && bv == null) return 0;
    if (av == null) return 1;
    if (bv == null) return -1;
    return factor * (Number(av) - Number(bv));
  });
}

export function OptionsLabScreenerPanel({
  snapshot,
  loading,
  universe,
  onUniverseChange,
  onRefresh,
  onResetBaseline,
  resetting,
  onSelectUnderlying,
}: {
  snapshot: OptionsScreenerSnapshot | null;
  loading?: boolean;
  universe: "indices" | "equities" | "all";
  onUniverseChange: (universe: "indices" | "equities" | "all") => void;
  onRefresh: () => void;
  onResetBaseline: () => void;
  resetting?: boolean;
  onSelectUnderlying: (row: OptionsScreenerRow) => void;
}) {
  const [query, setQuery] = useState("");
  const [pcrMin, setPcrMin] = useState("");
  const [pcrMax, setPcrMax] = useState("");
  const [ivMax, setIvMax] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("label");
  const [sortDesc, setSortDesc] = useState(false);

  const rows = snapshot?.rows ?? EMPTY_SCREENER_ROWS;
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    const min = parseOptionalNumber(pcrMin);
    const max = parseOptionalNumber(pcrMax);
    const ivCap = parseOptionalNumber(ivMax);
    return rows.filter((row) => {
      if (row.error) return true;
      if (q && !row.underlying_label.toLowerCase().includes(q)) return false;
      if (min != null && (row.pcr == null || row.pcr < min)) return false;
      if (max != null && (row.pcr == null || row.pcr > max)) return false;
      if (ivCap != null && (row.atm_iv == null || row.atm_iv > ivCap)) return false;
      return true;
    });
  }, [ivMax, pcrMax, pcrMin, query, rows]);

  const sorted = useMemo(
    () => sortRows(filtered, sortKey, sortDesc),
    [filtered, sortDesc, sortKey],
  );

  const fetchedLabel =
    snapshot?.fetched_at != null
      ? new Date(snapshot.fetched_at * 1000).toLocaleTimeString()
      : null;

  function toggleSort(key: SortKey) {
    if (sortKey === key) {
      setSortDesc((value) => !value);
      return;
    }
    setSortKey(key);
    setSortDesc(key !== "label");
  }

  if (!snapshot?.ok && !loading) {
    return (
      <p className="mt-6 py-10 text-center text-sm text-slate-muted">
        {snapshot?.error ?? "Screener unavailable — bind Kite on Signals ops or enable mock in Options Lab setup."}
      </p>
    );
  }

  return (
    <div className="mt-3 flex min-h-0 flex-1 flex-col gap-3">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-sm text-slate-muted">
            {universe === "equities"
              ? "Equity F&O screener"
              : universe === "all"
                ? "Index + equity F&O screener"
                : "Index F&O screener"}{" "}
            · ATM ±5 chain scan · refreshed {fetchedLabel ?? "…"}
          </p>
          <div className="mt-2 flex flex-wrap gap-1">
            {(
              [
                ["indices", "Indices", <ChartLineIcon key="i" />],
                ["equities", "Equities", <BuildingIcon key="e" />],
                ["all", "All", <LayersIcon key="a" />],
              ] as const
            ).map(([id, label, icon]) => (
              <Button
                key={id}
                type="button"
                size="sm"
                variant={universe === id ? "primary" : "secondary"}
                icon={icon}
                onClick={() => onUniverseChange(id)}
              >
                {label}
              </Button>
            ))}
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            variant="secondary"
            size="sm"
            icon={<HistoryIcon />}
            disabled={resetting}
            onClick={onResetBaseline}
          >
            Reset session baselines
          </Button>
          <Button variant="secondary" size="sm" icon={<RefreshIcon />} onClick={onRefresh}>
            Refresh
          </Button>
        </div>
      </div>

      {(snapshot?.warnings?.length ?? 0) > 0 ? (
        <ul className="list-inside list-disc rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-950">
          {snapshot?.warnings?.map((msg) => (
            <li key={msg}>{msg}</li>
          ))}
        </ul>
      ) : null}

      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
        <label className="text-xs text-slate-muted">
          Search
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="NIFTY, BANKNIFTY…"
            className="mt-1 w-full rounded-md border border-line bg-canvas px-2 py-1.5 text-sm text-ink"
          />
        </label>
        <label className="text-xs text-slate-muted">
          PCR min
          <input
            value={pcrMin}
            onChange={(e) => setPcrMin(e.target.value)}
            type="number"
            step="0.05"
            className="mt-1 w-full rounded-md border border-line bg-canvas px-2 py-1.5 text-sm text-ink"
          />
        </label>
        <label className="text-xs text-slate-muted">
          PCR max
          <input
            value={pcrMax}
            onChange={(e) => setPcrMax(e.target.value)}
            type="number"
            step="0.05"
            className="mt-1 w-full rounded-md border border-line bg-canvas px-2 py-1.5 text-sm text-ink"
          />
        </label>
        <label className="text-xs text-slate-muted">
          ATM IV max
          <input
            value={ivMax}
            onChange={(e) => setIvMax(e.target.value)}
            type="number"
            step="0.5"
            className="mt-1 w-full rounded-md border border-line bg-canvas px-2 py-1.5 text-sm text-ink"
          />
        </label>
      </div>

      <div className="min-h-0 flex-1 overflow-auto rounded-lg border border-line">
        <table className="min-w-full border-collapse text-left text-xs">
          <thead className="sticky top-0 z-10 bg-raised/95 backdrop-blur">
            <tr className="border-b border-line text-[10px] uppercase tracking-wide text-slate-muted">
              {(
                [
                  ["label", "Underlying"],
                  ["pcr", "PCR"],
                  ["max_pain", "Max pain"],
                  ["atm_iv", "ATM IV"],
                  ["iv_chg", "IV chg"],
                  ["oi_pct_chg", "OI chg"],
                  ["straddle", "Straddle"],
                ] as const
              ).map(([key, label]) => (
                <th key={key} className="px-3 py-2">
                  <button
                    type="button"
                    onClick={() => toggleSort(key)}
                    className="font-semibold hover:text-ink"
                  >
                    {label}
                    {sortKey === key ? (sortDesc ? " ↓" : " ↑") : ""}
                  </button>
                </th>
              ))}
              <th className="px-3 py-2">IVP</th>
              <th className="px-3 py-2">CE OI</th>
              <th className="px-3 py-2">PE OI</th>
              <th className="px-3 py-2">Action</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((row) => (
              <tr
                key={row.underlying_symbol}
                className={cn(
                  "border-b border-line/70",
                  row.error && "bg-rose/5",
                )}
              >
                <td className="px-3 py-2">
                  <div className="font-semibold text-ink">{row.underlying_label}</div>
                  <div className="text-[10px] text-slate-muted tabular-nums">
                    {formatNum(row.spot)} · ATM {row.atm ?? "—"}
                  </div>
                  {row.error ? (
                    <div className="mt-0.5 text-[10px] text-rose">{row.error}</div>
                  ) : null}
                </td>
                <td className="px-3 py-2 tabular-nums">{formatNum(row.pcr, 3)}</td>
                <td className="px-3 py-2 tabular-nums">
                  {row.max_pain != null ? String(row.max_pain) : "—"}
                </td>
                <td className="px-3 py-2 tabular-nums">{formatNum(row.atm_iv, 1)}</td>
                <td className="px-3 py-2 tabular-nums">{formatPct(row.iv_chg)}</td>
                <td className="px-3 py-2 tabular-nums">{formatPct(row.oi_pct_chg)}</td>
                <td className="px-3 py-2 tabular-nums">{formatNum(row.straddle)}</td>
                <td className="px-3 py-2 tabular-nums">
                  {row.ivp != null ? `${Math.round(row.ivp)}` : "—"}
                </td>
                <td className="px-3 py-2 tabular-nums">{formatOi(row.chain_ce_oi)}</td>
                <td className="px-3 py-2 tabular-nums">{formatOi(row.chain_pe_oi)}</td>
                <td className="px-3 py-2">
                  <Button
                    variant="secondary"
                    size="sm"
                    icon={<ExternalLinkIcon />}
                    disabled={Boolean(row.error)}
                    onClick={() => onSelectUnderlying(row)}
                  >
                    Open chain
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
