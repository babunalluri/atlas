"use client";

import { useEffect, useMemo, useState } from "react";

import { Button } from "@/components/ui/Button";
import type { StrategyLeg } from "@/components/domains/options-lab-strategy";
import { buildPayoffCurve, summarizeStrategy } from "@/components/domains/options-lab-strategy";
import type { OptionsIvPoint } from "@/lib/api/admin";

/**
 * Model backtest overlay — expiry payoff vs synthetic spot path.
 * When ATM IV history is present, default shock is calibrated from latest IV
 * as a **daily** move proxy; path shocks scale with √t (not linear in t).
 */
export function OptionsLabBacktestPanel({
  legs,
  spot,
  strikeStep,
  ivPoints,
}: {
  legs: StrategyLeg[];
  spot: number | null;
  strikeStep: number;
  ivPoints?: OptionsIvPoint[];
}) {
  const ivCalibrated = useMemo(() => {
    const ivs = (ivPoints ?? [])
      .map((p) => p.iv)
      .filter((v): v is number => typeof v === "number" && Number.isFinite(v) && v > 0);
    if (ivs.length === 0) return null;
    const latest = ivs[ivs.length - 1]!;
    // Daily-ish shock proxy from annualized IV: IV%/√252 ≈ daily move %.
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

  useEffect(() => {
    if (calibratedOnce || !ivCalibrated) return;
    setShockPct(ivCalibrated.shockPct);
    setCalibratedOnce(true);
  }, [calibratedOnce, ivCalibrated]);

  const result = useMemo(() => {
    if (!legs.length || spot == null || !Number.isFinite(spot)) return null;
    const curve = buildPayoffCurve(legs, { spot, strikeStep, wings: 12 });
    const summary = summarizeStrategy(legs, curve);
    const nearest = (target: number) => {
      if (curve.length === 0) return 0;
      let best = curve[0]!;
      for (const p of curve) {
        if (Math.abs(p.spot - target) < Math.abs(best.spot - target)) best = p;
      }
      return best.pnl;
    };

    const shocks = Array.from({ length: days }, (_, i) => {
      // Daily shockPct scaled by √(day index) — same class as volatilitySpotBands.
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
        // Flat: mild oscillating path around spot so equity is not a constant line.
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
      summary,
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
      {result ? (
        <>
          <div className="grid grid-cols-2 gap-2 text-sm sm:grid-cols-4">
            <div className="rounded border border-line px-2 py-2">
              <p className="th-label">Model hit rate</p>
              <p className="font-semibold tabular-nums">
                {(result.winRate * 100).toFixed(0)}%
              </p>
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
                      result.equity.length <= 1
                        ? 0
                        : (i / (result.equity.length - 1)) * 320;
                    const y = 68 - ((e.equity - equityMin) / equitySpan) * 60;
                    return `${x},${y}`;
                  })
                  .join(" ")}
              />
              <line
                x1="0"
                x2="320"
                y1={68 - ((0 - equityMin) / equitySpan) * 60}
                y2={68 - ((0 - equityMin) / equitySpan) * 60}
                stroke="currentColor"
                strokeOpacity="0.25"
                strokeWidth="1"
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
    </div>
  );
}
