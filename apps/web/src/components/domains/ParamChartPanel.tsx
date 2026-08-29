"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Button } from "@/components/ui/Button";
import {
  ChevronDownIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  RefreshIcon,
} from "@/components/ui/icons";
import {
  getParamChartConfig,
  getParamChartMonth,
  patchParamChartConfig,
  type ParamChartAdminConfig,
  type ParamChartDay,
  type ParamChartMetricValue,
  type ParamChartMonthSnapshot,
  type ParamChartSharedMetric,
  type SignalUnderlyingPreset,
} from "@/lib/api/admin";
import { streamParamChart } from "@/lib/api/param-chart-stream";
import {
  deskInstrumentIdentityKey,
  publishDeskInstrument,
  readDeskInstrument,
  subscribeDeskInstrument,
  type DeskInstrumentSelection,
} from "@/components/domains/desk-instrument";
import { suggestFutSymbol } from "@/components/domains/signal-setup-options";
import { useAgentOsToken } from "@/lib/auth/token";
import { TradingViewButton } from "@/components/domains/CommonInstrumentSetupBar";
import { cn } from "@/lib/utils";

import {
  MAX_OVERLAYS,
  partitionOverlays,
  toggleOverlay,
  type ChartSeriesId,
} from "./paramChartSeries";
import {
  barAxisLabel,
  shouldShowAxisLabel,
} from "./paramChartAxis";
import {
  isNoTradeBand,
  isSessionCloseBar,
  isSessionOpenBar,
} from "./paramChartSession";
import { mergeMonthStreamPatch } from "./paramChartStreamPatch";
import {
  clampView,
  ensureIndexVisible,
  panView,
  zoomView,
} from "./paramChartViewport";

const MONTH_LABELS = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
];

/** A month pack the server is still assembling — its rows are not final yet. */
function packIsBuilding(snap: ParamChartMonthSnapshot): boolean {
  return (
    Boolean(snap.building) ||
    (snap.kite?.errors || []).some((e) =>
      /pack_building|pack_rebuild_in_progress/i.test(String(e)),
    )
  );
}

const INTERVAL_OPTIONS = [
  { id: "1m", label: "1m" },
  { id: "5m", label: "5m" },
  { id: "15m", label: "15m" },
  { id: "1H", label: "1H" },
  { id: "1D", label: "1D" },
  { id: "1W", label: "1W" },
  { id: "1M", label: "1M" },
] as const;

function fmt(n: number | null | undefined, digits = 2): string {
  if (n == null || Number.isNaN(n)) return "—";
  return n.toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function numericMetric(value: unknown): number | null {
  if (value == null || value === "") return null;
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "boolean") return value ? 1 : 0;
  if (typeof value === "string") {
    const cleaned = value.replace(/[%₹,\s]/g, "").trim();
    if (!cleaned) return null;
    const n = Number(cleaned);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

const OVERLAY_COLORS = [
  "#0ea5e9", // sky
  "#8b5cf6", // violet
  "#10b981", // emerald
  "#f59e0b", // amber
  "#ec4899", // pink
  "#14b8a6", // teal
] as const;

const BUILTIN_SERIES: Array<{ id: ChartSeriesId; label: string; group: string }> = [
  { id: "close", label: "Underlying close", group: "Price & premium" },
  { id: "total", label: "Total premium (CE+PE)", group: "Price & premium" },
  { id: "ce", label: "CE premium", group: "Price & premium" },
  { id: "pe", label: "PE premium", group: "Price & premium" },
];

function seriesValue(
  day: ParamChartDay | null | undefined,
  seriesId: ChartSeriesId,
  opts?: {
    metricsByDay?: Record<string, Record<string, ParamChartMetricValue>>;
    liveMetrics?: Record<string, ParamChartMetricValue>;
    today?: string | null;
  },
): number | null {
  if (!day) return null;
  if (seriesId === "close") return day.close;
  if (seriesId === "total") return day.total;
  if (seriesId === "ce") return day.ce;
  if (seriesId === "pe") return day.pe;
  if (seriesId.startsWith("metric:")) {
    const mid = seriesId.slice("metric:".length);
    const dayKey = String(day.date || "").slice(0, 10);
    const fromBar = day.metrics?.[mid];
    const fromMap = opts?.metricsByDay?.[dayKey]?.[mid];
    const fromLive =
      opts?.today && dayKey === opts.today
        ? opts.liveMetrics?.[mid]
        : undefined;
    return numericMetric(
      fromLive?.value ?? fromBar?.value ?? fromMap?.value,
    );
  }
  return null;
}

function seriesLabel(
  id: ChartSeriesId,
  sharedMetrics: ParamChartSharedMetric[],
): string {
  if (id === "close") return "Underlying close";
  if (id === "total") return "Total premium (CE+PE)";
  if (id === "ce") return "CE premium";
  if (id === "pe") return "PE premium";
  if (id.startsWith("metric:")) {
    const mid = id.slice("metric:".length);
    return sharedMetrics.find((m) => m.id === mid)?.label ?? mid;
  }
  return id;
}

type SeriesPoint = { day: ParamChartDay; i: number; v: number };

function isFutureCalendarMonth(
  year: number | null | undefined,
  month: number | null | undefined,
  todayIso?: string | null,
): boolean {
  if (year == null || month == null) return false;
  const raw = String(todayIso || "").slice(0, 10);
  const now = /^\d{4}-\d{2}-\d{2}$/.test(raw)
    ? raw
    : new Date(Date.now() + 5.5 * 3600_000).toISOString().slice(0, 10);
  const ty = Number(now.slice(0, 4));
  const tm = Number(now.slice(5, 7));
  return year > ty || (year === ty && month > tm);
}

function DualAxisMonthChart({
  days,
  selectedDate,
  onSelect,
  primaryId,
  overlayIds,
  overlayLabels,
  interval = "1D",
  kiteErrors,
  kiteLiveError,
  chartStyle = "candle",
  histPreference = "auto",
  showSpotPct = false,
  metricsByDay,
  liveMetrics,
  today = null,
  year,
  month,
}: {
  days: ParamChartDay[];
  selectedDate: string | null;
  onSelect: (date: string) => void;
  primaryId: ChartSeriesId;
  overlayIds: ChartSeriesId[];
  overlayLabels: string[];
  interval?: string;
  kiteErrors?: string[];
  kiteLiveError?: string | null;
  /** Candle when OHLC present; otherwise line. */
  chartStyle?: "candle" | "line";
  histPreference?: "auto" | "volume" | "chg";
  /** Plot spot as % from first close in the visible window. */
  showSpotPct?: boolean;
  metricsByDay?: Record<string, Record<string, ParamChartMetricValue>>;
  liveMetrics?: Record<string, ParamChartMetricValue>;
  today?: string | null;
  year?: number | null;
  month?: number | null;
}) {
  const metricOpts = {
    metricsByDay,
    liveMetrics,
    today,
  };
  const sv = (day: ParamChartDay | null | undefined, id: ChartSeriesId) =>
    seriesValue(day, id, metricOpts);
  const [hoverIdx, setHoverIdx] = useState<number | null>(null);
  const [viewStart, setViewStart] = useState(0);
  /** 0 = fit all bars. */
  const [viewCount, setViewCount] = useState(0);
  const [dragging, setDragging] = useState(false);
  const plotRef = useRef<HTMLDivElement | null>(null);
  const plotBoxRef = useRef<HTMLDivElement | null>(null);
  const [plotBox, setPlotBox] = useState({ w: 980, h: 360 });
  const dragRef = useRef<{
    originX: number;
    originStart: number;
    moved: boolean;
  } | null>(null);
  const viewRef = useRef({ start: 0, count: 0, n: 0 });
  const layoutRef = useRef({ w: 760, left: 44, right: 8, innerW: 708 });

  const seriesKey = `${interval}:${days[0]?.date ?? ""}:${days.length}:${days[days.length - 1]?.date ?? ""}`;
  useEffect(() => {
    const n = days.length;
    // Dense intraday months — open on the latest session window.
    const win =
      interval === "1m" ? 240 : interval === "5m" ? 120 : interval === "15m" ? 80 : 0;
    if (win > 0 && n > win) {
      setViewStart(n - win);
      setViewCount(win);
    } else {
      setViewStart(0);
      setViewCount(0);
    }
    setHoverIdx(null);
  }, [seriesKey, days.length, interval]);

  // Non-passive wheel — zoom toward cursor (attached whenever the plot mounts).
  useEffect(() => {
    const el = plotRef.current;
    if (!el) return;
    const onWheel = (ev: WheelEvent) => {
      ev.preventDefault();
      const { start, count, n: vn } = viewRef.current;
      if (vn <= 0) return;
      const layout = layoutRef.current;
      const rect = el.getBoundingClientRect();
      const xSvg =
        ((ev.clientX - rect.left) / Math.max(rect.width, 1)) * layout.w;
      let anchor = start + Math.floor(count / 2);
      if (xSvg >= layout.left && xSvg <= layout.w - layout.right) {
        anchor =
          Math.floor(((xSvg - layout.left) / layout.innerW) * count) + start;
      }
      const factor = ev.deltaY > 0 ? 1.18 : 1 / 1.18;
      const next = zoomView(start, count, vn, anchor, factor, 5);
      setViewStart(next.start);
      setViewCount(next.count >= vn ? 0 : next.count);
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [seriesKey]);

  useEffect(() => {
    const el = plotBoxRef.current;
    if (!el) return;
    const apply = (width: number, height: number) => {
      const nextW = Math.max(280, Math.round(width));
      const nextH = Math.max(180, Math.round(height));
      setPlotBox((prev) =>
        prev.w === nextW && prev.h === nextH ? prev : { w: nextW, h: nextH },
      );
    };
    apply(el.clientWidth, el.clientHeight);
    const ro = new ResizeObserver((entries) => {
      const cr = entries[0]?.contentRect;
      if (!cr) return;
      apply(cr.width, cr.height);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [seriesKey, days.length]);

  const candidates: ChartSeriesId[] = [
    primaryId,
    "close",
    "total",
    "ce",
    "pe",
    ...overlayIds,
  ];
  let effectivePrimary: ChartSeriesId = primaryId;
  let primaryPts: SeriesPoint[] = [];
  for (const id of candidates) {
    const pts = days
      .map((d, i) => ({ day: d, i, v: sv(d, id) }))
      .filter((p): p is SeriesPoint => p.day != null && p.v != null);
    if (pts.length) {
      effectivePrimary = id;
      primaryPts = pts;
      break;
    }
  }

  if (!days.length || !primaryPts.length) {
    const future = isFutureCalendarMonth(year, month, today);
    const period =
      year && month
        ? `${MONTH_LABELS[month - 1]} ${year}`
        : "this month";
    const tokenish = (kiteErrors || []).filter((e) =>
      /ce_token_missing|pe_token_missing|ce_symbol_empty|pe_symbol_empty/i.test(
        String(e),
      ),
    );
    const otherErrors = [
      ...(kiteErrors || []).filter((e) => !tokenish.includes(e)),
      ...(kiteLiveError ? [kiteLiveError] : []),
    ].filter(Boolean);
    const detail = future ? "" : otherErrors.join(" · ");
    return (
      <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-2 rounded-md border border-dashed border-line px-4 text-center text-sm text-slate-muted">
        <p className="font-medium text-ink">
          {future ? `${period} hasn’t started` : "Chart has no series yet"}
        </p>
        <p>
          {future
            ? `No trading days yet in ${period}. Switch to the current month to see candles.`
            : "No OHLC bars for this month/interval yet (dump miss or empty Kite hist). Past months are stored once and reused."}
        </p>
        {detail ? (
          <p className="max-w-xl font-mono text-[11px] text-rose-600">{detail}</p>
        ) : future ? null : (
          <p className="text-xs">
            Pick a month with trading days, then wait for the first dump (or
            click Refresh). After that we read from storage.
          </p>
        )}
      </div>
    );
  }

  const nAll = days.length;
  const rawCount = viewCount <= 0 ? nAll : viewCount;
  const { start: winStart, count: winCount } = clampView(
    viewStart,
    rawCount,
    nAll,
  );
  const winEnd = winStart + winCount;
  viewRef.current = { start: winStart, count: winCount, n: nAll };
  const zoomed = winCount < nAll;

  const { price: priceOverlayIds, metrics: metricOverlayIds } =
    partitionOverlays(overlayIds);

  const buildOverlaySeries = (ids: ChartSeriesId[]) =>
    ids
      .map((id) => {
        if (id === effectivePrimary) return null;
        const colorIdx = overlayIds.indexOf(id);
        const pts = days
          .map((d, i) => ({ day: d, i, v: sv(d, id) }))
          .filter(
            (p): p is SeriesPoint =>
              p.v != null && p.i >= winStart && p.i < winEnd,
          );
        return {
          id,
          label: overlayLabels[colorIdx] || id,
          color: OVERLAY_COLORS[Math.max(0, colorIdx) % OVERLAY_COLORS.length],
          pts,
        };
      })
      .filter((s): s is NonNullable<typeof s> => s != null);

  const priceOverlaySeries = buildOverlaySeries(priceOverlayIds);
  const metricOverlaySeries = buildOverlaySeries(metricOverlayIds);

  const usePct = showSpotPct && effectivePrimary === "close";
  let pctBase: number | null = null;
  if (usePct) {
    for (let i = winStart; i < winEnd; i++) {
      const c = days[i]?.close;
      if (c != null && Number.isFinite(Number(c)) && Number(c) !== 0) {
        pctBase = Number(c);
        break;
      }
    }
  }
  const toPct = (v: number) =>
    pctBase != null ? ((v - pctBase) / pctBase) * 100 : v;

  const visiblePrimaryPts = primaryPts
    .filter((p) => p.i >= winStart && p.i < winEnd)
    .map((p) => (usePct ? { ...p, v: toPct(p.v) } : p));
  const primaryVals = visiblePrimaryPts.map((p) => p.v);
  // Include high/low so candle wicks fit the price scale (visible window only).
  if (effectivePrimary === "close") {
    for (let i = winStart; i < winEnd; i++) {
      const d = days[i];
      if (!d) continue;
      if (d.high != null && Number.isFinite(Number(d.high))) {
        primaryVals.push(usePct ? toPct(Number(d.high)) : Number(d.high));
      }
      if (d.low != null && Number.isFinite(Number(d.low))) {
        primaryVals.push(usePct ? toPct(Number(d.low)) : Number(d.low));
      }
    }
  }
  const pMin = primaryVals.length ? Math.min(...primaryVals) : 0;
  const pMax = primaryVals.length ? Math.max(...primaryVals) : 1;
  const pPad = (pMax - pMin) * 0.08 || Math.abs(pMax) * 0.02 || 1;
  const py0 = pMin - pPad;
  const py1 = pMax + pPad;

  const priceOverlayVals = priceOverlaySeries.flatMap((s) => s.pts.map((p) => p.v));
  let oy0 = 0;
  let oy1 = 1;
  if (priceOverlayVals.length) {
    const oMin = Math.min(...priceOverlayVals);
    const oMax = Math.max(...priceOverlayVals);
    const oPad = (oMax - oMin) * 0.08 || Math.abs(oMax) * 0.02 || 1;
    oy0 = oMin - oPad;
    oy1 = oMax + oPad;
  }

  const metricVals = metricOverlaySeries.flatMap((s) => s.pts.map((p) => p.v));
  let my0 = 0;
  let my1 = 1;
  if (metricVals.length) {
    const mMin = Math.min(...metricVals);
    const mMax = Math.max(...metricVals);
    const mPad = (mMax - mMin) * 0.08 || Math.abs(mMax) * 0.02 || 1;
    my0 = mMin - mPad;
    my1 = mMax + mPad;
  }

  const hasPriceOverlays = priceOverlaySeries.some((s) => s.pts.length > 0);
  const showMetricPane = metricOverlaySeries.some((s) => s.pts.length > 0);
  const w = plotBox.w;
  const h = plotBox.h;
  const axisH = 16;
  const gap = 6;
  const metricGap = showMetricPane ? 6 : 0;
  const usable = Math.max(1, h - gap - metricGap - axisH);
  const histH = Math.round(usable * (showMetricPane ? 0.16 : 0.18));
  const metricH = showMetricPane ? Math.round(usable * 0.22) : 0;
  const priceH = Math.max(1, usable - histH - metricH);
  const left = 42;
  const right = hasPriceOverlays ? 42 : 7;
  const top = 10;
  const priceBottomPad = 6;
  const innerW = w - left - right;
  const innerH = priceH - top - priceBottomPad;
  const metricTop = priceH + gap;
  const metricInnerTop = metricTop + 12;
  const metricInnerH = Math.max(24, metricH - 18);
  const n = winCount;
  layoutRef.current = { w, left, right, innerW };
  const xAt = (i: number) => left + (innerW * (i - winStart + 0.5)) / n;
  const yPrimary = (v: number) =>
    top + innerH * (1 - (v - py0) / (py1 - py0 || 1));
  const yOverlay = (v: number) =>
    top + innerH * (1 - (v - oy0) / (oy1 - oy0 || 1));
  const yMetric = (v: number) =>
    metricInnerTop +
    metricInnerH * (1 - (v - my0) / (my1 - my0 || 1));

  const primaryPath = visiblePrimaryPts
    .map((p, idx) => `${idx === 0 ? "M" : "L"} ${xAt(p.i)} ${yPrimary(p.v)}`)
    .join(" ");

  const selectedIdx = selectedDate
    ? days.findIndex((d) => d.date === selectedDate)
    : -1;
  const focusIdx = hoverIdx != null ? hoverIdx : selectedIdx;
  const focusDay =
    focusIdx >= winStart && focusIdx < winEnd ? days[focusIdx] : null;
  const leftPct = (left / w) * 100;
  const rightPct = (right / w) * 100;

  // Sparse X ticks for SVG + scrubber (avoid the digit-soup band).
  const visibleDays = days.slice(winStart, winEnd);
  const xTickOffsets = visibleDays
    .map((_, offset) => offset)
    .filter((offset) =>
      shouldShowAxisLabel(
        offset,
        n,
        interval,
        winStart + offset === focusIdx,
      ),
    );

  function idxFromClientX(clientX: number): number | null {
    const el = plotRef.current;
    if (!el || !days.length) return null;
    const rect = el.getBoundingClientRect();
    const xSvg = ((clientX - rect.left) / Math.max(rect.width, 1)) * w;
    if (xSvg < left || xSvg > w - right) return null;
    const i = Math.floor(((xSvg - left) / innerW) * n) + winStart;
    return Math.max(winStart, Math.min(winEnd - 1, i));
  }

  function applyView(next: { start: number; count: number }) {
    setViewStart(next.start);
    setViewCount(next.count >= nAll ? 0 : next.count);
  }

  function onPointerDown(e: React.PointerEvent<HTMLDivElement>) {
    if (e.button !== 0) return;
    dragRef.current = {
      originX: e.clientX,
      originStart: winStart,
      moved: false,
    };
    setDragging(true);
    e.currentTarget.setPointerCapture(e.pointerId);
  }

  function onPointerMove(e: React.PointerEvent<HTMLDivElement>) {
    const drag = dragRef.current;
    if (!drag) {
      const i = idxFromClientX(e.clientX);
      setHoverIdx(i);
      return;
    }
    const el = plotRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const dx = e.clientX - drag.originX;
    if (Math.abs(dx) > 3) drag.moved = true;
    const { count: vc, n: vn } = viewRef.current;
    const deltaBars = -(dx / Math.max(rect.width * (innerW / w), 1)) * vc;
    const next = panView(drag.originStart, vc, vn, deltaBars);
    applyView(next);
    const i = idxFromClientX(e.clientX);
    setHoverIdx(i);
  }

  function onPointerUp(e: React.PointerEvent<HTMLDivElement>) {
    const drag = dragRef.current;
    dragRef.current = null;
    setDragging(false);
    try {
      e.currentTarget.releasePointerCapture(e.pointerId);
    } catch {
      /* already released */
    }
    if (drag && !drag.moved) {
      const i = idxFromClientX(e.clientX);
      if (i != null) onSelect(days[i].date);
    }
  }

  function onDoubleClick() {
    const win =
      interval === "1m" ? 240 : interval === "5m" ? 120 : interval === "15m" ? 80 : 0;
    if (win > 0 && nAll > win) {
      applyView({ start: nAll - win, count: win });
      return;
    }
    applyView({ start: 0, count: nAll });
  }

  const tooltipRows: Array<{ label: string; value: string; color: string }> =
    [];
  if (focusDay) {
    const rawPv = sv(focusDay, effectivePrimary);
    const pv =
      usePct && rawPv != null && pctBase != null ? toPct(rawPv) : rawPv;
    tooltipRows.push({
      label:
        effectivePrimary === "close"
          ? usePct
            ? "Close %"
            : "Close"
          : seriesLabel(effectivePrimary, []),
      value: usePct ? `${fmt(pv)}%` : fmt(pv),
      color: "#f43f5e",
    });
    if (effectivePrimary === "close" && !usePct) {
      if (focusDay.open != null) {
        tooltipRows.push({ label: "Open", value: fmt(focusDay.open), color: "#94a3b8" });
      }
      if (focusDay.high != null) {
        tooltipRows.push({ label: "High", value: fmt(focusDay.high), color: "#26a69a" });
      }
      if (focusDay.low != null) {
        tooltipRows.push({ label: "Low", value: fmt(focusDay.low), color: "#ef5350" });
      }
    }
    for (const s of [...priceOverlaySeries, ...metricOverlaySeries]) {
      tooltipRows.push({
        label: s.label,
        value: fmt(sv(focusDay, s.id), 2),
        color: s.color,
      });
    }
    if (focusDay.chg != null) {
      tooltipRows.push({
        label: "Δ close",
        value: fmt(focusDay.chg),
        color: focusDay.chg >= 0 ? "#26a69a" : "#ef5350",
      });
    }
    if (focusDay.volume != null && Number(focusDay.volume) > 0) {
      tooltipRows.push({
        label: "Volume",
        value: fmt(focusDay.volume, 0),
        color: "#94a3b8",
      });
    }
  }

  const tipLeftPct =
    focusIdx >= winStart && focusIdx < winEnd
      ? leftPct +
        ((focusIdx - winStart + 0.5) * (100 - leftPct - rightPct)) / n
      : 50;
  const tipOnLeft = tipLeftPct > 62;

  const hasVolume = days.some(
    (d) => d.volume != null && Number(d.volume) > 0,
  );
  const histMode: "volume" | "chg" =
    histPreference === "volume"
      ? "volume"
      : histPreference === "chg"
        ? "chg"
        : hasVolume
          ? "volume"
          : "chg";
  const hasOhlc = days.some(
    (d) =>
      d.open != null && d.high != null && d.low != null && d.close != null,
  );
  const useCandles =
    chartStyle === "candle" &&
    effectivePrimary === "close" &&
    hasOhlc &&
    !usePct;
  const histVals = days.map((d) => {
    if (histMode === "volume") {
      const v = d.volume;
      return v != null && Number.isFinite(Number(v)) ? Number(v) : null;
    }
    const c = d.chg;
    return c != null && Number.isFinite(Number(c)) ? Number(c) : null;
  });
  const histAbsMax = Math.max(
    1e-9,
    ...histVals.map((v) => (v == null ? 0 : Math.abs(v))),
  );
  const histTop = priceH + gap + metricH + metricGap;
  const histPadTop = 4;
  const histPadBottom = 4;
  const histInner = histH - histPadTop - histPadBottom;
  const histZero =
    histMode === "chg"
      ? histTop + histPadTop + histInner / 2
      : histTop + histPadTop + histInner;
  const barSlot = innerW / n;
  const barW = Math.max(2, Math.min(8, barSlot * 0.48));

  const fmtAxis = (v: number) => {
    const a = Math.abs(v);
    if (a >= 1000) return String(Math.round(v));
    if (a >= 100) return (Math.round(v * 10) / 10).toString();
    return (Math.round(v * 100) / 100).toString();
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-1">
      <div className="flex shrink-0 flex-wrap items-center gap-x-4 gap-y-1 px-0.5 text-[11px] text-slate-muted">
        <span className="inline-flex items-center gap-1.5">
          <span
            className={cn(
              "inline-block rounded-sm",
              useCandles ? "h-2.5 w-2 bg-emerald-500/90" : "h-0.5 w-3 rounded-full bg-rose-500",
            )}
          />
          <span className="text-ink/80">
            {effectivePrimary === "close"
              ? usePct
                ? "Spot %"
                : useCandles
                  ? "OHLC"
                  : "Close"
              : seriesLabel(effectivePrimary, [])}
          </span>
        </span>
        {priceOverlaySeries.map((s) => (
          <span key={s.id} className="inline-flex items-center gap-1.5">
            <span
              className="inline-block h-0.5 w-3 rounded-full"
              style={{ backgroundColor: s.color }}
            />
            <span className="text-ink/80">{s.label}</span>
            {s.pts.length === 0 ? (
              <span className="opacity-60">(empty)</span>
            ) : null}
          </span>
        ))}
        {metricOverlaySeries.map((s) => (
          <span key={s.id} className="inline-flex items-center gap-1.5">
            <span
              className="inline-block h-0.5 w-3 rounded-full"
              style={{ backgroundColor: s.color }}
            />
            <span className="text-ink/80">{s.label}</span>
            <span className="opacity-50">·param</span>
            {s.pts.length === 0 ? (
              <span className="opacity-60">(empty)</span>
            ) : null}
          </span>
        ))}
        <span className="ml-auto font-mono text-[10px] tabular-nums opacity-70">
          {showMetricPane ? "Price · Params · " : "Price · "}
          {histMode === "volume" ? "Volume" : "Day Δ"}
          {" · "}
          {zoomed
            ? `bars ${winStart + 1}–${winEnd}/${nAll}`
            : `${nAll} bars`}
          {" · drag pan · scroll zoom · dbl-click reset"}
        </span>
      </div>

      <div
        ref={plotRef}
        className={cn(
          "relative flex min-h-0 flex-1 flex-col overflow-hidden rounded-md border border-line/70 bg-[#0b1220]/[0.03] dark:bg-black/20",
          dragging ? "cursor-grabbing" : "cursor-grab",
        )}
        style={{ touchAction: "none" }}
        tabIndex={0}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        onDoubleClick={onDoubleClick}
        onMouseLeave={() => {
          if (!dragRef.current) setHoverIdx(null);
        }}
        onKeyDown={(e) => {
          if (!days.length) return;
          const cur =
            hoverIdx != null
              ? hoverIdx
              : selectedDate
                ? days.findIndex((d) => d.date === selectedDate)
                : days.length - 1;
          if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
            e.preventDefault();
            const next =
              e.key === "ArrowLeft"
                ? Math.max(0, (cur < 0 ? 0 : cur) - 1)
                : Math.min(days.length - 1, (cur < 0 ? 0 : cur) + 1);
            setHoverIdx(next);
            onSelect(days[next].date);
            applyView(
              ensureIndexVisible(winStart, winCount, nAll, next),
            );
          }
          if (e.key === "Escape" && zoomed) {
            e.preventDefault();
            const win =
              interval === "1m"
                ? 240
                : interval === "5m"
                  ? 120
                  : interval === "15m"
                    ? 80
                    : 0;
            if (win > 0 && nAll > win) {
              applyView({ start: nAll - win, count: win });
            } else {
              applyView({ start: 0, count: nAll });
            }
          }
        }}
      >
        <div ref={plotBoxRef} className="relative min-h-0 flex-1">
        <svg
          viewBox={`0 0 ${w} ${h}`}
          className="absolute inset-0 h-full w-full select-none"
          preserveAspectRatio="none"
          role="img"
          aria-label="Param Chart"
          shapeRendering="geometricPrecision"
        >
          {/* Price pane grid */}
          {[0, 0.25, 0.5, 0.75, 1].map((t) => {
            const y = top + innerH * t;
            const pVal = py1 - (py1 - py0) * t;
            const oVal = oy1 - (oy1 - oy0) * t;
            return (
              <g key={t}>
                <line
                  x1={left}
                  x2={w - right}
                  y1={y}
                  y2={y}
                  stroke="currentColor"
                  className="text-line"
                  strokeWidth={1}
                  opacity={0.35}
                />
                <text
                  x={left - 4}
                  y={y + 3}
                  textAnchor="end"
                  className="fill-rose-500/90"
                  fontSize={9}
                  fontWeight={700}
                  fontFamily="ui-monospace, monospace"
                >
                  {fmtAxis(pVal)}
                </text>
                {hasPriceOverlays ? (
                  <text
                    x={w - right + 4}
                    y={y + 3}
                    textAnchor="start"
                    className="fill-sky-500/90"
                    fontSize={9}
                    fontWeight={700}
                    fontFamily="ui-monospace, monospace"
                  >
                    {fmtAxis(oVal)}
                  </text>
                ) : null}
              </g>
            );
          })}

          {/* Session / no-trade shading (intraday only) */}
          {days.map((d, i) => {
            if (i < winStart || i >= winEnd) return null;
            if (!isNoTradeBand(d.date, interval)) return null;
            return (
              <rect
                key={`nt-${d.date}`}
                x={xAt(i) - barSlot / 2}
                y={top}
                width={Math.max(barSlot, 2)}
                height={priceH - top - 2}
                fill="rgba(245, 158, 11, 0.07)"
              />
            );
          })}
          {days.map((d, i) => {
            if (i < winStart || i >= winEnd) return null;
            const open = isSessionOpenBar(d.date, interval);
            const close = isSessionCloseBar(d.date, interval);
            if (!open && !close) return null;
            return (
              <line
                key={`sess-${d.date}`}
                x1={xAt(i)}
                x2={xAt(i)}
                y1={top}
                y2={priceH - 2}
                stroke={open ? "#22c55e" : "#f97316"}
                strokeWidth={1}
                strokeDasharray="3 3"
                opacity={0.75}
              />
            );
          })}

          {/* Pane divider — price → params (optional) → hist */}
          <line
            x1={left}
            x2={w - right}
            y1={priceH}
            y2={priceH}
            stroke="currentColor"
            className="text-line"
            strokeWidth={1}
            opacity={0.55}
          />
          {showMetricPane ? (
            <>
              {[0, 0.5, 1].map((t) => {
                const y = metricInnerTop + metricInnerH * t;
                const mVal = my1 - (my1 - my0) * t;
                return (
                  <g key={`mgrid-${t}`}>
                    <line
                      x1={left}
                      x2={w - right}
                      y1={y}
                      y2={y}
                      stroke="currentColor"
                      className="text-line"
                      strokeWidth={1}
                      opacity={0.28}
                    />
                    <text
                      x={left - 4}
                      y={y + 3}
                      textAnchor="end"
                      className="fill-violet-500/90"
                      fontSize={9}
                      fontWeight={700}
                      fontFamily="ui-monospace, monospace"
                    >
                      {fmtAxis(mVal)}
                    </text>
                  </g>
                );
              })}
              <text
                x={left + 2}
                y={metricTop + 10}
                className="fill-slate-muted"
                fontSize={9}
              >
                Params
              </text>
              <line
                x1={left}
                x2={w - right}
                y1={metricTop + metricH}
                y2={metricTop + metricH}
                stroke="currentColor"
                className="text-line"
                strokeWidth={1}
                opacity={0.55}
              />
              {metricOverlaySeries.map((s) => {
                if (!s.pts.length) return null;
                const d = s.pts
                  .map(
                    (p, idx) =>
                      `${idx === 0 ? "M" : "L"} ${xAt(p.i)} ${yMetric(p.v)}`,
                  )
                  .join(" ");
                return (
                  <path
                    key={`m-${s.id}`}
                    d={d}
                    fill="none"
                    stroke={s.color}
                    strokeWidth={1.5}
                    strokeLinejoin="round"
                    strokeLinecap="round"
                  />
                );
              })}
            </>
          ) : null}
          <text
            x={left + 2}
            y={histTop + 10}
            className="fill-slate-muted"
            fontSize={9}
          >
            {histMode === "volume" ? "Vol" : "Δ"}
          </text>

          {/* Hist zero / baseline */}
          {histMode === "chg" ? (
            <line
              x1={left}
              x2={w - right}
              y1={histZero}
              y2={histZero}
              stroke="currentColor"
              className="text-slate-muted"
              strokeWidth={1}
              opacity={0.45}
            />
          ) : null}

          {/* Crosshair spanning both panes */}
          {focusIdx >= 0 ? (
            <g>
              <line
                x1={xAt(focusIdx)}
                x2={xAt(focusIdx)}
                y1={top}
                y2={histTop + histH}
                stroke="#787b86"
                strokeWidth={1}
                strokeDasharray="3 3"
                opacity={0.85}
              />
              {sv(days[focusIdx], effectivePrimary) != null ? (
                <g>
                  {(() => {
                    const rawPv = sv(
                      days[focusIdx],
                      effectivePrimary,
                    )!;
                    const pv =
                      usePct && pctBase != null ? toPct(rawPv) : rawPv;
                    const label = usePct
                      ? `${fmtAxis(pv)}%`
                      : fmtAxis(pv);
                    const badgeW = Math.min(
                      left - 4,
                      Math.max(32, label.length * 6.4 + 8),
                    );
                    const cy = yPrimary(pv);
                    // Nudge badge away from grid tick labels when they collide.
                    const tickYs = [0, 0.25, 0.5, 0.75, 1].map(
                      (t) => top + innerH * t,
                    );
                    let yAdj = cy;
                    for (const ty of tickYs) {
                      if (Math.abs(cy - ty) < 11) {
                        yAdj = cy < ty ? ty - 12 : ty + 12;
                        break;
                      }
                    }
                    yAdj = Math.max(top + 7, Math.min(top + innerH - 7, yAdj));
                    return (
                      <>
                        <rect
                          x={2}
                          y={yAdj - 7}
                          width={badgeW}
                          height={14}
                          rx={2}
                          fill="#f43f5e"
                          opacity={0.95}
                        />
                        <text
                          x={2 + badgeW - 3}
                          y={yAdj + 3}
                          textAnchor="end"
                          fill="#fff"
                          fontSize={9}
                          fontWeight={700}
                          fontFamily="ui-monospace, monospace"
                        >
                          {label}
                        </text>
                      </>
                    );
                  })()}
                </g>
              ) : null}
            </g>
          ) : null}

          {/* Series — candlesticks for underlying close when OHLC available */}
          {useCandles
            ? days.map((d, i) => {
                if (i < winStart || i >= winEnd) return null;
                if (
                  d.open == null ||
                  d.high == null ||
                  d.low == null ||
                  d.close == null
                ) {
                  return null;
                }
                const o = Number(d.open);
                const h = Number(d.high);
                const l = Number(d.low);
                const c = Number(d.close);
                const up = c >= o;
                const color = up ? "#26a69a" : "#ef5350";
                const x = xAt(i);
                const bodyTop = yPrimary(Math.max(o, c));
                const bodyBot = yPrimary(Math.min(o, c));
                const bodyH = Math.max(1, bodyBot - bodyTop);
                const wickW = Math.max(0.85, Math.min(1.45, barW * 0.16));
                const bodyW = Math.max(2, Math.min(8, barW * 0.62));
                return (
                  <g key={`cndl-${d.date}`}>
                    <line
                      x1={x}
                      x2={x}
                      y1={yPrimary(h)}
                      y2={yPrimary(l)}
                      stroke={color}
                      strokeWidth={wickW}
                    />
                    <rect
                      x={x - bodyW / 2}
                      y={bodyTop}
                      width={bodyW}
                      height={bodyH}
                      fill={color}
                      stroke={color}
                      strokeWidth={0.5}
                      opacity={0.95}
                    />
                  </g>
                );
              })
            : (
              <path
                d={primaryPath}
                fill="none"
                stroke="#f43f5e"
                strokeWidth={1.25}
                strokeLinejoin="round"
                strokeLinecap="round"
              />
            )}
          {priceOverlaySeries.map((s) => {
            if (!s.pts.length) return null;
            const d = s.pts
              .map(
                (p, idx) =>
                  `${idx === 0 ? "M" : "L"} ${xAt(p.i)} ${yOverlay(p.v)}`,
              )
              .join(" ");
            return (
              <path
                key={s.id}
                d={d}
                fill="none"
                stroke={s.color}
                strokeWidth={1.25}
                strokeLinejoin="round"
                strokeLinecap="round"
              />
            );
          })}

          {/* Day hit areas + focus markers only */}
          {days.map((d, i) => {
            if (i < winStart || i >= winEnd) return null;
            const active = i === focusIdx;
            const pv = sv(d, effectivePrimary);
            const hv = histVals[i];
            const up =
              histMode === "chg"
                ? (hv ?? 0) >= 0
                : (d.chg ?? (d.close != null && d.open != null ? d.close - d.open : 0)) >=
                  0;
            const barColor = up ? "#26a69a" : "#ef5350";
            let barY = histZero;
            let barHeight = 0;
            if (hv != null) {
              if (histMode === "volume") {
                barHeight = (Math.abs(hv) / histAbsMax) * histInner;
                barY = histZero - barHeight;
              } else {
                barHeight = (Math.abs(hv) / histAbsMax) * (histInner / 2);
                barY = hv >= 0 ? histZero - barHeight : histZero;
              }
            }
            return (
              <g key={d.date} className="pointer-events-none">
                <rect
                  x={xAt(i) - barSlot / 2}
                  y={top}
                  width={Math.max(barSlot, 4)}
                  height={histTop + histH - top}
                  fill={active ? "rgba(41,98,255,0.06)" : "transparent"}
                />
                {hv != null && barHeight > 0.5 ? (
                  <rect
                    x={xAt(i) - barW / 2}
                    y={barY}
                    width={barW}
                    height={Math.max(barHeight, 1)}
                    rx={1}
                    fill={barColor}
                    opacity={active ? 0.95 : 0.72}
                  />
                ) : null}
                {active && pv != null ? (
                  <circle
                    cx={xAt(i)}
                    cy={yPrimary(
                      usePct && pctBase != null ? toPct(pv) : pv,
                    )}
                    r={3.2}
                    fill="#f43f5e"
                    stroke="#fff"
                    strokeWidth={1.2}
                  />
                ) : null}
                {active
                  ? priceOverlaySeries.map((s) => {
                      const ov = sv(d, s.id);
                      if (ov == null) return null;
                      return (
                        <circle
                          key={s.id}
                          cx={xAt(i)}
                          cy={yOverlay(ov)}
                          r={3.2}
                          fill={s.color}
                          stroke="#fff"
                          strokeWidth={1.2}
                        />
                      );
                    })
                  : null}
                {active
                  ? metricOverlaySeries.map((s) => {
                      const ov = sv(d, s.id);
                      if (ov == null) return null;
                      return (
                        <circle
                          key={s.id}
                          cx={xAt(i)}
                          cy={yMetric(ov)}
                          r={3.2}
                          fill={s.color}
                          stroke="#fff"
                          strokeWidth={1.2}
                        />
                      );
                    })
                  : null}
              </g>
            );
          })}

          {/* Time axis — sparse ticks only */}
          {xTickOffsets.map((offset) => {
            const i = winStart + offset;
            const d = days[i];
            if (!d) return null;
            const prev =
              offset > 0 ? days[winStart + offset - 1]?.date : null;
            return (
              <text
                key={`xtick-${d.date}`}
                x={xAt(i)}
                y={h - 4}
                textAnchor="middle"
                className="fill-slate-muted"
                fontSize={8}
                fontFamily="ui-monospace, monospace"
              >
                {barAxisLabel(d.date, interval, { prevDate: prev })}
              </text>
            );
          })}
        </svg>

        {focusDay ? (
          <div
            className="pointer-events-none absolute top-2 z-10 min-w-[10rem] rounded border border-white/10 bg-[#131722]/[0.92] px-2.5 py-2 text-white shadow-xl backdrop-blur-sm dark:bg-[#131722]/0.95"
            style={{
              left: `${tipLeftPct}%`,
              transform: tipOnLeft
                ? "translateX(calc(-100% - 8px))"
                : "translateX(8px)",
            }}
          >
            <p className="mb-1.5 font-mono text-[10px] text-white/70">
              D{focusDay.day_index} · {focusDay.date} · {focusDay.weekday}
            </p>
            <ul className="space-y-1">
              {tooltipRows.map((row) => (
                <li
                  key={row.label}
                  className="flex items-center justify-between gap-4 text-[11px]"
                >
                  <span className="inline-flex min-w-0 items-center gap-1.5 text-white/65">
                    <span
                      className="size-1.5 shrink-0 rounded-full"
                      style={{ backgroundColor: row.color }}
                    />
                    <span className="truncate">{row.label}</span>
                  </span>
                  <span className="shrink-0 font-mono tabular-nums text-white">
                    {row.value}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        </div>

        {/* Slim scrubber — labels only on sparse ticks; cells stay clickable */}
        <div
          className="grid shrink-0 border-t border-line/60 bg-canvas/30"
          style={{
            paddingLeft: `${leftPct}%`,
            paddingRight: `${rightPct}%`,
            gridTemplateColumns: `repeat(${n}, minmax(0, 1fr))`,
          }}
        >
          {visibleDays.map((d, offset) => {
            const i = winStart + offset;
            const active = i === focusIdx;
            const showLabel = shouldShowAxisLabel(
              offset,
              n,
              interval,
              active,
            );
            const prev = offset > 0 ? visibleDays[offset - 1]?.date : null;
            return (
              <button
                key={d.date}
                type="button"
                title={`${d.date}${d.weekday ? ` · ${d.weekday}` : ""}`}
                onClick={() => onSelect(d.date)}
                onMouseEnter={() => setHoverIdx(i)}
                className={cn(
                  "flex min-w-0 items-center justify-center border-r border-line/30 px-0 py-1 transition last:border-r-0",
                  active
                    ? "bg-[#2962ff]/15 text-ink"
                    : "text-slate-muted hover:bg-raised/50 hover:text-ink",
                  d.is_today && !active && "text-amber-600",
                )}
              >
                {showLabel ? (
                  <span className="truncate px-0.5 text-center font-mono text-[9px] font-medium leading-none tabular-nums">
                    {barAxisLabel(d.date, interval, { prevDate: prev })}
                  </span>
                ) : (
                  <span
                    className={cn(
                      "block h-1 w-px rounded-full",
                      active ? "bg-[#2962ff]" : "bg-line/70",
                    )}
                    aria-hidden
                  />
                )}
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function ParamSidebar({
  sharedMetrics,
  search,
  onSearch,
  selectedSeries,
  onSelectSeries,
  selectedDay,
  metricsByDay,
  liveMetrics,
  today,
}: {
  sharedMetrics: ParamChartSharedMetric[];
  search: string;
  onSearch: (q: string) => void;
  selectedSeries: ChartSeriesId[];
  onSelectSeries: (id: ChartSeriesId) => void;
  selectedDay: ParamChartDay | null;
  metricsByDay?: Record<string, Record<string, ParamChartMetricValue>>;
  liveMetrics?: Record<string, ParamChartMetricValue>;
  today?: string | null;
}) {
  const q = search.trim().toLowerCase();
  const searching = Boolean(q);
  const [paneCollapsed, setPaneCollapsed] = useState(false);
  const [collapsedGroups, setCollapsedGroups] = useState<Record<string, boolean>>(
    {},
  );

  useEffect(() => {
    try {
      setPaneCollapsed(
        window.localStorage.getItem("atlas-param-chart-sidebar-collapsed") ===
          "1",
      );
      const raw = window.localStorage.getItem(
        "atlas-param-chart-collapsed-groups",
      );
      if (!raw) return;
      const parsed = JSON.parse(raw) as unknown;
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        setCollapsedGroups(parsed as Record<string, boolean>);
      }
    } catch {
      // private mode / blocked storage
    }
  }, []);

  function setPaneOpen(open: boolean) {
    setPaneCollapsed(!open);
    try {
      window.localStorage.setItem(
        "atlas-param-chart-sidebar-collapsed",
        open ? "0" : "1",
      );
    } catch {
      // private mode / blocked storage
    }
  }

  function toggleGroup(title: string) {
    setCollapsedGroups((prev) => {
      const next = { ...prev, [title]: !prev[title] };
      try {
        window.localStorage.setItem(
          "atlas-param-chart-collapsed-groups",
          JSON.stringify(next),
        );
      } catch {
        // private mode / blocked storage
      }
      return next;
    });
  }

  const filteredMetrics = sharedMetrics.filter(
    (m) => {
      if (!q) return true;
      return (
        m.label.toLowerCase().includes(q) ||
        String(m.check_no).includes(q) ||
        m.category.toLowerCase().includes(q)
      );
    },
  );

  const groups = useMemo(() => {
    const out: Array<{ title: string; items: Array<{ id: ChartSeriesId; label: string; meta?: string }> }> = [];
    const builtins = BUILTIN_SERIES.filter(
      (b) => !q || b.label.toLowerCase().includes(q),
    );
    if (builtins.length) {
      out.push({
        title: "Price & premium",
        items: builtins.map((b) => ({ id: b.id, label: b.label })),
      });
    }
    const byCat = new Map<string, ParamChartSharedMetric[]>();
    for (const m of filteredMetrics) {
      const cat = m.category || "Shared params";
      const list = byCat.get(cat) || [];
      list.push(m);
      byCat.set(cat, list);
    }
    for (const [title, items] of byCat) {
      out.push({
        title,
        items: items.map((m) => ({
          id: `metric:${m.id}` as ChartSeriesId,
          label: m.label,
          meta: `#${m.check_no}`,
        })),
      });
    }
    return out;
  }, [filteredMetrics, q]);

  if (paneCollapsed) {
    return (
      <button
        type="button"
        onClick={() => setPaneOpen(true)}
        title="Show parameters"
        aria-label="Show parameters"
        className="flex shrink-0 items-center justify-center gap-2 rounded-md border border-line bg-canvas/40 px-3 py-2 text-xs font-medium text-slate-muted transition hover:bg-raised/70 hover:text-ink lg:h-full lg:w-9 lg:flex-col lg:px-0 lg:py-4"
      >
        <ChevronRightIcon className="h-3.5 w-3.5" />
        <span className="lg:[writing-mode:vertical-rl] lg:rotate-180 lg:text-[10px] lg:font-semibold lg:uppercase lg:tracking-[0.14em]">
          Params
        </span>
      </button>
    );
  }

  return (
    <aside className="flex h-full min-h-0 w-full shrink-0 flex-col rounded-md border border-line bg-canvas/40 lg:max-w-[16.5rem]">
      <div className="flex items-center gap-1 border-b border-line p-1.5">
        <input
          type="search"
          value={search}
          onChange={(e) => onSearch(e.target.value)}
          placeholder="Search parameters…"
          className="min-w-0 flex-1 rounded border border-line bg-raised px-2 py-1.5 text-sm"
        />
        <button
          type="button"
          onClick={() => setPaneOpen(false)}
          title="Hide parameters"
          aria-label="Hide parameters"
          className="shrink-0 rounded p-1 text-slate-muted hover:bg-raised/70 hover:text-ink"
        >
          <ChevronLeftIcon className="h-3.5 w-3.5" />
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-1">
        {groups.map((group) => {
          const open = searching || !collapsedGroups[group.title];
          const selectedInGroup = group.items.filter((item) =>
            item.id === "close"
              ? selectedSeries.length === 0
              : selectedSeries.includes(item.id),
          ).length;
          return (
          <div key={group.title} className="mb-2">
            <button
              type="button"
              onClick={() => toggleGroup(group.title)}
              aria-expanded={open}
              title={open ? `Collapse ${group.title}` : `Expand ${group.title}`}
              className="flex w-full items-center gap-1 rounded-md border border-line/70 bg-fog/60 px-2 py-1.5 text-left text-[11px] font-semibold uppercase tracking-wide text-ink hover:bg-fog"
            >
              {open ? (
                <ChevronDownIcon className="h-3.5 w-3.5 shrink-0 text-slate-muted" />
              ) : (
                <ChevronRightIcon className="h-3.5 w-3.5 shrink-0 text-slate-muted" />
              )}
              <span className="min-w-0 flex-1 truncate">{group.title}</span>
              <span className="shrink-0 font-mono text-[10px] font-normal tabular-nums text-slate-muted">
                {selectedInGroup ? `${selectedInGroup}/` : ""}
                {group.items.length}
              </span>
            </button>
            {open ? (
            <ul className="ml-3 mt-0.5 space-y-px pl-3">
              {group.items.map((item) => {
                const selected =
                  item.id === "close"
                    ? selectedSeries.length === 0
                    : selectedSeries.includes(item.id);
                const colorIdx = selectedSeries.indexOf(item.id);
                const live =
                  item.id.startsWith("metric:") && selectedDay
                    ? (() => {
                        const mid = item.id.slice(7);
                        const dayKey = String(selectedDay.date || "").slice(0, 10);
                        return (
                          (today && dayKey === today
                            ? liveMetrics?.[mid]
                            : undefined) ??
                          selectedDay.metrics?.[mid] ??
                          metricsByDay?.[dayKey]?.[mid] ??
                          null
                        );
                      })()
                    : null;
                return (
                  <li key={item.id}>
                    <button
                      type="button"
                      onClick={() => onSelectSeries(item.id)}
                      title={
                        item.meta
                          ? `${item.meta} · ${item.label}`
                          : item.label
                      }
                      className={cn(
                        "flex w-full items-center gap-2 rounded-md px-1.5 py-1 text-left text-[12px] leading-snug transition",
                        selected
                          ? "bg-sky-500/15 text-ink ring-1 ring-sky-500/40"
                          : "text-ink/80 hover:bg-raised/70 hover:text-ink",
                      )}
                    >
                      <span
                        className="size-2 shrink-0 rounded-full"
                        style={{
                          backgroundColor:
                            item.id === "close"
                              ? "#e11d48"
                              : selected && colorIdx >= 0
                                ? OVERLAY_COLORS[colorIdx % OVERLAY_COLORS.length]
                                : "#94a3b8",
                        }}
                      />
                      <span className="min-w-0 flex-1 truncate">
                        {item.meta ? (
                          <span className="mr-1 font-mono text-[10px] text-slate-muted">
                            {item.meta}
                          </span>
                        ) : null}
                        <span className="font-medium">{item.label}</span>
                      </span>
                      {live?.value != null && live.value !== "" ? (
                        <span className="shrink-0 font-mono text-[11px] tabular-nums text-ink">
                          {String(live.value)}
                        </span>
                      ) : null}
                    </button>
                  </li>
                );
              })}
            </ul>
            ) : null}
          </div>
          );
        })}
      </div>
    </aside>
  );
}

export function ParamChartPanel({
  active = true,
  instrument,
  hideChartButton = false,
}: {
  active?: boolean;
  /**
   * Underlying to open, from the trader workspace.
   *
   * Request-scoped: it rides on `?underlying=` for the month read and the
   * stream, so this window can read another instrument without moving the
   * tenant desk chart. The server clears the desk strike for a scoped read and
   * resolves ATM itself, which is why the strike and entry-premium inputs are
   * disabled while an instrument is pinned — see `pinned` below.
   */
  instrument?: string;
  /** True when a dialog header already shows TV beside the instrument. */
  hideChartButton?: boolean;
}) {
  const { getAccessToken, isLoaded, isSignedIn } = useAgentOsToken();
  /**
   * This window is scoped to one instrument from the workspace.
   *
   * Config writes are desk-wide, so anything that only makes sense for the
   * desk instrument is disabled here: strike and entry premiums are cleared
   * and re-derived server-side for a scoped read, so editing them would move
   * the whole desk's chart while changing nothing in this window. Interval
   * stays live — it is desk-wide too, but it does drive this read.
   */
  const pinned = Boolean(instrument?.trim());
  const [config, setConfig] = useState<ParamChartAdminConfig | null>(null);
  const [presets, setPresets] = useState<SignalUnderlyingPreset[]>([]);
  const [sharedMetrics, setSharedMetrics] = useState<ParamChartSharedMetric[]>(
    [],
  );
  const [month, setMonth] = useState<ParamChartMonthSnapshot | null>(null);
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const [paramSearch, setParamSearch] = useState("");
  const [overlaySeries, setOverlaySeries] = useState<ChartSeriesId[]>(["total"]);
  const [chartStyle, setChartStyle] = useState<"candle" | "line">("candle");
  const [histPreference, setHistPreference] = useState<"auto" | "volume" | "chg">(
    "auto",
  );
  const [showSpotPct, setShowSpotPct] = useState(false);
  const [alertsOn, setAlertsOn] = useState(true);
  const [buildNotice, setBuildNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [draftStrike, setDraftStrike] = useState("");
  const [draftEntryCe, setDraftEntryCe] = useState("");
  const [draftEntryPe, setDraftEntryPe] = useState("");
  const streamAbort = useRef<AbortController | null>(null);
  /** Ignore SSE frames until month pack matches this interval. */
  const pendingIntervalRef = useRef<string | null>(null);
  const [packTargetInterval, setPackTargetInterval] = useState<string | null>(
    null,
  );
  const prefetchedIntervals = useRef(new Set<string>());
  const lastDeskInstrumentKey = useRef("");
  const configRef = useRef(config);
  configRef.current = config;

  const applySnapshot = useCallback((snap: ParamChartMonthSnapshot) => {
    const pending = pendingIntervalRef.current;
    const snapIv = snap.config?.interval || snap.interval || "1D";
    if (pending && snapIv !== pending) {
      // Still building previous pack — keep optimistic interval UI.
      return;
    }
    // Soft SSE / lock stubs: keep "building…" but don't wipe the plot forever.
    const building = packIsBuilding(snap);
    if (building && !(snap.days?.length) && !snap.stream_patch) {
      setLoading(true);
      return;
    }
    if (pending && snapIv === pending) {
      pendingIntervalRef.current = null;
      setPackTargetInterval(null);
    }

    if (snap.stream_patch) {
      setMonth((prev) => mergeMonthStreamPatch(prev, snap));
      setLoading(false);
      if (snap.config) {
        setConfig(snap.config);
        setDraftStrike(
          snap.config.strike == null ? "" : String(snap.config.strike),
        );
        setDraftEntryCe(String(snap.config.entry_ce_premium ?? ""));
        setDraftEntryPe(String(snap.config.entry_pe_premium ?? ""));
      }
      // shared_metrics come from REST/config — not every SSE tick.
      return;
    }

    setMonth(snap);
    setLoading(false);
    if (snap.config) {
      setConfig(snap.config);
      setDraftStrike(
        snap.config.strike == null ? "" : String(snap.config.strike),
      );
      setDraftEntryCe(String(snap.config.entry_ce_premium ?? ""));
      setDraftEntryPe(String(snap.config.entry_pe_premium ?? ""));
    }
    if (snap.shared_metrics) setSharedMetrics(snap.shared_metrics);
    setSelectedDate((prev) => {
      if (prev && snap.days?.some((d) => d.date === prev)) return prev;
      const today = snap.days?.find((d) => d.is_today)?.date;
      return today || snap.days?.[snap.days.length - 1]?.date || null;
    });
  }, []);

  const loadMonthUntilReady = useCallback(
    async (
      token: string,
      opts: {
        year?: number;
        month?: number;
        refresh?: boolean;
        underlying?: string | null;
      },
    ) => {
      const snap = await getParamChartMonth(token, opts);
      if (!snap.ok) return snap;
      if (!packIsBuilding(snap)) return snap;
      // Wait for an in-flight dump. Never pass refresh=true on retries —
      // that would kick another Kite hist pull and trip the 60/min cap.
      let last = snap;
      for (let i = 0; i < 8; i++) {
        await new Promise((r) => setTimeout(r, 2500));
        try {
          last = await getParamChartMonth(token, {
            year: opts.year,
            month: opts.month,
            underlying: opts.underlying,
            refresh: false,
          });
        } catch {
          return last;
        }
        if (!last.ok || !packIsBuilding(last)) return last;
      }
      return last;
    },
    [],
  );

  const prefetchIntervalPack = useCallback(
    async (interval: string) => {
      if (!config || !isLoaded || !isSignedIn || !active) return;
      if ((config.interval || "1D") === interval) return;
      if (!["1m", "5m", "15m", "1H"].includes(interval)) return;
      if (prefetchedIntervals.current.has(interval)) return;
      prefetchedIntervals.current.add(interval);
      try {
        const token = await getAccessToken();
        await getParamChartMonth(token, {
          year: config.year,
          month: config.month,
          interval,
          underlying: instrument?.trim() || null,
          refresh: false,
          buildMissing: false,
        });
      } catch {
        prefetchedIntervals.current.delete(interval);
      }
    },
    [active, config, getAccessToken, instrument, isLoaded, isSignedIn],
  );

  const refresh = useCallback(
    async (opts?: { force?: boolean }) => {
      if (!isLoaded || !isSignedIn || !active) return;
      setLoading(true);
      setError(null);
      setBuildNotice(null);
      try {
        const token = await getAccessToken();
        const [cfg, snap] = await Promise.all([
          getParamChartConfig(token),
          loadMonthUntilReady(token, {
            refresh: Boolean(opts?.force),
            underlying: instrument?.trim() || null,
          }),
        ]);
        // A pinned window is served a scoped config by `month_state`; writing
        // the tenant desk config over it would name the wrong instrument.
        if (cfg.ok && !pinned) {
          setConfig(cfg.config);
          setDraftStrike(
            cfg.config.strike == null ? "" : String(cfg.config.strike),
          );
          setDraftEntryCe(String(cfg.config.entry_ce_premium ?? ""));
          setDraftEntryPe(String(cfg.config.entry_pe_premium ?? ""));
          setPresets(cfg.presets || []);
          setSharedMetrics(cfg.shared_metrics || []);
        } else if (cfg.error) {
          setError(cfg.error);
        }
        if (snap.ok) {
          applySnapshot(snap);
          setBuildNotice(
            packIsBuilding(snap)
              ? "Still rebuilding this month's pack from Kite — the chart updates on its own when it lands."
              : null,
          );
        } else if (snap.error) {
          setError(snap.error);
        }
      } catch (err) {
        setError(
          err instanceof Error ? err.message : "Failed to load Param Chart",
        );
      } finally {
        setLoading(false);
      }
    },
    [
      active,
      applySnapshot,
      getAccessToken,
      instrument,
      isLoaded,
      isSignedIn,
      loadMonthUntilReady,
      pinned,
    ],
  );

  const patchConfig = useCallback(
    async (patch: Partial<ParamChartAdminConfig>) => {
      // Optimistic — interval buttons must flip immediately (1W/1M hist is slow).
      if (patch.interval) {
        pendingIntervalRef.current = String(patch.interval);
        setPackTargetInterval(String(patch.interval));
      }
      setConfig((prev) => (prev ? { ...prev, ...patch } : prev));
      setSaving(true);
      setError(null);
      try {
        const token = await getAccessToken();
        const res = await patchParamChartConfig(token, patch);
        if (!res.ok) {
          pendingIntervalRef.current = null;
          setPackTargetInterval(null);
          setError(res.error || "Config save failed");
          return;
        }
        setConfig(res.config);
        setDraftStrike(
          res.config.strike == null ? "" : String(res.config.strike),
        );
        setDraftEntryCe(String(res.config.entry_ce_premium ?? ""));
        setDraftEntryPe(String(res.config.entry_pe_premium ?? ""));
        setPresets(res.presets || []);
        setSharedMetrics(res.shared_metrics || []);
        // Unblock controls before the (possibly year-long) month rebuild.
        setSaving(false);
        setLoading(true);
        try {
          const snap = await loadMonthUntilReady(token, {
            year: res.config.year,
            month: res.config.month,
            refresh: false,
          });
          if (snap.ok) applySnapshot(snap);
          else if (snap.error) setError(snap.error);
        } finally {
          setLoading(false);
        }
      } catch (err) {
        pendingIntervalRef.current = null;
        setPackTargetInterval(null);
        setError(err instanceof Error ? err.message : "Config save failed");
      } finally {
        setSaving(false);
      }
    },
    [applySnapshot, getAccessToken, loadMonthUntilReady],
  );

  // The workspace's instrument is request-scoped: it rides on ?underlying= for
  // the month read and the stream. It used to PATCH tenant config, which moved
  // the desk chart for every other window and user.

  useEffect(() => {
    if (!active || !config) return;
    const apply = (selection: DeskInstrumentSelection) => {
      if (selection.source === "param-chart") return;
      const key = deskInstrumentIdentityKey(selection);
      if (key === lastDeskInstrumentKey.current) return;
      const current = configRef.current;
      const sameUnderlying =
        current?.underlying_symbol?.trim() === selection.underlying_symbol;
      const sameFut =
        (current?.fut_symbol || "").trim() ===
        (selection.fut_symbol || "").trim();
      const sameStep =
        selection.strike_step == null ||
        current?.strike_step === selection.strike_step;
      if (sameUnderlying && sameFut && sameStep) {
        lastDeskInstrumentKey.current = key;
        return;
      }
      lastDeskInstrumentKey.current = key;
      // Identity only — never apply live chain CE/PE (fixed-strike-per-month).
      void patchConfig({
        underlying_symbol: selection.underlying_symbol,
        underlying_label: selection.underlying_label,
        fut_symbol: selection.fut_symbol,
        ...(selection.strike_step != null
          ? { strike_step: selection.strike_step }
          : {}),
      });
    };
    const existing = readDeskInstrument();
    if (existing && existing.source !== "param-chart") apply(existing);
    return subscribeDeskInstrument(apply);
  }, [active, config, patchConfig]);

  const commitNumeric = useCallback(
    (field: "strike" | "entry_ce_premium" | "entry_pe_premium", raw: string) => {
      if (!config) return;
      if (field === "strike") {
        const next = raw.trim() === "" ? null : Number(raw);
        if (next !== null && Number.isNaN(next)) return;
        if (next === config.strike) return;
        void patchConfig({ strike: next });
        return;
      }
      const next = Number(raw);
      if (Number.isNaN(next)) return;
      if (field === "entry_ce_premium" && next === config.entry_ce_premium) return;
      if (field === "entry_pe_premium" && next === config.entry_pe_premium) return;
      void patchConfig({ [field]: next });
    },
    [config, patchConfig],
  );

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    if (!active || !isLoaded || !isSignedIn) return;
    const ac = new AbortController();
    streamAbort.current?.abort();
    streamAbort.current = ac;
    let cancelled = false;

    (async () => {
      let delayMs = 1000;
      while (!cancelled && !ac.signal.aborted) {
        try {
          const token = await getAccessToken();
          await streamParamChart({
            accessToken: token,
            signal: ac.signal,
            underlying: instrument?.trim() || null,
            onState: (snap) => {
              if (!cancelled && snap.ok) applySnapshot(snap);
            },
          });
          // Clean end (tab switch / abort) — stop reconnect loop.
          break;
        } catch {
          if (cancelled || ac.signal.aborted) break;
          await new Promise((r) => setTimeout(r, delayMs));
          delayMs = Math.min(delayMs * 2, 15_000);
        }
      }
    })();

    return () => {
      cancelled = true;
      ac.abort();
    };
  }, [active, applySnapshot, getAccessToken, instrument, isLoaded, isSignedIn]);

  const selectedDay = useMemo(() => {
    const days = month?.days;
    if (!days?.length) return null;
    return (
      days.find((d) => d.date === selectedDate) ??
      days.find((d) => d.is_today) ??
      days[days.length - 1] ??
      null
    );
  }, [month, selectedDate]);

  const packBuilding =
    loading ||
    Boolean(month?.building) ||
    packTargetInterval != null;

  const chartInterval = useMemo(() => {
    const target = config?.interval || month?.interval || "1D";
    if (
      packBuilding &&
      month?.days?.length &&
      month.interval &&
      month.interval !== target
    ) {
      return month.interval;
    }
    return target;
  }, [config?.interval, month?.days?.length, month?.interval, packBuilding]);

  const overlayLabels = useMemo(
    () => overlaySeries.map((id) => seriesLabel(id, sharedMetrics)),
    [overlaySeries, sharedMetrics],
  );

  const alertHits = useMemo(() => {
    if (!alertsOn) return [] as string[];
    const day =
      (selectedDate && month?.days?.find((d) => d.date === selectedDate)) ||
      month?.days?.[month.days.length - 1];
    const dayKey = String(day?.date || month?.today || "").slice(0, 10);
    const metrics =
      (dayKey && month?.metrics_by_day?.[dayKey]) ||
      month?.live_metrics ||
      (day?.metrics && Object.keys(day.metrics).length ? day.metrics : null);
    if (!metrics) return [];
    const hits: string[] = [];
    const pcr = Number(metrics.chk_008?.value);
    if (Number.isFinite(pcr) && pcr > 1.2) {
      hits.push(`PCR ${pcr.toFixed(2)} > 1.2`);
    }
    const decay = Number(metrics.chk_010?.value);
    if (Number.isFinite(decay) && Math.abs(decay) >= 20) {
      hits.push(`Straddle decay |${decay}| ≥ 20`);
    }
    const bnPct = Number(metrics.chk_016?.value);
    if (Number.isFinite(bnPct) && Math.abs(bnPct) >= 0.6) {
      hits.push(`BANKNIFTY move ${bnPct}% (≥ 0.6%)`);
    }
    return hits;
  }, [
    alertsOn,
    month?.days,
    month?.live_metrics,
    month?.metrics_by_day,
    month?.today,
    selectedDate,
  ]);

  const yearOptions = useMemo(() => {
    const y = config?.year ?? new Date().getFullYear();
    return [y - 1, y, y + 1];
  }, [config?.year]);

  function selectSeries(id: ChartSeriesId) {
    setOverlaySeries((prev) => toggleOverlay(prev, id, MAX_OVERLAYS));
  }

  return (
    <div className="flex h-full min-h-0 flex-col gap-3 overflow-hidden">
      <div className="flex shrink-0 flex-wrap items-center gap-2 rounded-md border border-line bg-canvas/40 px-2 py-1.5">
        {/* TV belongs beside whatever names the instrument. In a dialog the
            header carries it instead. */}
        {!hideChartButton ? (
          <TradingViewButton
            symbol={config?.underlying_symbol ?? ""}
            label={config?.underlying_label}
          />
        ) : null}
        {/* Pinned by the workspace row: hide the picker rather than show a
            disabled one — switching here would also move the desk-wide chart
            out from under this window's own heading. */}
        <select
          className={cn(
            "rounded border border-line bg-raised px-2 py-1 text-sm disabled:opacity-60",
            pinned && "hidden",
          )}
          disabled={!config || saving || pinned}
          value={config?.underlying_symbol ?? ""}
          onChange={(e) => {
            const symbol = e.target.value;
            const preset = presets.find((p) => p.symbol === symbol);
            void patchConfig({
              underlying_symbol: symbol,
              underlying_label: preset?.label ?? symbol,
              ...(preset?.strike_step != null
                ? { strike_step: preset.strike_step }
                : {}),
            });
            publishDeskInstrument({
              underlying_symbol: symbol,
              underlying_label: preset?.label ?? symbol,
              fut_symbol:
                preset?.fut_symbol?.trim() ||
                suggestFutSymbol(symbol) ||
                undefined,
              strike_step: preset?.strike_step,
              source: "param-chart",
            });
          }}
        >
          {(presets.length
            ? presets
            : config
              ? [
                  {
                    symbol: config.underlying_symbol,
                    label: config.underlying_label,
                    strike_step: config.strike_step,
                  },
                ]
              : []
          ).map((p) => (
            <option key={p.symbol} value={p.symbol}>
              {p.label}
            </option>
          ))}
        </select>

        <span aria-hidden className="h-5 w-px shrink-0 bg-line" />
        <label className="flex items-center gap-1 text-xs text-slate-muted">
          Strike
          <input
            type="number"
            className="w-24 rounded border border-line bg-raised px-2 py-1 font-mono text-sm"
            disabled={!config || saving || pinned}
            title={
              pinned
                ? "Set on the desk chart — a pinned window resolves this from live spot"
                : undefined
            }
            value={draftStrike}
            onChange={(e) => setDraftStrike(e.target.value)}
            onBlur={() => commitNumeric("strike", draftStrike)}
            onKeyDown={(e) => {
              if (e.key === "Enter") (e.target as HTMLInputElement).blur();
            }}
          />
        </label>

        <label className="flex items-center gap-1 text-xs text-slate-muted">
          Entry CE
          <input
            type="number"
            className="w-20 rounded border border-line bg-raised px-2 py-1 font-mono text-sm"
            disabled={!config || saving || pinned}
            title={
              pinned
                ? "Set on the desk chart — a pinned window resolves this from live spot"
                : undefined
            }
            value={draftEntryCe}
            onChange={(e) => setDraftEntryCe(e.target.value)}
            onBlur={() => commitNumeric("entry_ce_premium", draftEntryCe)}
            onKeyDown={(e) => {
              if (e.key === "Enter") (e.target as HTMLInputElement).blur();
            }}
          />
        </label>
        <label className="flex items-center gap-1 text-xs text-slate-muted">
          Entry PE
          <input
            type="number"
            className="w-20 rounded border border-line bg-raised px-2 py-1 font-mono text-sm"
            disabled={!config || saving || pinned}
            title={
              pinned
                ? "Set on the desk chart — a pinned window resolves this from live spot"
                : undefined
            }
            value={draftEntryPe}
            onChange={(e) => setDraftEntryPe(e.target.value)}
            onBlur={() => commitNumeric("entry_pe_premium", draftEntryPe)}
            onKeyDown={(e) => {
              if (e.key === "Enter") (e.target as HTMLInputElement).blur();
            }}
          />
        </label>

        <span aria-hidden className="h-5 w-px shrink-0 bg-line" />
        <div className="flex items-center rounded-md border border-line bg-raised p-0.5">
          {INTERVAL_OPTIONS.map((opt) => {
            const active = (config?.interval || "1D") === opt.id;
            return (
              <button
                key={opt.id}
                type="button"
                disabled={!config}
                onClick={() => {
                  if ((config?.interval || "1D") === opt.id) return;
                  void patchConfig({ interval: opt.id });
                }}
                onMouseEnter={() => void prefetchIntervalPack(opt.id)}
                className={cn(
                  "rounded px-2.5 py-1 font-mono text-xs font-semibold transition",
                  active
                    ? "bg-[#2962ff] text-white shadow-sm"
                    : "text-slate-muted hover:bg-canvas/80 hover:text-ink",
                )}
                title={
                  opt.id === "1m"
                    ? "1-minute bars for selected month"
                    : opt.id === "5m"
                      ? "5-minute bars for selected month"
                      : opt.id === "15m"
                        ? "15-minute bars for selected month"
                        : opt.id === "1H"
                          ? "Hourly bars for selected month"
                          : opt.id === "1W"
                            ? "Weekly bars for selected year"
                            : opt.id === "1M"
                              ? "Monthly bars for selected year"
                              : "Daily bars for selected month"
                }
              >
                {opt.label}
              </button>
            );
          })}
        </div>

        <span aria-hidden className="h-5 w-px shrink-0 bg-line" />
        <select
          className="rounded border border-line bg-raised px-2 py-1 text-sm"
          disabled={
            !config ||
            saving ||
            (config?.interval || "1D") === "1M" ||
            (config?.interval || "1D") === "1W"
          }
          value={config?.month ?? 1}
          onChange={(e) => void patchConfig({ month: Number(e.target.value) })}
        >
          {MONTH_LABELS.map((label, i) => (
            <option key={label} value={i + 1}>
              {label}
            </option>
          ))}
        </select>
        <select
          className="rounded border border-line bg-raised px-2 py-1 text-sm"
          disabled={!config || saving}
          value={config?.year ?? new Date().getFullYear()}
          onChange={(e) => void patchConfig({ year: Number(e.target.value) })}
        >
          {yearOptions.map((y) => (
            <option key={y} value={y}>
              {y}
            </option>
          ))}
        </select>

        <div className="ml-auto flex items-center gap-2">
          {config?.strike != null ? (
            <span className="hidden font-mono text-[11px] text-slate-muted xl:inline">
              {config.ce_symbol || "—"} / {config.pe_symbol || "—"}
            </span>
          ) : null}
          <Button
            type="button"
            size="sm"
            variant="secondary"
            icon={<RefreshIcon />}
            disabled={loading || saving}
            onClick={() => void refresh({ force: true })}
          >
            {loading || month?.building ? "Loading…" : "Refresh"}
          </Button>
        </div>
      </div>

      {error ? (
        <p className="shrink-0 rounded-md border border-rose-300/60 bg-rose-50 px-3 py-2 text-sm text-rose-700">
          {error}
        </p>
      ) : null}

      {buildNotice ? (
        <p className="shrink-0 rounded-md border border-amber/40 bg-amber/10 px-3 py-2 text-sm text-amber">
          {buildNotice}
        </p>
      ) : null}

      {alertHits.length ? (
        <div className="shrink-0 rounded-md border border-amber-300/70 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-500/40 dark:bg-amber-500/10 dark:text-amber-100">
          <span className="font-semibold">Param alerts · </span>
          {alertHits.join(" · ")}
        </div>
      ) : null}

      <div className="flex min-h-0 flex-1 flex-col gap-3 lg:flex-row">
        <ParamSidebar
          sharedMetrics={sharedMetrics}
          search={paramSearch}
          onSearch={setParamSearch}
          selectedSeries={overlaySeries}
          onSelectSeries={selectSeries}
          selectedDay={selectedDay}
          metricsByDay={month?.metrics_by_day}
          liveMetrics={month?.live_metrics}
          today={month?.today}
        />

        <div className="flex min-h-0 min-w-0 flex-1 flex-col">
          <div className="flex min-h-[22rem] flex-1 flex-col overflow-hidden rounded-md border border-line bg-raised/40 p-2">
            <div className="mb-1.5 flex shrink-0 flex-wrap items-center justify-between gap-2">
              <h2 className="text-sm font-semibold tracking-tight">
                {config?.underlying_label ?? "Param Chart"} ·{" "}
                {config
                  ? (config.interval || "1D") === "1M" ||
                    (config.interval || "1D") === "1W"
                    ? `${config.year} · ${config.interval || "1D"}`
                    : `${MONTH_LABELS[(config.month || 1) - 1]} ${config.year} · ${config.interval || "1D"}`
                  : ""}
                {packBuilding ? (
                  <span className="ml-2 text-xs font-normal text-slate-muted">
                    {packTargetInterval &&
                    month?.interval &&
                    packTargetInterval !== month.interval
                      ? `(building ${packTargetInterval} — showing ${month.interval} until ready)`
                      : packTargetInterval === "1m" ||
                          packTargetInterval === "5m" ||
                          config?.interval === "1m" ||
                          config?.interval === "5m"
                        ? "(first intraday dump can take ~15–45s — then reused)"
                        : "(building chart…)"}
                  </span>
                ) : null}
              </h2>
              <div className="flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  onClick={() => setShowSpotPct((v) => !v)}
                  className={cn(
                    "rounded border px-2 py-0.5 text-[10px] font-semibold transition",
                    showSpotPct
                      ? "border-[#2962ff] bg-[#2962ff] text-white"
                      : "border-line bg-raised text-slate-muted hover:text-ink",
                  )}
                  title="Plot spot as % from first close in the visible window"
                >
                  Spot %
                </button>
                <button
                  type="button"
                  onClick={() => setAlertsOn((v) => !v)}
                  className={cn(
                    "rounded border px-2 py-0.5 text-[10px] font-semibold transition",
                    alertsOn
                      ? "border-amber-500/80 bg-amber-500/15 text-amber-800 dark:text-amber-200"
                      : "border-line bg-raised text-slate-muted hover:text-ink",
                  )}
                  title="Toggle parameter threshold alerts on selected / last bar"
                >
                  Alerts
                </button>
                <div className="flex items-center rounded-md border border-line bg-raised p-0.5">
                  {(
                    [
                      ["candle", "Candles"],
                      ["line", "Line"],
                    ] as const
                  ).map(([id, label]) => (
                    <button
                      key={id}
                      type="button"
                      onClick={() => setChartStyle(id)}
                      className={cn(
                        "rounded px-2 py-0.5 text-[10px] font-semibold transition",
                        chartStyle === id
                          ? "bg-[#2962ff] text-white"
                          : "text-slate-muted hover:text-ink",
                      )}
                    >
                      {label}
                    </button>
                  ))}
                </div>
                <div className="flex items-center rounded-md border border-line bg-raised p-0.5">
                  {(
                    [
                      ["auto", "Auto"],
                      ["volume", "Vol"],
                      ["chg", "Δ"],
                    ] as const
                  ).map(([id, label]) => (
                    <button
                      key={id}
                      type="button"
                      onClick={() => setHistPreference(id)}
                      className={cn(
                        "rounded px-2 py-0.5 text-[10px] font-semibold transition",
                        histPreference === id
                          ? "bg-[#2962ff] text-white"
                          : "text-slate-muted hover:text-ink",
                      )}
                      title={
                        id === "auto"
                          ? "Volume when present, else day Δ"
                          : id === "volume"
                            ? "Bottom pane = volume"
                            : "Bottom pane = day Δ close"
                      }
                    >
                      {label}
                    </button>
                  ))}
                </div>
              </div>
            </div>
            {selectedDay ? (
              <div className="mb-1.5 flex shrink-0 flex-wrap items-center gap-x-2 gap-y-1 rounded-md border border-line bg-canvas/50 px-2.5 py-1.5 text-xs">
                <span className="text-slate-muted">
                  Day {selectedDay.day_index} · {selectedDay.date}
                </span>
                <span className="text-slate-muted">·</span>
                <span className="font-mono tabular-nums">
                  Close {fmt(selectedDay.close)} · Total{" "}
                  {fmt(selectedDay.total, 1)}
                </span>
                {overlaySeries.map((id, idx) => {
                  if (id === "total" || id === "close") return null;
                  const label = overlayLabels[idx];
                  let value: string = "—";
                  if (id === "ce") value = fmt(selectedDay.ce, 1);
                  else if (id === "pe") value = fmt(selectedDay.pe, 1);
                  else if (id.startsWith("metric:")) {
                    const v = seriesValue(selectedDay, id, {
                      metricsByDay: month?.metrics_by_day,
                      liveMetrics: month?.live_metrics,
                      today: month?.today,
                    });
                    value = v == null ? "—" : String(v);
                  }
                  return (
                    <span key={id} className="inline-flex items-center gap-1">
                      <span className="text-slate-muted">·</span>
                      <span
                        className="size-1.5 shrink-0 rounded-full"
                        style={{
                          backgroundColor:
                            OVERLAY_COLORS[idx % OVERLAY_COLORS.length],
                        }}
                      />
                      <span className="font-medium text-ink">{label}</span>
                      <span className="font-mono tabular-nums">{value}</span>
                    </span>
                  );
                })}
              </div>
            ) : null}
            <div className="relative flex min-h-0 flex-1 flex-col">
              <DualAxisMonthChart
                days={month?.days ?? []}
                selectedDate={selectedDate}
                onSelect={setSelectedDate}
                primaryId="close"
                overlayIds={overlaySeries}
                overlayLabels={overlayLabels}
                interval={chartInterval}
                kiteErrors={month?.kite?.errors}
                kiteLiveError={month?.kite_live?.error}
                chartStyle={chartStyle}
                histPreference={histPreference}
                showSpotPct={showSpotPct}
                metricsByDay={month?.metrics_by_day}
                liveMetrics={month?.live_metrics}
                today={month?.today}
                year={config?.year ?? month?.year}
                month={config?.month ?? month?.month}
              />
              {packBuilding && packTargetInterval ? (
                <div className="pointer-events-none absolute inset-0 flex items-start justify-end p-2">
                  <span className="rounded-md border border-line/80 bg-canvas/90 px-2 py-1 text-[10px] font-semibold text-slate-muted shadow-sm backdrop-blur-sm">
                    Building {packTargetInterval}…
                  </span>
                </div>
              ) : null}
            </div>
            {(() => {
              const days = month?.days ?? [];
              const wantsPremium = overlaySeries.some(
                (id) => id === "ce" || id === "pe" || id === "total",
              );
              const hasPremium = days.some(
                (d) => d.ce != null || d.pe != null || d.total != null,
              );
              const tokenMiss = (month?.kite?.errors || []).some((e) =>
                /ce_token_missing|pe_token_missing/i.test(String(e)),
              );
              const future = isFutureCalendarMonth(
                config?.year ?? month?.year,
                config?.month ?? month?.month,
                month?.today,
              );
              if (
                !days.length ||
                future ||
                !wantsPremium ||
                hasPremium ||
                !tokenMiss
              ) {
                return null;
              }
              return (
                <p className="mt-2 shrink-0 rounded-md border border-amber-300/50 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:border-amber-500/30 dark:bg-amber-950/40 dark:text-amber-100">
                  CE/PE premiums missing for this month: the option contract is
                  expired and Atlas has no saved instrument token yet. Open this
                  month and hit <strong>Refresh</strong> once while the contract
                  is still live — we persist the token so hist premiums work
                  after expiry. Spot/OHLC is unaffected.
                </p>
              );
            })()}
          </div>
        </div>
      </div>
    </div>
  );
}
