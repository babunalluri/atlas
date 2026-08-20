"use client";

import { useMemo } from "react";

import type { OptionsIvPoint } from "@/lib/api/admin";

const W = 640;
const H = 240;
const PAD = { t: 16, r: 16, b: 36, l: 48 };

export function OptionsLabIvChart({
  points,
  atmIv,
  ivp,
  sampleDays,
}: {
  points: OptionsIvPoint[];
  atmIv: number | null;
  ivp: number | null;
  sampleDays?: number;
}) {
  const plot = useMemo(() => {
    if (points.length === 0) return null;
    const vals = points.map((p) => p.iv);
    const rawMin = Math.min(...vals);
    const rawMax = Math.max(...vals);
    const pad = rawMax === rawMin ? Math.max(rawMax * 0.08, 0.5) : 0;
    const minY = rawMin - pad;
    const maxY = rawMax + pad;
    const innerW = W - PAD.l - PAD.r;
    const innerH = H - PAD.t - PAD.b;

    const x = (idx: number) =>
      PAD.l + (points.length <= 1 ? innerW / 2 : (idx / (points.length - 1)) * innerW);
    const y = (iv: number) =>
      PAD.t + innerH - ((iv - minY) / (maxY - minY || 1)) * innerH;

    const path = points
      .map((p, idx) => `${idx === 0 ? "M" : "L"} ${x(idx).toFixed(1)} ${y(p.iv).toFixed(1)}`)
      .join(" ");

    return { minY, maxY, path, first: points[0], last: points[points.length - 1], x, y };
  }, [points]);

  if (!plot || points.length === 0) {
    return (
      <p className="py-12 text-center text-sm text-slate-muted">
        ATM IV history builds from daily samples while Options Lab runs.
      </p>
    );
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <p className="text-xs text-slate-muted">
          ATM IV history · {sampleDays ?? points.length} samples
        </p>
        <div className="flex flex-wrap gap-3 text-xs tabular-nums">
          <span>
            ATM IV <strong className="text-ink">{atmIv != null ? atmIv.toFixed(1) : "—"}</strong>
          </span>
          <span>
            IVP{" "}
            <strong className="text-ink">{ivp != null ? `${ivp.toFixed(0)}` : "—"}</strong>
          </span>
          <span className="text-slate-muted">
            Latest {plot.last.iv.toFixed(1)} · {plot.last.day}
          </span>
        </div>
      </div>

      <div className="overflow-x-auto rounded-lg border border-line bg-canvas/40 p-2">
        <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full min-w-[20rem]">
          <line
            x1={PAD.l}
            y1={H - PAD.b}
            x2={W - PAD.r}
            y2={H - PAD.b}
            stroke="currentColor"
            className="text-line"
            strokeWidth={1}
          />
          <line
            x1={PAD.l}
            y1={PAD.t}
            x2={PAD.l}
            y2={H - PAD.b}
            stroke="currentColor"
            className="text-line"
            strokeWidth={1}
          />
          {atmIv != null ? (
            <line
              x1={PAD.l}
              y1={plot.y(atmIv)}
              x2={W - PAD.r}
              y2={plot.y(atmIv)}
              stroke="currentColor"
              className="text-teal/50"
              strokeDasharray="4 4"
              strokeWidth={1}
            />
          ) : null}
          <path d={plot.path} fill="none" stroke="currentColor" className="text-ink" strokeWidth={2} />
          <text x={PAD.l - 6} y={PAD.t + 8} textAnchor="end" className="fill-slate-muted text-[9px]">
            {plot.maxY.toFixed(1)}
          </text>
          <text x={PAD.l - 6} y={H - PAD.b} textAnchor="end" className="fill-slate-muted text-[9px]">
            {plot.minY.toFixed(1)}
          </text>
          <text x={PAD.l} y={H - 10} className="fill-slate-muted text-[9px]">
            {plot.first.day.slice(5)}
          </text>
          <text x={W - PAD.r} y={H - 10} textAnchor="end" className="fill-slate-muted text-[9px]">
            {plot.last.day.slice(5)}
          </text>
        </svg>
        <p className="mt-2 text-[10px] text-slate-muted">
          IVP = share of stored daily ATM IV samples below today&apos;s IV (needs ≥5 days).
        </p>
      </div>
    </div>
  );
}
