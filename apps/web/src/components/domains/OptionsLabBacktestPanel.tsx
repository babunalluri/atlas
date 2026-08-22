"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { Button } from "@/components/ui/Button";
import type { StrategyLeg } from "@/components/domains/options-lab-strategy";
import { buildPayoffCurve, summarizeStrategy } from "@/components/domains/options-lab-strategy";
import {
  createOptionsLabBacktest,
  createOptionsLabBot,
  deleteOptionsLabBacktest,
  listOptionsLabBacktests,
  summarizeOptionsLabBacktests,
  type OptionsIvPoint,
  type OptionsLabBacktestSummaryRow,
} from "@/lib/api/admin";
import { cn } from "@/lib/utils";

/**
 * Model backtest overlay — expiry payoff vs synthetic spot path.
 * Saves runs to tenant session store for Wave 2 bot handoff.
 */
export function OptionsLabBacktestPanel({
  legs,
  spot,
  strikeStep,
  ivPoints,
  getAccessToken,
  underlyingSymbol,
  underlyingLabel,
}: {
  legs: StrategyLeg[];
  spot: number | null;
  strikeStep: number;
  ivPoints?: OptionsIvPoint[];
  getAccessToken: () => Promise<string | null>;
  underlyingSymbol?: string;
  underlyingLabel?: string;
}) {
  const ivCalibrated = useMemo(() => {
    const ivs = (ivPoints ?? [])
      .map((p) => p.iv)
      .filter((v): v is number => typeof v === "number" && Number.isFinite(v) && v > 0);
    if (ivs.length === 0) return null;
    const latest = ivs[ivs.length - 1]!;
    const daily = latest / Math.sqrt(252);
    return {
      latest,
      shockPct: Math.min(15, Math.max(0.5, Math.round(daily * 10) / 10)),
      samples: ivs.length,
    };
  }, [ivPoints]);

  const [shockPct, setShockPct] = useState(2);
  const [days, setDays] = useState(10);
  const [pathBias, setPathBias] = useState<"up" | "down" | "flat">("flat");
  const [calibratedOnce, setCalibratedOnce] = useState(false);
  const [saveName, setSaveName] = useState("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState<OptionsLabBacktestSummaryRow[]>([]);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [summary, setSummary] = useState<Awaited<
    ReturnType<typeof summarizeOptionsLabBacktests>
  > | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    if (calibratedOnce || !ivCalibrated) return;
    setShockPct(ivCalibrated.shockPct);
    setCalibratedOnce(true);
  }, [calibratedOnce, ivCalibrated]);

  const refreshSaved = useCallback(async () => {
    try {
      const token = await getAccessToken();
      if (!token) return;
      const res = await listOptionsLabBacktests(token);
      if (!res.ok) {
        setMessage(res.error ?? "Failed to load saved backtests");
        return;
      }
      setSaved(res.backtests ?? []);
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "Failed to load saved backtests");
    }
  }, [getAccessToken]);

  useEffect(() => {
    void refreshSaved();
  }, [refreshSaved]);

  const result = useMemo(() => {
    if (!legs.length || spot == null || !Number.isFinite(spot)) return null;
    const curve = buildPayoffCurve(legs, { spot, strikeStep, wings: 12 });
    const summaryLocal = summarizeStrategy(legs, curve);
    const nearest = (target: number) => {
      if (curve.length === 0) return 0;
      let best = curve[0]!;
      for (const p of curve) {
        if (Math.abs(p.spot - target) < Math.abs(best.spot - target)) best = p;
      }
      return best.pnl;
    };

    const shocks = Array.from({ length: days }, (_, i) => {
      const sqrtT = Math.sqrt(i + 1);
      const move = (shockPct / 100) * sqrtT;
      const up = spot * (1 + move);
      const down = spot * (1 - move);
      let pathSpot: number;
      if (pathBias === "up") {
        pathSpot = spot * (1 + move * 0.85);
      } else if (pathBias === "down") {
        pathSpot = spot * (1 - move * 0.85);
      } else {
        pathSpot = spot * (1 + Math.sin(((i + 1) / days) * Math.PI * 2) * move * 0.35);
      }
      return {
        day: i + 1,
        up,
        down,
        pathSpot,
        pnlUp: nearest(up),
        pnlDown: nearest(down),
        pnlPath: nearest(pathSpot),
      };
    });

    const equity = shocks.map((s, idx) => ({
      day: s.day,
      pnl: s.pnlPath,
      equity: s.pnlPath,
      label: `D${idx + 1}`,
    }));

    const pnls = shocks.flatMap((s) => [s.pnlUp, s.pnlDown]);
    const wins = pnls.filter((p) => p > 0).length;
    const avg = pnls.reduce((a, b) => a + b, 0) / (pnls.length || 1);
    const maxDd = Math.min(...equity.map((e) => e.equity), 0);
    const peak = Math.max(...equity.map((e) => e.equity), 0);
    return {
      summary: summaryLocal,
      shocks,
      equity,
      winRate: wins / (pnls.length || 1),
      avg,
      maxDd,
      peak,
    };
  }, [days, legs, pathBias, shockPct, spot, strikeStep]);

  function downloadCsv() {
    if (!result) return;
    const lines = [
      "day,up_spot,pnl_up,down_spot,pnl_down,path_spot,path_pnl",
      ...result.shocks.map(
        (s) =>
          `${s.day},${s.up.toFixed(2)},${s.pnlUp.toFixed(2)},${s.down.toFixed(2)},${s.pnlDown.toFixed(2)},${s.pathSpot.toFixed(2)},${s.pnlPath.toFixed(2)}`,
      ),
    ];
    const blob = new Blob([lines.join("\n")], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `options-lab-model-backtest-${days}d.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }

  async function onSave() {
    if (!legs.length || spot == null) return;
    setSaving(true);
    setMessage(null);
    try {
      const token = await getAccessToken();
      if (!token) {
        setMessage("Not signed in.");
        return;
      }
      const res = await createOptionsLabBacktest(token, {
        name: saveName.trim() || undefined,
        legs: legs.map((leg) => ({
          id: leg.id,
          side: leg.side,
          type: leg.type,
          strike: leg.strike,
          qty: leg.qty,
          premium: leg.premium,
        })),
        spot,
        days,
        shock_pct: shockPct,
        path_bias: pathBias,
        strike_step: strikeStep,
        underlying_symbol: underlyingSymbol,
        underlying_label: underlyingLabel,
      });
      if (!res.ok || !res.backtest) {
        setMessage(res.error ?? "Save failed");
        return;
      }
      setSaveName("");
      setMessage(`Saved “${res.backtest.name}” (${res.backtest.id}) · fidelity model`);
      await refreshSaved();
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "Save failed");
    } finally {
      setSaving(false);
    }
  }

  async function onDelete(id: string) {
    const token = await getAccessToken();
    if (!token) return;
    const res = await deleteOptionsLabBacktest(token, id);
    if (!res.ok) {
      setMessage(res.error ?? "Delete failed");
      return;
    }
    setSelectedIds((prev) => prev.filter((x) => x !== id));
    await refreshSaved();
  }

  async function onCreateBot(row: OptionsLabBacktestSummaryRow) {
    const id = String(row.id ?? "");
    if (!id) return;
    const token = await getAccessToken();
    if (!token) return;
    const res = await createOptionsLabBot(token, {
      name: `Bot · ${row.name || id}`,
      backtest_id: id,
      mode: "paper",
      enabled: false,
      profit_pct: 50,
      stop_pct: 40,
      underlying_symbol: row.underlying_symbol ?? underlyingSymbol,
      source: "backtest",
    });
    if (!res.ok) {
      setMessage(res.error ?? "Create bot failed");
      return;
    }
    setMessage(
      `Bot “${res.bot?.name}” created — open Automations tab to arm (paper).`,
    );
  }

  async function onSummary() {
    const token = await getAccessToken();
    if (!token) return;
    const res = await summarizeOptionsLabBacktests(token, {
      ids: selectedIds.length ? selectedIds : undefined,
      limit: 5,
    });
    if (!res.ok) {
      setMessage(res.error ?? "Summary failed");
      setSummary(null);
      return;
    }
    setSummary(res);
  }

  if (!legs.length) {
    return (
      <p className="py-10 text-center text-sm text-slate-muted">
        Build a strategy in the rail first, then run a model backtest.
      </p>
    );
  }

  const equityMin = result ? Math.min(...result.equity.map((e) => e.equity), 0) : 0;
  const equityMax = result ? Math.max(...result.equity.map((e) => e.equity), 0) : 0;
  const equitySpan = Math.max(1e-6, equityMax - equityMin);

  return (
    <div className="flex flex-col gap-3 pt-3">
      <p className="text-xs text-slate-muted">
        Model estimate (expiry payoff vs spot path) — not historical option tick replay. Shock % is
        a daily proxy; path moves scale with √t. Fidelity:{" "}
        <span className="font-semibold text-ink">
          {ivCalibrated ? "model · IV-calibrated" : "model"}
        </span>
        {ivCalibrated
          ? ` · ATM IV ${ivCalibrated.latest.toFixed(1)}% (${ivCalibrated.samples} samples)`
          : null}
        . Saved runs are available for Wave 2 bot handoff.
      </p>
      <div className="flex flex-wrap items-end gap-3">
        <label className="text-xs text-slate-muted">
          Window (days)
          <input
            type="number"
            min={3}
            max={45}
            value={days}
            onChange={(e) => setDays(Math.max(3, Math.min(45, Number(e.target.value) || 10)))}
            className="ml-1 w-16 rounded border border-line bg-canvas px-2 py-1 text-sm"
          />
        </label>
        <label className="text-xs text-slate-muted">
          Daily shock %
          <input
            type="number"
            min={0.5}
            max={15}
            step={0.5}
            value={shockPct}
            onChange={(e) => setShockPct(Math.max(0.5, Number(e.target.value) || 2))}
            className="ml-1 w-16 rounded border border-line bg-canvas px-2 py-1 text-sm"
          />
        </label>
        <label className="text-xs text-slate-muted">
          Path bias
          <select
            value={pathBias}
            onChange={(e) => setPathBias(e.target.value as "up" | "down" | "flat")}
            className="ml-1 rounded border border-line bg-canvas px-2 py-1 text-sm"
          >
            <option value="flat">flat (oscillate)</option>
            <option value="up">bullish</option>
            <option value="down">bearish</option>
          </select>
        </label>
        {ivCalibrated ? (
          <Button
            type="button"
            size="sm"
            variant="secondary"
            onClick={() => setShockPct(ivCalibrated.shockPct)}
          >
            Use IV shock ({ivCalibrated.shockPct}%)
          </Button>
        ) : null}
        <Button type="button" size="sm" variant="secondary" onClick={downloadCsv} disabled={!result}>
          Download CSV
        </Button>
      </div>

      <div className="flex flex-wrap items-end gap-2 rounded border border-line bg-canvas/40 px-2 py-2">
        <label className="text-xs text-slate-muted">
          Save as
          <input
            value={saveName}
            onChange={(e) => setSaveName(e.target.value)}
            placeholder="Model · 10d"
            className="ml-1 w-40 rounded border border-line bg-canvas px-2 py-1 text-sm"
          />
        </label>
        <Button type="button" size="sm" disabled={saving || spot == null} onClick={() => void onSave()}>
          {saving ? "Saving…" : "Save run"}
        </Button>
        <Button type="button" size="sm" variant="secondary" onClick={() => void onSummary()}>
          Portfolio summary
        </Button>
      </div>
      {message ? <p className="text-xs text-slate-muted">{message}</p> : null}

      {result ? (
        <>
          <div className="grid grid-cols-2 gap-2 text-sm sm:grid-cols-4">
            <div className="rounded border border-line px-2 py-2">
              <p className="th-label">Model hit rate</p>
              <p className="font-semibold tabular-nums">{(result.winRate * 100).toFixed(0)}%</p>
              <p className="mt-0.5 text-[10px] text-slate-muted">
                Up/down expiry samples with P&amp;L&gt;0 — not trade win rate
              </p>
            </div>
            <div className="rounded border border-line px-2 py-2">
              <p className="th-label">Avg P&amp;L</p>
              <p className="font-semibold tabular-nums">{result.avg.toFixed(1)}</p>
            </div>
            <div className="rounded border border-line px-2 py-2">
              <p className="th-label">Path trough</p>
              <p className="font-semibold tabular-nums">{result.maxDd.toFixed(1)}</p>
            </div>
            <div className="rounded border border-line px-2 py-2">
              <p className="th-label">Path peak</p>
              <p className="font-semibold tabular-nums">{result.peak.toFixed(1)}</p>
            </div>
          </div>

          <div className="rounded border border-line px-2 py-2">
            <p className="th-label mb-1">Model equity (path P&amp;L by day)</p>
            <svg viewBox="0 0 320 72" className="h-16 w-full text-teal" aria-hidden>
              <polyline
                fill="none"
                stroke="currentColor"
                strokeWidth="1.5"
                points={result.equity
                  .map((e, i) => {
                    const x =
                      result.equity.length <= 1 ? 0 : (i / (result.equity.length - 1)) * 320;
                    const y = 68 - ((e.equity - equityMin) / equitySpan) * 60;
                    return `${x},${y}`;
                  })
                  .join(" ")}
              />
            </svg>
          </div>

          <div className="overflow-auto rounded border border-line">
            <table className="w-full text-left text-xs">
              <thead className="bg-raised text-slate-muted">
                <tr>
                  <th className="px-2 py-1">Day</th>
                  <th className="px-2 py-1">Up spot</th>
                  <th className="px-2 py-1">P&amp;L</th>
                  <th className="px-2 py-1">Down spot</th>
                  <th className="px-2 py-1">P&amp;L</th>
                  <th className="px-2 py-1">Path</th>
                </tr>
              </thead>
              <tbody>
                {result.shocks.map((s) => (
                  <tr key={s.day} className="border-t border-line">
                    <td className="px-2 py-1 tabular-nums">{s.day}</td>
                    <td className="px-2 py-1 tabular-nums">{s.up.toFixed(0)}</td>
                    <td className="px-2 py-1 tabular-nums">{s.pnlUp.toFixed(1)}</td>
                    <td className="px-2 py-1 tabular-nums">{s.down.toFixed(0)}</td>
                    <td className="px-2 py-1 tabular-nums">{s.pnlDown.toFixed(1)}</td>
                    <td className="px-2 py-1 tabular-nums">{s.pnlPath.toFixed(1)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : null}

      <div className="rounded border border-line p-2">
        <p className="th-label mb-2">Saved model runs</p>
        {saved.length === 0 ? (
          <p className="text-xs text-slate-muted">No saved runs yet.</p>
        ) : (
          <ul className="divide-y divide-line text-xs">
            {saved.map((row) => {
              const id = String(row.id ?? "");
              const on = selectedIds.includes(id);
              return (
                <li key={id} className="flex flex-wrap items-center justify-between gap-2 py-1.5">
                  <label className="flex min-w-0 items-center gap-2">
                    <input
                      type="checkbox"
                      checked={on}
                      onChange={() =>
                        setSelectedIds((prev) =>
                          on ? prev.filter((x) => x !== id) : [...prev, id],
                        )
                      }
                    />
                    <span className="truncate font-medium text-ink">{row.name}</span>
                    <span className="text-slate-muted">
                      {row.stats
                        ? `hit ${(row.stats.hit_rate * 100).toFixed(0)}% · avg ${row.stats.avg_pnl.toFixed(0)}`
                        : row.fidelity}
                    </span>
                  </label>
                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      className="text-teal hover:underline"
                      onClick={() => void onCreateBot(row)}
                    >
                      Create bot
                    </button>
                    <button
                      type="button"
                      className="text-rose hover:underline"
                      onClick={() => void onDelete(id)}
                    >
                      Delete
                    </button>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </div>

      {summary?.ok && summary.stats ? (
        <div className="rounded border border-line p-2 text-xs">
          <p className="th-label mb-1">Portfolio summary ({summary.count} runs)</p>
          <p className="text-slate-muted">{summary.note}</p>
          <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-4">
            <div>
              <p className="text-slate-muted">Avg hit</p>
              <p className="font-semibold tabular-nums">
                {(summary.stats.avg_hit_rate * 100).toFixed(0)}%
              </p>
            </div>
            <div>
              <p className="text-slate-muted">Avg P&amp;L</p>
              <p className="font-semibold tabular-nums">{summary.stats.avg_pnl.toFixed(1)}</p>
            </div>
            <div>
              <p className="text-slate-muted">Avg trough</p>
              <p className="font-semibold tabular-nums">
                {summary.stats.avg_path_trough.toFixed(1)}
              </p>
            </div>
            <div>
              <p className="text-slate-muted">Avg peak</p>
              <p className="font-semibold tabular-nums">{summary.stats.avg_path_peak.toFixed(1)}</p>
            </div>
          </div>
          {(summary.correlations ?? []).length > 0 ? (
            <ul className="mt-2 space-y-0.5 text-slate-muted">
              {(summary.correlations ?? []).map((c) => (
                <li key={`${c.a}-${c.b}`} className={cn("tabular-nums")}>
                  {c.a_name} ↔ {c.b_name}: corr {c.corr?.toFixed(2)}
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
