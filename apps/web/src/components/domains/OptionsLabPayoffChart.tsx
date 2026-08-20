"use client";

import { useMemo } from "react";

import type { PayoffPoint } from "@/components/domains/options-lab-strategy";

const W = 640;
const H = 240;
const PAD = { t: 16, r: 16, b: 32, l: 48 };

export function OptionsLabPayoffChart({
  points,
  spot,
  breakevens,
}: {
  points: PayoffPoint[];
  spot: number | null;
  breakevens: number[];
}) {
  const plot = useMemo(() => {
    if (points.length === 0) return null;
    const pnls = points.map((p) => p.pnl);
    const rawMin = Math.min(...pnls, 0);
    const rawMax = Math.max(...pnls, 0);
    const padY = rawMax === rawMin ? Math.max(Math.abs(rawMax) * 0.1, 1) : 0;
    const minY = rawMin - padY;
    const maxY = rawMax + padY;
    const minX = points[0].spot;
    const maxX = points[points.length - 1].spot;
    const innerW = W - PAD.l - PAD.r;
    const innerH = H - PAD.t - PAD.b;

    const x = (spotPrice: number) =>
      PAD.l + ((spotPrice - minX) / (maxX - minX || 1)) * innerW;
    const y = (pnl: number) =>
      PAD.t + innerH - ((pnl - minY) / (maxY - minY || 1)) * innerH;

    const path = points
      .map((p, idx) => `${idx === 0 ? "M" : "L"} ${x(p.spot).toFixed(1)} ${y(p.pnl).toFixed(1)}`)
      .join(" ");

    const zeroY = y(0);

    return { minX, maxX, minY, maxY, path, zeroY, x, y };
  }, [points]);

  if (!plot || points.length === 0) {
    return (
      <p className="py-10 text-center text-sm text-slate-muted">
        Pick a template to see expiry payoff.
      </p>
    );
  }

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
        {spot != null && spot >= plot.minX && spot <= plot.maxX ? (
          <line
            x1={plot.x(spot)}
            y1={PAD.t}
            x2={plot.x(spot)}
            y2={H - PAD.b}
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
              y2={H - PAD.b}
              stroke="currentColor"
              className="text-amber-500/70"
              strokeDasharray="2 3"
              strokeWidth={1}
            />
          ) : null,
        )}
        <path d={plot.path} fill="none" stroke="currentColor" className="text-ink" strokeWidth={2} />
        <text x={PAD.l - 4} y={PAD.t + 6} textAnchor="end" className="fill-slate-muted text-[9px]">
          {plot.maxY.toFixed(0)}
        </text>
        <text x={PAD.l - 4} y={plot.zeroY + 3} textAnchor="end" className="fill-slate-muted text-[9px]">
          0
        </text>
        <text x={PAD.l - 4} y={H - PAD.b} textAnchor="end" className="fill-slate-muted text-[9px]">
          {plot.minY.toFixed(0)}
        </text>
        <text x={PAD.l} y={H - 8} className="fill-slate-muted text-[9px]">
          {Math.round(plot.minX)}
        </text>
        <text x={W - PAD.r} y={H - 8} textAnchor="end" className="fill-slate-muted text-[9px]">
          {Math.round(plot.maxX)}
        </text>
      </svg>
      <div className="mt-2 flex flex-wrap gap-4 text-[10px] text-slate-muted">
        <span className="inline-flex items-center gap-1">
          <span className="inline-block h-0.5 w-4 bg-ink" /> Payoff @ expiry
        </span>
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
      </div>
    </div>
  );
}
