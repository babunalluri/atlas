"use client";

import { useMemo } from "react";

import type { PayoffPoint } from "@/components/domains/options-lab-strategy";

const W = 640;
const H = 280;
const PAD = { t: 16, r: 16, b: 48, l: 48 };
const OI_H = 28;

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
}: {
  points: PayoffPoint[];
  /** Pre-expiry / target-date mark curve (optional). */
  targetPoints?: PayoffPoint[];
  /** IV-shocked scenario curve (optional). */
  scenarioPoints?: PayoffPoint[];
  spot: number | null;
  breakevens: number[];
  sdBands?: { sd1: [number, number]; sd2: [number, number] } | null;
  oiBars?: PayoffOiBar[];
  targetLabel?: string;
  scenarioLabel?: string;
}) {
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
    const bandSpots = sdBands
      ? [...sdBands.sd1, ...sdBands.sd2]
      : [];
    const xCandidates = [
      ...pointSpots,
      ...bandSpots,
      ...(spot != null && Number.isFinite(spot) ? [spot] : []),
      ...breakevens.filter((v) => Number.isFinite(v)),
    ];
    const minX = Math.min(...xCandidates);
    const maxX = Math.max(...xCandidates);
    const innerW = W - PAD.l - PAD.r;
    const innerH = H - PAD.t - PAD.b - (oiBars && oiBars.length > 0 ? OI_H : 0);

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

    return {
      minX,
      maxX,
      minY,
      maxY,
      path: toPath(points),
      targetPath: targetPoints && targetPoints.length > 0 ? toPath(targetPoints) : null,
      scenarioPath:
        scenarioPoints && scenarioPoints.length > 0 ? toPath(scenarioPoints) : null,
      zeroY,
      x,
      y,
      innerH,
      maxOi,
    };
  }, [breakevens, oiBars, points, scenarioPoints, sdBands, spot, targetPoints]);

  if (!plot || points.length === 0) {
    return (
      <p className="py-10 text-center text-sm text-slate-muted">
        Pick a template to see expiry payoff.
      </p>
    );
  }

  const oiBase = PAD.t + plot.innerH + 4;

  return (
    <div className="overflow-x-auto rounded-lg border border-line bg-canvas/40 p-2">
      <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full min-w-[20rem]">
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
                ["sd2", sdBands.sd2, "text-violet-400/35"] as const,
                ["sd1", sdBands.sd1, "text-violet-500/55"] as const,
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
        {spot != null && spot >= plot.minX && spot <= plot.maxX ? (
          <line
            x1={plot.x(spot)}
            y1={PAD.t}
            x2={plot.x(spot)}
            y2={PAD.t + plot.innerH}
            stroke="currentColor"
            className="text-teal/60"
            strokeWidth={1}
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
              className="text-amber-500/70"
              strokeDasharray="2 3"
              strokeWidth={1}
            />
          ) : null,
        )}
        {plot.scenarioPath ? (
          <path
            d={plot.scenarioPath}
            fill="none"
            stroke="currentColor"
            className="text-violet-600/80"
            strokeWidth={1.5}
            strokeDasharray="5 3"
          />
        ) : null}
        {plot.targetPath ? (
          <path
            d={plot.targetPath}
            fill="none"
            stroke="currentColor"
            className="text-sky-600"
            strokeWidth={1.75}
          />
        ) : null}
        <path d={plot.path} fill="none" stroke="currentColor" className="text-ink" strokeWidth={2} />
        {(oiBars ?? []).map((bar) => {
          if (bar.strike < plot.minX || bar.strike > plot.maxX) return null;
          const cx = plot.x(bar.strike);
          const ceH = (bar.ceOi / plot.maxOi) * (OI_H - 4);
          const peH = (bar.peOi / plot.maxOi) * (OI_H - 4);
          return (
            <g key={bar.strike}>
              <rect
                x={cx - 3}
                y={oiBase + (OI_H - 4 - ceH)}
                width={3}
                height={Math.max(0, ceH)}
                className="fill-teal/70"
              />
              <rect
                x={cx}
                y={oiBase + (OI_H - 4 - peH)}
                width={3}
                height={Math.max(0, peH)}
                className="fill-rose/70"
              />
            </g>
          );
        })}
        <text x={PAD.l - 4} y={PAD.t + 6} textAnchor="end" className="fill-slate-muted text-[9px]">
          {plot.maxY.toFixed(0)}
        </text>
        <text x={PAD.l - 4} y={plot.zeroY + 3} textAnchor="end" className="fill-slate-muted text-[9px]">
          0
        </text>
        <text x={PAD.l - 4} y={PAD.t + plot.innerH} textAnchor="end" className="fill-slate-muted text-[9px]">
          {plot.minY.toFixed(0)}
        </text>
        <text x={PAD.l} y={H - 8} className="fill-slate-muted text-[9px]">
          {Math.round(plot.minX)}
        </text>
        <text x={W - PAD.r} y={H - 8} textAnchor="end" className="fill-slate-muted text-[9px]">
          {Math.round(plot.maxX)}
        </text>
      </svg>
      <div className="mt-2 flex flex-wrap gap-3 text-[10px] text-slate-muted">
        <span className="inline-flex items-center gap-1">
          <span className="inline-block h-0.5 w-4 bg-ink" /> Expiry
        </span>
        {plot.targetPath ? (
          <span className="inline-flex items-center gap-1 text-sky-700">
            <span className="inline-block h-0.5 w-4 bg-sky-600" />{" "}
            {targetLabel ?? "Target date"}
          </span>
        ) : null}
        {plot.scenarioPath ? (
          <span className="inline-flex items-center gap-1 text-violet-700">
            <span className="inline-block h-0.5 w-4 border-t border-dashed border-violet-600" />{" "}
            {scenarioLabel ?? "IV scenario"}
          </span>
        ) : null}
        {spot != null ? (
          <span className="inline-flex items-center gap-1 text-teal">
            <span className="inline-block h-3 w-0.5 bg-teal/70" /> Spot
          </span>
        ) : null}
        {breakevens.length > 0 ? (
          <span className="inline-flex items-center gap-1 text-amber-700">
            <span className="inline-block h-3 w-0.5 border-l border-dashed border-amber-600" />{" "}
            Breakeven
          </span>
        ) : null}
        {sdBands ? (
          <span className="inline-flex items-center gap-1 text-violet-700">
            <span className="inline-block h-3 w-0.5 border-l border-dashed border-violet-400" /> ±1/2σ
          </span>
        ) : null}
        {oiBars && oiBars.length > 0 ? (
          <span className="inline-flex items-center gap-1">
            <span className="inline-block h-2 w-2 bg-teal/70" />
            <span className="inline-block h-2 w-2 bg-rose/70" /> OI
          </span>
        ) : null}
      </div>
    </div>
  );
}
