"use client";

import { useEffect, useMemo, useState } from "react";

import type { OptionsStraddlePoint } from "@/lib/api/admin";
import { cn } from "@/lib/utils";

const W = 640;
const H = 220;
const PAD = { t: 12, r: 12, b: 28, l: 44 };

/** Stroke + legend — design tokens only (no arbitrary palette). */
const SERIES_STROKE = [
  "text-ink",
  "text-teal",
  "text-rose",
  "text-amber-600",
  "text-slate-muted",
] as const;
const SERIES_SWATCH = [
  "bg-ink",
  "bg-teal",
  "bg-rose",
  "bg-amber-600",
  "bg-slate-muted",
] as const;

export function OptionsLabStraddleChart({
  points,
  atm,
  series,
  strikes,
}: {
  points: OptionsStraddlePoint[];
  atm: number | null;
  series?: Record<string, OptionsStraddlePoint[]>;
  strikes?: number[];
}) {
  const availableStrikes = useMemo(() => {
    if (strikes && strikes.length > 0) return strikes;
    if (series) {
      return Object.keys(series)
        .map(Number)
        .filter((n) => Number.isFinite(n))
        .sort((a, b) => a - b);
    }
    return atm != null ? [atm] : [];
  }, [atm, series, strikes]);

  const strikeLadderKey = useMemo(
    () => availableStrikes.join(","),
    [availableStrikes],
  );

  const [mode, setMode] = useState<"atm" | "multi">("atm");
  const [pinned, setPinned] = useState<number[]>([]);

  useEffect(() => {
    if (!strikeLadderKey) {
      setPinned([]);
      return;
    }
    const ladder = strikeLadderKey.split(",").map(Number);
    const center = atm != null && ladder.includes(atm) ? atm : ladder[0];
    const idx = ladder.indexOf(center);
    const lo = Math.max(0, idx - 1);
    const hi = Math.min(ladder.length, idx + 2);
    setPinned(ladder.slice(lo, hi));
  }, [atm, strikeLadderKey]);

  const plot = useMemo(() => {
    const multiSeries =
      mode === "multi" && series
        ? pinned
            .map((strike) => ({
              strike,
              points: series[String(strike)] ?? [],
            }))
            .filter((s) => s.points.length > 0)
        : [{ strike: atm, points }];

    const allPoints = multiSeries.flatMap((s) => s.points);
    if (allPoints.length === 0) return null;

    const vals = allPoints.flatMap((p) =>
      mode === "atm" ? [p.combined, p.ce, p.pe] : [p.combined],
    );
    const rawMin = Math.min(...vals);
    const rawMax = Math.max(...vals);
    const pad = rawMax === rawMin ? Math.max(rawMax * 0.05, 1) : 0;
    const minY = rawMin - pad;
    const maxY = rawMax + pad;
    const innerW = W - PAD.l - PAD.r;
    const innerH = H - PAD.t - PAD.b;

    const times = allPoints.map((p) => p.t);
    const minT = Math.min(...times);
    const maxT = Math.max(...times);
    const spanT = maxT - minT;

    const x = (t: number) =>
      PAD.l + (spanT <= 0 ? innerW / 2 : ((t - minT) / spanT) * innerW);
    const y = (val: number) =>
      PAD.t + innerH - ((val - minY) / (maxY - minY || 1)) * innerH;

    const lineFor = (pts: OptionsStraddlePoint[], key: "combined" | "ce" | "pe") =>
      pts
        .map((p, idx) => {
          const val = p[key];
          return `${idx === 0 ? "M" : "L"} ${x(p.t).toFixed(1)} ${y(val).toFixed(1)}`;
        })
        .join(" ");

    const sessionMins = spanT > 0 ? Math.max(1, Math.round(spanT / 60)) : null;
    const lastAtm = points.length ? points[points.length - 1] : null;

    return {
      minY,
      maxY,
      lineFor,
      multiSeries,
      lastAtm,
      sessionMins,
      sampleCount: mode === "atm" ? points.length : allPoints.length,
    };
  }, [atm, mode, pinned, points, series]);

  if (!plot) {
    return (
      <p className="py-12 text-center text-sm text-slate-muted">
        Straddle history builds while Options Lab is open (ATM CE + PE premium).
      </p>
    );
  }

  function togglePin(strike: number) {
    setPinned((prev) => {
      if (prev.includes(strike)) {
        return prev.length <= 1 ? prev : prev.filter((s) => s !== strike);
      }
      if (prev.length >= 5) return prev;
      return [...prev, strike].sort((a, b) => a - b);
    });
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-slate-muted">
          {mode === "atm" ? "ATM straddle" : "Multi-straddle"} · {plot.sampleCount} samples
          {plot.sessionMins != null ? ` · ~${plot.sessionMins} min span` : ""}
          {atm != null ? ` · ATM ${atm}` : ""}
        </p>
        <div className="inline-flex rounded-md border border-line bg-canvas/60 p-0.5 text-xs">
          {(
            [
              ["atm", "ATM"],
              ["multi", "Multi"],
            ] as const
          ).map(([id, label]) => (
            <button
              key={id}
              type="button"
              onClick={() => setMode(id)}
              className={cn(
                "rounded px-2 py-1 font-medium transition",
                mode === id ? "bg-raised text-ink shadow-sm" : "text-slate-muted",
              )}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {mode === "multi" ? (
        <div className="mb-2 flex flex-wrap gap-1">
          {availableStrikes.map((strike) => {
            const on = pinned.includes(strike);
            return (
              <button
                key={strike}
                type="button"
                onClick={() => togglePin(strike)}
                className={cn(
                  "rounded border px-1.5 py-0.5 text-xs tabular-nums",
                  on
                    ? "border-teal/50 bg-teal/15 font-semibold text-ink"
                    : "border-line text-slate-muted",
                  strike === atm && "ring-1 ring-teal/40",
                )}
              >
                {strike}
              </button>
            );
          })}
        </div>
      ) : (
        <div className="mb-3 flex flex-wrap gap-3 text-xs tabular-nums">
          {plot.lastAtm ? (
            <>
              <span>
                Combined{" "}
                <strong className="text-ink">{plot.lastAtm.combined.toFixed(2)}</strong>
              </span>
              <span className="text-teal">CE {plot.lastAtm.ce.toFixed(2)}</span>
              <span className="text-rose">PE {plot.lastAtm.pe.toFixed(2)}</span>
            </>
          ) : null}
        </div>
      )}

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
          {mode === "atm" ? (
            <>
              <path
                d={plot.lineFor(points, "ce")}
                fill="none"
                stroke="currentColor"
                className="text-teal/50"
                strokeWidth={1.5}
              />
              <path
                d={plot.lineFor(points, "pe")}
                fill="none"
                stroke="currentColor"
                className="text-rose/50"
                strokeWidth={1.5}
              />
              <path
                d={plot.lineFor(points, "combined")}
                fill="none"
                stroke="currentColor"
                className="text-ink"
                strokeWidth={2}
              />
            </>
          ) : (
            plot.multiSeries.map((s, idx) => (
              <path
                key={s.strike ?? idx}
                d={plot.lineFor(s.points, "combined")}
                fill="none"
                stroke="currentColor"
                className={SERIES_STROKE[idx % SERIES_STROKE.length]}
                strokeWidth={idx === 0 ? 2 : 1.5}
              />
            ))
          )}
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
          {mode === "atm" ? (
            <>
              <span className="inline-flex items-center gap-1">
                <span className="inline-block h-0.5 w-4 bg-ink" /> Combined
              </span>
              <span className="inline-flex items-center gap-1">
                <span className="inline-block h-0.5 w-4 bg-teal/60" /> CE
              </span>
              <span className="inline-flex items-center gap-1">
                <span className="inline-block h-0.5 w-4 bg-rose/60" /> PE
              </span>
            </>
          ) : (
            plot.multiSeries.map((s, idx) => (
              <span key={s.strike ?? idx} className="inline-flex items-center gap-1">
                <span
                  className={cn(
                    "inline-block h-0.5 w-4",
                    SERIES_SWATCH[idx % SERIES_SWATCH.length],
                  )}
                />{" "}
                {s.strike}
              </span>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
