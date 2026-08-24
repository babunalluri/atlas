"use client";

import { Button } from "@/components/ui/Button";
import { RefreshIcon } from "@/components/ui/icons";
import type { OptionsLabFlowsSnapshot } from "@/lib/api/admin";
import { cn } from "@/lib/utils";

function formatCr(value: number | null | undefined) {
  if (value == null || Number.isNaN(value)) return "—";
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(0)} Cr`;
}

function tone(value: number | null | undefined) {
  if (value == null) return "text-slate-muted";
  if (value > 0) return "text-teal";
  if (value < 0) return "text-rose";
  return "text-ink";
}

export function OptionsLabFlowsPanel({
  snapshot,
  loading,
  onRefresh,
}: {
  snapshot: OptionsLabFlowsSnapshot | null;
  loading?: boolean;
  onRefresh: () => void;
}) {
  const fii = snapshot?.fii_net ?? null;
  const dii = snapshot?.dii_net ?? null;
  const adr = snapshot?.advance_decline_ratio ?? null;
  const series = snapshot?.series ?? [];

  return (
    <div className="flex flex-col gap-4 pt-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <p className="min-w-0 flex-1 text-sm text-slate-muted">
          NSE FII / DII net (₹ crores). Session keeps a multi-day series when you refresh
          (Mock seeds a short history). Independent of Signal Engine Start.
        </p>
        <Button
          type="button"
          size="sm"
          variant="secondary"
          className="ml-auto shrink-0"
          icon={<RefreshIcon />}
          disabled={loading}
          onClick={onRefresh}
        >
          Refresh
        </Button>
      </div>

      {snapshot && !snapshot.ok ? (
        <p className="rounded-md border border-rose/30 bg-rose/10 px-2 py-1.5 text-sm text-rose">
          {snapshot.error ?? "Flows unavailable"}
        </p>
      ) : null}

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <div className="rounded-lg border border-line bg-raised/40 px-3 py-3">
          <p className="th-label">FII net</p>
          <p className={cn("mt-1 text-xl font-semibold tabular-nums", tone(fii))}>
            {formatCr(fii)}
          </p>
        </div>
        <div className="rounded-lg border border-line bg-raised/40 px-3 py-3">
          <p className="th-label">DII net</p>
          <p className={cn("mt-1 text-xl font-semibold tabular-nums", tone(dii))}>
            {formatCr(dii)}
          </p>
        </div>
        <div className="rounded-lg border border-line bg-raised/40 px-3 py-3">
          <p className="th-label">Advance / decline</p>
          <p className="mt-1 text-xl font-semibold tabular-nums text-ink">
            {adr != null ? adr.toFixed(2) : "—"}
          </p>
        </div>
      </div>

      {series.length > 0 ? (
        <div className="overflow-auto rounded-lg border border-line">
          <table className="min-w-full border-collapse text-left text-sm">
            <thead className="bg-raised/80">
              <tr>
                <th className="th-label px-3 py-2">Period</th>
                <th className="th-label px-3 py-2 text-right">FII</th>
                <th className="th-label px-3 py-2 text-right">DII</th>
              </tr>
            </thead>
            <tbody>
              {series.map((row) => (
                <tr key={row.label} className="border-t border-line/70">
                  <td className="px-3 py-2">{row.label}</td>
                  <td className={cn("px-3 py-2 text-right tabular-nums", tone(row.fii_net))}>
                    {formatCr(row.fii_net)}
                  </td>
                  <td className={cn("px-3 py-2 text-right tabular-nums", tone(row.dii_net))}>
                    {formatCr(row.dii_net)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="text-sm text-slate-muted">
          {loading ? "Loading NSE flows…" : "No series yet — refresh or enable Mock."}
        </p>
      )}

      {snapshot?.warnings?.length ? (
        <ul className="space-y-1 text-xs text-amber-800 dark:text-amber-200">
          {snapshot.warnings.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
