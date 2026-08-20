"use client";

import { useMemo } from "react";

import type { OptionsStraddlePoint } from "@/lib/api/admin";

const W = 640;
const H = 220;
const PAD = { t: 12, r: 12, b: 28, l: 44 };

export function OptionsLabStraddleChart({
  points,
  atm,
}: {
  points: OptionsStraddlePoint[];
  atm: number | null;
}) {
  const plot = useMemo(() => {
    if (points.length === 0) return null;

    const vals = points.flatMap((p) => [p.combined, p.ce, p.pe]);
    const rawMin = Math.min(...vals);
    const rawMax = Math.max(...vals);
    const pad = rawMax === rawMin ? Math.max(rawMax * 0.05, 1) : 0;
    const minY = rawMin - pad;
    const maxY = rawMax + pad;
    const innerW = W - PAD.l - PAD.r;
    const innerH = H - PAD.t - PAD.b;

    const times = points.map((p) => p.t);
    const minT = Math.min(...times);
    const maxT = Math.max(...times);
    const spanT = maxT - minT;

    const x = (t: number) =>
      PAD.l + (spanT <= 0 ? innerW / 2 : ((t - minT) / spanT) * innerW);
    const y = (val: number) =>
      PAD.t + innerH - ((val - minY) / (maxY - minY || 1)) * innerH;

    const line = (key: "combined" | "ce" | "pe") =>
      points
        .map((p, idx) => {
          const val = p[key];
          return `${idx === 0 ? "M" : "L"} ${x(p.t).toFixed(1)} ${y(val).toFixed(1)}`;
        })
        .join(" ");

    const sessionMins =
      spanT > 0 ? Math.max(1, Math.round(spanT / 60)) : null;

    return { minY, maxY, line, last: points[points.length - 1], sessionMins };
  }, [points]);

  if (!plot || points.length === 0) {
    return (
      <p className="py-12 text-center text-sm text-slate-muted">
        Straddle history builds while Options Lab is open (ATM CE + PE premium).
      </p>
    );
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <p className="text-xs text-slate-muted">
          ATM straddle · {points.length} samples
          {plot.sessionMins != null ? ` · ~${plot.sessionMins} min span` : ""}
          {atm != null ? ` · strike ${atm}` : ""}
        </p>
        <div className="flex flex-wrap gap-3 text-xs tabular-nums">
          <span>
            Combined{" "}
            <strong className="text-ink">{plot.last.combined.toFixed(2)}</strong>
          </span>
          <span className="text-teal">CE {plot.last.ce.toFixed(2)}</span>
          <span className="text-rose">PE {plot.last.pe.toFixed(2)}</span>
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
          <path
            d={plot.line("ce")}
            fill="none"
            stroke="currentColor"
            className="text-teal/50"
            strokeWidth={1.5}
          />
          <path
            d={plot.line("pe")}
            fill="none"
            stroke="currentColor"
            className="text-rose/50"
            strokeWidth={1.5}
          />
          <path
            d={plot.line("combined")}
            fill="none"
            stroke="currentColor"
            className="text-ink"
            strokeWidth={2}
          />
          <text
            x={PAD.l - 6}
            y={PAD.t + 8}
            textAnchor="end"
            className="fill-slate-muted text-[9px]"
          >
            {plot.maxY.toFixed(0)}
          </text>
          <text
            x={PAD.l - 6}
            y={H - PAD.b}
            textAnchor="end"
            className="fill-slate-muted text-[9px]"
          >
            {plot.minY.toFixed(0)}
          </text>
        </svg>
        <div className="mt-2 flex flex-wrap gap-4 text-[10px] text-slate-muted">
          <span className="inline-flex items-center gap-1">
            <span className="inline-block h-0.5 w-4 bg-ink" /> Combined
          </span>
          <span className="inline-flex items-center gap-1">
            <span className="inline-block h-0.5 w-4 bg-teal/60" /> CE
          </span>
          <span className="inline-flex items-center gap-1">
            <span className="inline-block h-0.5 w-4 bg-rose/60" /> PE
          </span>
        </div>
      </div>
    </div>
  );
}
