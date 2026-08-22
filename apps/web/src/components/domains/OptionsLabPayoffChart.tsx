"use client";

import { useMemo, type ReactNode } from "react";

import type { PayoffPoint } from "@/components/domains/options-lab-strategy";
import { cn } from "@/lib/utils";

const W = 720;
const H_FULL = 320;
const H_FILL = 360;
const PAD = { t: 20, r: 20, b: 36, l: 52 };
const OI_H_FULL = 36;
const OI_H_FILL = 72;

export type PayoffOiBar = {
  strike: number;
  ceOi: number;
  peOi: number;
};

export function OptionsLabPayoffChart({
  points,
  targetPoints,
  scenarioPoints,
  spot,
  breakevens,
  sdBands,
  oiBars,
  targetLabel,
  scenarioLabel,
  compact = false,
  fill = false,
  footer,
}: {
  points: PayoffPoint[];
  targetPoints?: PayoffPoint[];
  scenarioPoints?: PayoffPoint[];
  spot: number | null;
  breakevens: number[];
  sdBands?: { sd1: [number, number]; sd2: [number, number] } | null;
  oiBars?: PayoffOiBar[];
  targetLabel?: string;
  scenarioLabel?: string;
  compact?: boolean;
  /** Stretch to parent height (strategy rail hero). */
  fill?: boolean;
  /** Controls under the graph (target / IV) — keeps analysis together. */
  footer?: ReactNode;
}) {
  const H = fill ? H_FILL : compact ? 220 : H_FULL;
  const oiBand = fill ? OI_H_FILL : OI_H_FULL;
  const hasOi = Boolean(oiBars && oiBars.length > 0);

  const plot = useMemo(() => {
    if (points.length === 0) return null;
    const series = [
      ...points,
      ...(targetPoints ?? []),
      ...(scenarioPoints ?? []),
    ];
    const pnls = series.map((p) => p.pnl);
    const rawMin = Math.min(...pnls, 0);
    const rawMax = Math.max(...pnls, 0);
    const padY = rawMax === rawMin ? Math.max(Math.abs(rawMax) * 0.1, 1) : 0;
    const minY = rawMin - padY;
    const maxY = rawMax + padY;
    const pointSpots = series.map((p) => p.spot);
    const bandSpots = sdBands ? [...sdBands.sd1, ...sdBands.sd2] : [];
    const xCandidates = [
      ...pointSpots,
      ...bandSpots,
      ...(spot != null && Number.isFinite(spot) ? [spot] : []),
      ...breakevens.filter((v) => Number.isFinite(v)),
    ];
    const minX = Math.min(...xCandidates);
    const maxX = Math.max(...xCandidates);
    const innerW = W - PAD.l - PAD.r;
    const innerH = H - PAD.t - PAD.b - (hasOi ? oiBand : 0);

    const x = (spotPrice: number) =>
      PAD.l + ((spotPrice - minX) / (maxX - minX || 1)) * innerW;
    const y = (pnl: number) =>
      PAD.t + innerH - ((pnl - minY) / (maxY - minY || 1)) * innerH;

    const toPath = (pts: PayoffPoint[]) =>
      pts
        .map(
          (p, idx) =>
            `${idx === 0 ? "M" : "L"} ${x(p.spot).toFixed(1)} ${y(p.pnl).toFixed(1)}`,
        )
        .join(" ");

    const zeroY = y(0);
    const maxOi = Math.max(
      1,
      ...(oiBars ?? []).flatMap((b) => [b.ceOi, b.peOi]),
    );

    // Area under expiry curve for profit (Sensibull-style color).
    const areaProfit = (() => {
      if (points.length < 2) return null;
      const parts: string[] = [];
      let started = false;
      for (let i = 0; i < points.length; i++) {
        const p = points[i];
        if (p.pnl >= 0) {
          if (!started) {
            parts.push(`M ${x(p.spot).toFixed(1)} ${zeroY.toFixed(1)}`);
            started = true;
          }
          parts.push(`L ${x(p.spot).toFixed(1)} ${y(p.pnl).toFixed(1)}`);
        } else if (started) {
          parts.push(`L ${x(p.spot).toFixed(1)} ${zeroY.toFixed(1)} Z`);
          started = false;
        }
      }
      if (started) {
        const last = points[points.length - 1];
        parts.push(`L ${x(last.spot).toFixed(1)} ${zeroY.toFixed(1)} Z`);
      }
      return parts.length ? parts.join(" ") : null;
    })();

    return {
      minX,
      maxX,
      minY,
      maxY,
      path: toPath(points),
      targetPath: targetPoints && targetPoints.length > 0 ? toPath(targetPoints) : null,
      scenarioPath:
        scenarioPoints && scenarioPoints.length > 0 ? toPath(scenarioPoints) : null,
      areaProfit,
      zeroY,
      x,
      y,
      innerH,
      maxOi,
    };
  }, [
    breakevens,
    H,
    hasOi,
    oiBand,
    oiBars,
    points,
    scenarioPoints,
    sdBands,
    spot,
    targetPoints,
  ]);

  if (!plot || points.length === 0) {
    return (
      <div
        className={cn(
          "flex items-center justify-center rounded-lg border border-line bg-canvas/40 text-sm text-slate-muted",
          fill ? "h-full min-h-[12rem]" : "py-10",
        )}
      >
        Pick a template to see expiry payoff.
      </div>
    );
  }

  const oiBase = PAD.t + plot.innerH + 4;

  const chart = (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      preserveAspectRatio="xMidYMid meet"
      className={fill ? "h-full w-full" : "h-auto w-full min-w-[20rem]"}
      role="img"
      aria-label="Strategy payoff chart"
    >
      {/* Profit shade */}
      {plot.areaProfit ? (
        <path d={plot.areaProfit} className="fill-teal/15" stroke="none" />
      ) : null}

      <line
        x1={PAD.l}
        y1={plot.zeroY}
        x2={W - PAD.r}
        y2={plot.zeroY}
        stroke="currentColor"
        className="text-line"
        strokeDasharray="4 4"
      />

      {sdBands
        ? (
            [
              ["sd2", sdBands.sd2, "text-violet-400/40"] as const,
              ["sd1", sdBands.sd1, "text-violet-500/60"] as const,
            ] as const
          ).map(([key, band, cls]) =>
            band.map((edge, idx) =>
              edge >= plot.minX && edge <= plot.maxX ? (
                <line
                  key={`${key}-${idx}`}
                  x1={plot.x(edge)}
                  y1={PAD.t}
                  x2={plot.x(edge)}
                  y2={PAD.t + plot.innerH}
                  stroke="currentColor"
                  className={cls}
                  strokeDasharray="3 3"
                  strokeWidth={1}
                />
              ) : null,
            ),
          )
        : null}

      {/* OI bars — prominent under plot (Sensibull-style dual CE/PE) */}
      {(oiBars ?? []).map((bar) => {
        if (bar.strike < plot.minX || bar.strike > plot.maxX) return null;
        const cx = plot.x(bar.strike);
        const ceH = (bar.ceOi / plot.maxOi) * (oiBand - 6);
        const peH = (bar.peOi / plot.maxOi) * (oiBand - 6);
        const barW = fill ? 5 : 3;
        return (
          <g key={bar.strike}>
            <rect
              x={cx - barW}
              y={oiBase + (oiBand - 6 - ceH)}
              width={barW}
              height={Math.max(0, ceH)}
              className="fill-rose/55"
            />
            <rect
              x={cx}
              y={oiBase + (oiBand - 6 - peH)}
              width={barW}
              height={Math.max(0, peH)}
              className="fill-teal/55"
            />
          </g>
        );
      })}

      {spot != null && spot >= plot.minX && spot <= plot.maxX ? (
        <line
          x1={plot.x(spot)}
          y1={PAD.t}
          x2={plot.x(spot)}
          y2={PAD.t + plot.innerH}
          stroke="currentColor"
          className="text-teal"
          strokeWidth={2}
        />
      ) : null}

      {breakevens.map((be) =>
        be >= plot.minX && be <= plot.maxX ? (
          <line
            key={be}
            x1={plot.x(be)}
            y1={PAD.t}
            x2={plot.x(be)}
            y2={PAD.t + plot.innerH}
            stroke="currentColor"
            className="text-amber-500"
            strokeDasharray="3 3"
            strokeWidth={1.25}
          />
        ) : null,
      )}

      {plot.scenarioPath ? (
        <path
          d={plot.scenarioPath}
          fill="none"
          stroke="currentColor"
          className="text-violet-600"
          strokeWidth={2}
          strokeDasharray="6 4"
        />
      ) : null}
      {plot.targetPath ? (
        <path
          d={plot.targetPath}
          fill="none"
          stroke="currentColor"
          className="text-sky-600"
          strokeWidth={2.5}
        />
      ) : null}
      {/* Expiry — strongest line (rose like Sensibull “on expiry”) */}
      <path
        d={plot.path}
        fill="none"
        stroke="currentColor"
        className="text-rose-600"
        strokeWidth={2.75}
      />

      <text x={PAD.l - 6} y={PAD.t + 8} textAnchor="end" className="fill-slate-muted text-[10px]">
        {plot.maxY.toFixed(0)}
      </text>
      <text
        x={PAD.l - 6}
        y={plot.zeroY + 3}
        textAnchor="end"
        className="fill-slate-muted text-[10px]"
      >
        0
      </text>
      <text
        x={PAD.l - 6}
        y={PAD.t + plot.innerH}
        textAnchor="end"
        className="fill-slate-muted text-[10px]"
      >
        {plot.minY.toFixed(0)}
      </text>
      <text x={PAD.l} y={H - 10} className="fill-slate-muted text-[10px]">
        {Math.round(plot.minX)}
      </text>
      <text
        x={W - PAD.r}
        y={H - 10}
        textAnchor="end"
        className="fill-slate-muted text-[10px]"
      >
        {Math.round(plot.maxX)}
      </text>
      {spot != null && spot >= plot.minX && spot <= plot.maxX ? (
        <text
          x={plot.x(spot)}
          y={PAD.t + plot.innerH + (hasOi ? oiBand - 8 : 14)}
          textAnchor="middle"
          className="fill-teal text-[10px] font-semibold"
        >
          Spot {Math.round(spot)}
        </text>
      ) : null}
    </svg>
  );

  const legend = (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-muted">
      <span className="inline-flex items-center gap-1.5">
        <span className="inline-block h-0.5 w-4 bg-rose-600" /> On expiry
      </span>
      {plot.targetPath ? (
        <span className="inline-flex items-center gap-1.5 text-sky-700">
          <span className="inline-block h-0.5 w-4 bg-sky-600" />{" "}
          {targetLabel ?? "On target"}
        </span>
      ) : null}
      {plot.scenarioPath ? (
        <span className="inline-flex items-center gap-1.5 text-violet-700">
          <span className="inline-block h-0.5 w-4 border-t-2 border-dashed border-violet-600" />{" "}
          {scenarioLabel ?? "IV scenario"}
        </span>
      ) : null}
      {spot != null ? (
        <span className="inline-flex items-center gap-1.5 text-teal">
          <span className="inline-block h-3 w-0.5 bg-teal" /> Spot
        </span>
      ) : null}
      {breakevens.length > 0 ? (
        <span className="inline-flex items-center gap-1.5 text-amber-700">
          <span className="inline-block h-3 w-0.5 border-l-2 border-dashed border-amber-500" />{" "}
          Breakeven
        </span>
      ) : null}
      {hasOi ? (
        <span className="inline-flex items-center gap-1.5">
          <span className="inline-block size-2.5 rounded-sm bg-rose/60" /> Call OI
          <span className="inline-block size-2.5 rounded-sm bg-teal/60" /> Put OI
        </span>
      ) : null}
    </div>
  );

  if (fill) {
    return (
      <div className="flex h-full min-h-0 flex-col overflow-hidden rounded-lg border border-line bg-canvas">
        <div className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-b border-line px-2.5 py-1.5">
          <p className="text-sm font-semibold text-ink">Payoff graph</p>
          {legend}
        </div>
        <div className="relative min-h-0 flex-1 bg-canvas/60">
          <div className="absolute inset-0">{chart}</div>
        </div>
        {footer ? (
          <div className="shrink-0 border-t border-line bg-raised/30 px-2.5 py-1.5">
            {footer}
          </div>
        ) : null}
      </div>
    );
  }

  return (
    <div className="overflow-x-auto rounded-lg border border-line bg-canvas/40 p-2">
      <div className="mb-2">{legend}</div>
      {chart}
      {footer ? <div className="mt-2 border-t border-line pt-2">{footer}</div> : null}
    </div>
  );
}
