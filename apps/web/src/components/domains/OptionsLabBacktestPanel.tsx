"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { Button } from "@/components/ui/Button";
import {
  ChartLineIcon,
  DownloadIcon,
  PlusIcon,
  SaveIcon,
  TrashIcon,
} from "@/components/ui/icons";
import type { StrategyLeg } from "@/components/domains/options-lab-strategy";
import {
  buildPayoffCurve,
  resolveDaysToExpiry,
  strategyPnlAtBsMark,
  summarizeStrategy,
} from "@/components/domains/options-lab-strategy";
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

/** Match Options Lab desk toolbar (Wings select / Button sm). */
const fieldClass =
  "rounded-md border border-line bg-canvas px-2 py-1.5 text-xs font-medium tracking-tight text-ink";
const fieldNarrowClass = cn(fieldClass, "w-16 px-1.5");

function pnlTone(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value) || value === 0) return "text-ink";
  return value > 0 ? "text-teal" : "text-rose";
}

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
  futSymbol,
  handoffHint,
}: {
  legs: StrategyLeg[];
  spot: number | null;
  strikeStep: number;
  ivPoints?: OptionsIvPoint[];
  getAccessToken: () => Promise<string | null>;
  underlyingSymbol?: string;
  underlyingLabel?: string;
  futSymbol?: string;
  /** e.g. Ideas handoff label while chain/template legs load. */
  handoffHint?: string | null;
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
  const [useHistorical, setUseHistorical] = useState(false);
  const [useMarks, setUseMarks] = useState(false);
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

  const entryDte = useMemo(() => {
    const fromLegs = resolveDaysToExpiry({
      futSymbol,
      optionSymbols: legs.map((leg) => leg.symbol),
    });
    if (fromLegs != null && fromLegs > 0) return fromLegs;
    return null;
  }, [futSymbol, legs]);

  const result = useMemo(() => {
    if (!legs.length || spot == null || !Number.isFinite(spot)) return null;
    const dte0 = entryDte != null && entryDte > 0 ? entryDte : days;
    const lifeDays = Math.max(1, Math.ceil(dte0));
    const pathDays = Math.min(days, lifeDays);
    const ivPct = ivCalibrated?.latest ?? 15;
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
    const markPnl = (target: number, day: number) => {
      const rawYears = (dte0 - day) / 365;
      const years =
        rawYears <= 0 ? 0 : rawYears < 0.0005 ? 0.0005 : rawYears;
      return strategyPnlAtBsMark(legs, { spot: target, years, ivPct });
    };

    const shocks = Array.from({ length: pathDays }, (_, i) => {
      const day = i + 1;
      const sqrtT = Math.sqrt(day);
      const move = (shockPct / 100) * sqrtT;
      const up = spot * (1 + move);
      const down = spot * (1 - move);
      let pathSpot: number;
      if (pathBias === "up") {
        pathSpot = spot * (1 + move * 0.85);
      } else if (pathBias === "down") {
        pathSpot = spot * (1 - move * 0.85);
      } else {
        pathSpot = spot * (1 + Math.sin((day / pathDays) * Math.PI * 2) * move * 0.35);
      }
      return {
        day,
        up,
        down,
        pathSpot,
        pnlUp: useMarks ? markPnl(up, day) : nearest(up),
        pnlDown: useMarks ? markPnl(down, day) : nearest(down),
        pnlPath: useMarks ? markPnl(pathSpot, day) : nearest(pathSpot),
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
    const maxDd = Math.min(...equity.map((e) => e.equity));
    const peak = Math.max(...equity.map((e) => e.equity));
    return {
      summary: summaryLocal,
      shocks,
      equity,
      winRate: wins / (pnls.length || 1),
      avg,
      maxDd,
      peak,
      fidelityPreview: useMarks ? "bs_marks" : "model",
      entryDte: dte0,
    };
  }, [
    days,
    entryDte,
    ivCalibrated?.latest,
    legs,
    pathBias,
    shockPct,
    spot,
    strikeStep,
    useMarks,
  ]);

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
        use_historical: useHistorical,
        use_marks: useMarks,
        iv_pct: ivCalibrated?.latest,
        entry_dte: entryDte ?? days,
      });
      if (!res.ok || !res.backtest) {
        setMessage(res.error ?? "Save failed");
        return;
      }
      setSaveName("");
      const fidelity =
        res.backtest.fidelity ??
        (useMarks ? "bs_marks" : useHistorical ? "model_hist" : "model");
      const warnBits = res.warnings?.length ? ` · ${res.warnings.join(" ")}` : "";
      setMessage(
        `Saved “${res.backtest.name}” (${res.backtest.id}) · fidelity ${fidelity}${warnBits}`,
      );
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
      avoid_events: true,
      max_dte_hold: 1,
      underlying_symbol: row.underlying_symbol ?? underlyingSymbol,
      source: "backtest",
    });
    if (!res.ok) {
      setMessage(res.error ?? "Create bot failed");
      return;
    }
    setMessage(
      `Bot “${res.bot?.name}” created — open Bot (next to Backtest) to arm (paper).`,
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
      <div className="flex flex-col gap-2 py-10 text-center text-sm text-slate-muted">
        {handoffHint ? (
          <p className="text-ink">
            Loading idea into Lab: <span className="font-medium">{handoffHint}</span>
          </p>
        ) : null}
        <p>
          {handoffHint
            ? "Waiting for chain quotes + template legs — keep this overlay open a moment."
            : "Build a strategy in the rail first, then run a model backtest."}
        </p>
      </div>
    );
  }

  const pathStart = result?.equity[0]?.equity ?? null;
  const pathEnd =
    result && result.equity.length
      ? result.equity[result.equity.length - 1]!.equity
      : null;
  const pathDelta =
    pathStart != null && pathEnd != null ? pathEnd - pathStart : null;
  const troughPt =
    result?.equity.reduce(
      (best, e) => (e.equity < best.equity ? e : best),
      result.equity[0]!,
    ) ?? null;
  const peakPt =
    result?.equity.reduce(
      (best, e) => (e.equity > best.equity ? e : best),
      result.equity[0]!,
    ) ?? null;
  const endShock = result?.shocks.length
    ? result.shocks[result.shocks.length - 1]!
    : null;
  /** Scale to path range only (plus small pad) so the line uses the full plot. */
  const pathVals = result?.equity.map((e) => e.equity) ?? [];
  const rawMin = pathVals.length ? Math.min(...pathVals) : 0;
  const rawMax = pathVals.length ? Math.max(...pathVals) : 0;
  const padAmt = Math.max(1, (rawMax - rawMin) * 0.12);
  const chartMin = rawMin - padAmt;
  const chartMax = rawMax + padAmt;
  const chartSpan = Math.max(1e-6, chartMax - chartMin);
  const showBreakeven = 0 >= chartMin && 0 <= chartMax;
  const equityPoints = result
    ? result.equity.map((e, i) => {
        const n = result.equity.length;
        const x = n <= 1 ? 50 : (i / (n - 1)) * 100;
        const y = ((chartMax - e.equity) / chartSpan) * 100;
        return { x, y, ...e };
      })
    : [];
  const equityPolyline = equityPoints.map((p) => `${p.x},${p.y}`).join(" ");
  const zeroY = ((chartMax - 0) / chartSpan) * 100;
  const equityArea =
    equityPoints.length > 0
      ? `${equityPoints[0]!.x},100 ${equityPolyline} ${equityPoints[equityPoints.length - 1]!.x},100`
      : "";
  const troughIsEnd =
    troughPt != null &&
    pathEnd != null &&
    troughPt.day === result?.equity.length &&
    Math.abs(troughPt.equity - pathEnd) < 1e-9;
  const peakIsEnd =
    peakPt != null &&
    pathEnd != null &&
    peakPt.day === result?.equity.length &&
    Math.abs(peakPt.equity - pathEnd) < 1e-9;

  return (
    <div className="mt-3 flex min-h-0 flex-1 flex-col gap-3">
      {handoffHint ? (
        <p className="rounded-md border border-teal/30 bg-teal/10 px-2 py-1.5 text-xs text-teal">
          From Ideas: {handoffHint}
        </p>
      ) : null}
      <p className="text-sm text-slate-muted">
        {useMarks
          ? "Black-76 mark path (flat IV) vs entry premium — not NSE tick / chain archive."
          : "Model estimate (expiry payoff vs spot path) — not historical option tick replay."}{" "}
        Shock % is a daily proxy; path moves scale with √t. Preview fidelity:{" "}
        <span className="font-medium text-ink">
          {useMarks
            ? "bs_marks"
            : ivCalibrated
              ? "model · IV-calibrated"
              : "model"}
        </span>
        {ivCalibrated
          ? ` · ATM IV ${ivCalibrated.latest.toFixed(1)}% (${ivCalibrated.samples} samples)`
          : null}
        {entryDte != null ? ` · DTE ${entryDte}` : " · DTE≈window"}
        . Prefer historical closes on save for{" "}
        <span className="font-medium text-ink">model_hist</span>
        {useMarks ? " under BS marks" : ""}.
      </p>
      {useHistorical && !useMarks ? (
        <p className="rounded-md border border-line bg-fog/40 px-2 py-1.5 text-xs text-slate-muted">
          On-screen chart/table stay <span className="font-medium text-ink">model</span>{" "}
          preview. <span className="font-medium text-ink">Save run</span> writes{" "}
          <span className="font-medium text-ink">model_hist</span>.
        </p>
      ) : null}
      <div className="flex w-full flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <label className="flex items-center gap-1.5 text-xs text-slate-muted">
            Window (days)
            <input
              type="number"
              min={3}
              max={45}
              value={days}
              onChange={(e) => setDays(Math.max(3, Math.min(45, Number(e.target.value) || 10)))}
              className={fieldNarrowClass}
            />
          </label>
          <label className="flex items-center gap-1.5 text-xs text-slate-muted">
            Daily shock %
            <input
              type="number"
              min={0.5}
              max={15}
              step={0.5}
              value={shockPct}
              onChange={(e) => setShockPct(Math.max(0.5, Number(e.target.value) || 2))}
              className={fieldNarrowClass}
            />
          </label>
          <label className="flex items-center gap-1.5 text-xs text-slate-muted">
            Path bias
            <select
              value={pathBias}
              onChange={(e) => setPathBias(e.target.value as "up" | "down" | "flat")}
              disabled={useHistorical}
              className={cn(fieldClass, "disabled:opacity-50")}
            >
              <option value="flat">flat (oscillate)</option>
              <option value="up">bullish</option>
              <option value="down">bearish</option>
            </select>
          </label>
          <label className="flex cursor-pointer items-center gap-1.5 rounded-md border border-line bg-raised px-2 py-1 text-xs font-medium text-ink">
            <input
              type="checkbox"
              className="size-3.5 shrink-0 rounded border-line text-teal focus-visible:ring-2 focus-visible:ring-teal/30"
              checked={useHistorical}
              onChange={(e) => setUseHistorical(e.target.checked)}
            />
            Prefer historical closes
          </label>
          <label className="flex cursor-pointer items-center gap-1.5 rounded-md border border-line bg-raised px-2 py-1 text-xs font-medium text-ink">
            <input
              type="checkbox"
              className="size-3.5 shrink-0 rounded border-line text-teal focus-visible:ring-2 focus-visible:ring-teal/30"
              checked={useMarks}
              onChange={(e) => setUseMarks(e.target.checked)}
            />
            Mark path (BS)
          </label>
        </div>
        {ivCalibrated ? (
          <Button
            type="button"
            size="sm"
            variant="secondary"
            className="ml-auto shrink-0"
            icon={<ChartLineIcon />}
            onClick={() => setShockPct(ivCalibrated.shockPct)}
          >
            Use IV shock ({ivCalibrated.shockPct}%)
          </Button>
        ) : null}
      </div>

      <div className="flex w-full flex-wrap items-center justify-between gap-x-3 gap-y-2 rounded-lg border border-line bg-canvas/40 px-2.5 py-2">
        <label className="flex items-center gap-1.5 text-xs text-slate-muted">
          Save as
          <input
            value={saveName}
            onChange={(e) => setSaveName(e.target.value)}
            placeholder="Model · 10d"
            className={cn(fieldClass, "w-40")}
          />
        </label>
        <div className="ml-auto flex flex-wrap items-center justify-end gap-2">
          <Button
            type="button"
            size="sm"
            icon={<SaveIcon />}
            disabled={saving || spot == null}
            onClick={() => void onSave()}
          >
            {saving ? "Saving…" : "Save run"}
          </Button>
          <Button
            type="button"
            size="sm"
            variant="secondary"
            icon={<ChartLineIcon />}
            onClick={() => void onSummary()}
          >
            Portfolio summary
          </Button>
          <Button
            type="button"
            size="sm"
            variant="secondary"
            icon={<DownloadIcon />}
            onClick={downloadCsv}
            disabled={!result}
          >
            Download CSV
          </Button>
        </div>
      </div>
      {message ? <p className="text-xs text-slate-muted">{message}</p> : null}

      {result ? (
        <>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            <div
              className="rounded-lg border border-line bg-raised/40 px-2.5 py-2"
              title="Share of up/down expiry samples with P&L > 0 — not trade win rate"
            >
              <p className="th-label">Model hit rate</p>
              <p
                className={cn(
                  "mt-0.5 text-lg font-semibold tabular-nums",
                  result.winRate >= 0.5 ? "text-teal" : "text-rose",
                )}
              >
                {(result.winRate * 100).toFixed(0)}%
              </p>
            </div>
            <div className="rounded-lg border border-line bg-raised/40 px-2.5 py-2">
              <p className="th-label">Avg P&amp;L</p>
              <p className={cn("mt-0.5 text-lg font-semibold tabular-nums", pnlTone(result.avg))}>
                {result.avg.toFixed(1)}
              </p>
            </div>
            <div className="rounded-lg border border-line bg-raised/40 px-2.5 py-2">
              <p className="th-label">Path trough</p>
              <p className={cn("mt-0.5 text-lg font-semibold tabular-nums", pnlTone(result.maxDd))}>
                {result.maxDd.toFixed(1)}
                {troughPt != null ? (
                  <span className="ml-1 text-xs font-normal text-slate-muted">
                    D{troughPt.day}
                  </span>
                ) : null}
              </p>
            </div>
            <div className="rounded-lg border border-line bg-raised/40 px-2.5 py-2">
              <p className="th-label">Path peak</p>
              <p className={cn("mt-0.5 text-lg font-semibold tabular-nums", pnlTone(result.peak))}>
                {result.peak.toFixed(1)}
                {peakPt != null ? (
                  <span className="ml-1 text-xs font-normal text-slate-muted">
                    D{peakPt.day}
                  </span>
                ) : null}
              </p>
            </div>
          </div>

          <div className="overflow-hidden rounded-lg border border-line bg-raised/40">
            <div className="flex flex-wrap items-start justify-between gap-3 border-b border-line/60 px-3 py-2">
              <div className="min-w-0">
                <p className="th-label">Model equity</p>
                <p className="mt-0.5 text-xs text-slate-muted">
                  Path P&amp;L by day · {pathBias} · {shockPct}%/√t ·{" "}
                  {result.equity.length}d
                  {useMarks ? " · BS marks" : " · expiry payoff"}
                  {troughIsEnd ? " · ends at trough" : null}
                  {peakIsEnd ? " · ends at peak" : null}
                </p>
              </div>
              <div className="flex flex-wrap items-end gap-4 text-right">
                <div>
                  <p className="th-label">High</p>
                  <p
                    className={cn(
                      "text-sm font-semibold tabular-nums",
                      pnlTone(peakPt?.equity),
                    )}
                  >
                    {peakPt != null ? peakPt.equity.toFixed(1) : "—"}
                    {peakPt != null ? (
                      <span className="ml-1 text-[10px] font-normal text-slate-muted">
                        D{peakPt.day}
                      </span>
                    ) : null}
                  </p>
                </div>
                <div>
                  <p className="th-label">Low</p>
                  <p
                    className={cn(
                      "text-sm font-semibold tabular-nums",
                      pnlTone(troughPt?.equity),
                    )}
                  >
                    {troughPt != null ? troughPt.equity.toFixed(1) : "—"}
                    {troughPt != null ? (
                      <span className="ml-1 text-[10px] font-normal text-slate-muted">
                        D{troughPt.day}
                      </span>
                    ) : null}
                  </p>
                </div>
                <div>
                  <p className="th-label">End</p>
                  <p
                    className={cn(
                      "text-lg font-semibold tabular-nums",
                      pnlTone(pathEnd),
                    )}
                  >
                    {pathEnd != null
                      ? `${pathEnd > 0 ? "+" : ""}${pathEnd.toFixed(1)}`
                      : "—"}
                  </p>
                  {pathDelta != null ? (
                    <p className={cn("text-[11px] tabular-nums", pnlTone(pathDelta))}>
                      {pathDelta > 0 ? "+" : ""}
                      {pathDelta.toFixed(1)} vs D1
                    </p>
                  ) : null}
                </div>
              </div>
            </div>

            <div className="px-3 py-2">
              <div
                className={cn(
                  "relative h-24 w-full overflow-hidden",
                  pathEnd != null && pathEnd >= 0 ? "text-teal" : "text-rose",
                )}
              >
                <svg
                  viewBox="0 0 100 100"
                  preserveAspectRatio="none"
                  className="absolute inset-0 h-full w-full"
                  aria-hidden
                >
                  {showBreakeven ? (
                    <line
                      x1="0"
                      y1={zeroY}
                      x2="100"
                      y2={zeroY}
                      stroke="currentColor"
                      strokeOpacity="0.3"
                      strokeWidth="0.6"
                      strokeDasharray="2 2"
                      vectorEffect="non-scaling-stroke"
                    />
                  ) : null}
                  {equityArea ? (
                    <polygon
                      points={equityArea}
                      fill="currentColor"
                      fillOpacity="0.12"
                      stroke="none"
                    />
                  ) : null}
                  <polyline
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                    strokeLinejoin="round"
                    strokeLinecap="round"
                    points={equityPolyline}
                    vectorEffect="non-scaling-stroke"
                  />
                </svg>
                {equityPoints.map((p) => {
                  const isHi = p.day === peakPt?.day;
                  const isLo = p.day === troughPt?.day;
                  return (
                    <span
                      key={`dot-${p.day}`}
                      className={cn(
                        "absolute rounded-full bg-current",
                        isHi || isLo ? "size-2.5" : "size-1.5",
                      )}
                      style={{
                        left: `${p.x}%`,
                        top: `${p.y}%`,
                        transform: "translate(-50%, -50%)",
                      }}
                      title={`D${p.day}: ${p.equity.toFixed(1)}`}
                    />
                  );
                })}
              </div>

              <div
                className="mt-1.5 grid gap-1"
                style={{
                  gridTemplateColumns: `repeat(${Math.max(result.equity.length, 1)}, minmax(0, 1fr))`,
                }}
              >
                {result.equity.map((e) => {
                  const isHi = e.day === peakPt?.day;
                  const isLo = e.day === troughPt?.day;
                  return (
                    <div
                      key={e.day}
                      className={cn(
                        "rounded-md px-1 py-1 text-center",
                        isHi || isLo ? "bg-fog/80" : undefined,
                      )}
                    >
                      <p className="th-label">D{e.day}</p>
                      <p
                        className={cn(
                          "text-xs font-semibold tabular-nums",
                          pnlTone(e.equity),
                        )}
                      >
                        {e.equity.toFixed(1)}
                      </p>
                    </div>
                  );
                })}
              </div>

              {endShock != null && spot != null ? (
                <p className="mt-1.5 text-[11px] text-slate-muted">
                  End spot{" "}
                  <span className="font-semibold tabular-nums text-ink">
                    {endShock.pathSpot.toFixed(0)}
                  </span>
                  <span className={cn("tabular-nums", pnlTone(endShock.pathSpot - spot))}>
                    {" "}
                    ({endShock.pathSpot - spot >= 0 ? "+" : ""}
                    {(((endShock.pathSpot - spot) / spot) * 100).toFixed(1)}% vs entry)
                  </span>
                  {showBreakeven ? " · dashed = breakeven" : null}
                </p>
              ) : null}
            </div>
          </div>

          <div className="rounded-lg border border-line">
            <table className="w-full border-collapse text-sm">
              <thead className="bg-raised/95">
                <tr className="border-b border-line">
                  <th className="th-label px-1.5 py-1 text-left">Day</th>
                  <th className="th-label px-1.5 py-1 text-right">Up spot</th>
                  <th className="th-label px-1.5 py-1 text-right">P&amp;L</th>
                  <th className="th-label px-1.5 py-1 text-right">Down spot</th>
                  <th className="th-label px-1.5 py-1 text-right">P&amp;L</th>
                  <th className="th-label px-1.5 py-1 text-right">Path</th>
                </tr>
              </thead>
              <tbody>
                {result.shocks.map((s) => (
                  <tr key={s.day} className="border-b border-line/50">
                    <td className="px-1.5 py-1 tabular-nums text-slate-muted">
                      {s.day}
                    </td>
                    <td className="px-1.5 py-1 text-right font-semibold tabular-nums text-ink">
                      {s.up.toFixed(0)}
                    </td>
                    <td
                      className={cn(
                        "px-1.5 py-1 text-right font-semibold tabular-nums",
                        pnlTone(s.pnlUp),
                      )}
                    >
                      {s.pnlUp.toFixed(1)}
                    </td>
                    <td className="px-1.5 py-1 text-right font-semibold tabular-nums text-ink">
                      {s.down.toFixed(0)}
                    </td>
                    <td
                      className={cn(
                        "px-1.5 py-1 text-right font-semibold tabular-nums",
                        pnlTone(s.pnlDown),
                      )}
                    >
                      {s.pnlDown.toFixed(1)}
                    </td>
                    <td
                      className={cn(
                        "px-1.5 py-1 text-right font-semibold tabular-nums",
                        pnlTone(s.pnlPath),
                      )}
                    >
                      {s.pnlPath.toFixed(1)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : null}

      <div className="rounded-lg border border-line p-3">
        <p className="th-label mb-2">Saved model runs</p>
        {saved.length === 0 ? (
          <p className="text-sm text-slate-muted">No saved runs yet.</p>
        ) : (
          <ul className="divide-y divide-line">
            {saved.map((row) => {
              const id = String(row.id ?? "");
              const on = selectedIds.includes(id);
              return (
                <li
                  key={id}
                  className="flex flex-wrap items-center justify-between gap-2 py-2"
                >
                  <label className="flex min-w-0 items-center gap-2 text-xs">
                    <input
                      type="checkbox"
                      className="size-3.5 shrink-0 rounded border-line text-teal"
                      checked={on}
                      onChange={() =>
                        setSelectedIds((prev) =>
                          on ? prev.filter((x) => x !== id) : [...prev, id],
                        )
                      }
                    />
                    <span className="truncate text-sm font-medium text-ink">{row.name}</span>
                    <span className="text-slate-muted">
                      {row.fidelity ? `${row.fidelity} · ` : ""}
                      {row.stats ? (
                        <>
                          hit {(row.stats.hit_rate * 100).toFixed(0)}% · avg{" "}
                          <span className={pnlTone(row.stats.avg_pnl)}>
                            {row.stats.avg_pnl.toFixed(0)}
                          </span>
                        </>
                      ) : null}
                    </span>
                  </label>
                  <div className="ml-auto flex items-center gap-2">
                    <Button
                      type="button"
                      size="sm"
                      variant="secondary"
                      icon={<PlusIcon />}
                      onClick={() => void onCreateBot(row)}
                    >
                      Create bot
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      variant="danger"
                      icon={<TrashIcon />}
                      onClick={() => void onDelete(id)}
                    >
                      Delete
                    </Button>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </div>

      {summary?.ok && summary.stats ? (
        <div className="rounded-lg border border-line p-3">
          <p className="th-label mb-1">Portfolio summary ({summary.count} runs)</p>
          <p className="text-sm text-slate-muted">{summary.note}</p>
          <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
            <div className="rounded-lg border border-line bg-raised/40 px-3 py-3">
              <p className="th-label">Avg hit</p>
              <p className="mt-1 text-xl font-semibold tabular-nums text-ink">
                {(summary.stats.avg_hit_rate * 100).toFixed(0)}%
              </p>
            </div>
            <div className="rounded-lg border border-line bg-raised/40 px-3 py-3">
              <p className="th-label">Avg P&amp;L</p>
              <p
                className={cn(
                  "mt-1 text-xl font-semibold tabular-nums",
                  pnlTone(summary.stats.avg_pnl),
                )}
              >
                {summary.stats.avg_pnl.toFixed(1)}
              </p>
            </div>
            <div className="rounded-lg border border-line bg-raised/40 px-3 py-3">
              <p className="th-label">Avg trough</p>
              <p
                className={cn(
                  "mt-1 text-xl font-semibold tabular-nums",
                  pnlTone(summary.stats.avg_path_trough),
                )}
              >
                {summary.stats.avg_path_trough.toFixed(1)}
              </p>
            </div>
            <div className="rounded-lg border border-line bg-raised/40 px-3 py-3">
              <p className="th-label">Avg peak</p>
              <p
                className={cn(
                  "mt-1 text-xl font-semibold tabular-nums",
                  pnlTone(summary.stats.avg_path_peak),
                )}
              >
                {summary.stats.avg_path_peak.toFixed(1)}
              </p>
            </div>
          </div>
          {(summary.correlations ?? []).length > 0 ? (
            <ul className="mt-2 space-y-0.5 text-xs text-slate-muted">
              {(summary.correlations ?? []).map((c) => (
                <li key={`${c.a}-${c.b}`} className="tabular-nums">
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
