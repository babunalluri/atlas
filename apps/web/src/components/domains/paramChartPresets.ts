/** Built-in Param Chart layout presets (TradingView-style). */

import type { ChartSeriesId } from "./paramChartSeries";

export type ParamChartPreset = {
  id: string;
  label: string;
  /** Optional interval flip when applying. */
  interval?: string;
  overlays: ChartSeriesId[];
  chartStyle?: "candle" | "line";
  histPreference?: "auto" | "volume" | "chg";
};

/** Desk-oriented starter layouts (metric ids match checklist chk_*). */
export const BUILTIN_PARAM_CHART_PRESETS: ParamChartPreset[] = [
  {
    id: "premium",
    label: "Premium trail",
    interval: "1D",
    overlays: ["total", "ce", "pe"],
    chartStyle: "candle",
    histPreference: "auto",
  },
  {
    id: "intraday",
    label: "Intraday",
    interval: "5m",
    overlays: ["total"],
    chartStyle: "candle",
    histPreference: "volume",
  },
  {
    id: "decay",
    label: "Decay watch",
    interval: "1D",
    overlays: ["total", "metric:chk_010", "metric:chk_008"],
    chartStyle: "candle",
  },
  {
    id: "globals",
    label: "Global open",
    interval: "1H",
    overlays: ["metric:chk_061", "metric:chk_015", "metric:chk_016"],
    chartStyle: "line",
  },
  {
    id: "open-bias",
    label: "Open bias",
    interval: "1m",
    overlays: ["total", "metric:chk_048", "metric:chk_007"],
    chartStyle: "candle",
  },
];

const STORAGE_KEY = "atlas.paramChart.customPresets";

export function loadCustomPresets(): ParamChartPreset[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as ParamChartPreset[];
    return Array.isArray(parsed) ? parsed.filter((p) => p?.id && p?.label) : [];
  } catch {
    return [];
  }
}

export function saveCustomPreset(preset: ParamChartPreset): ParamChartPreset[] {
  const prev = loadCustomPresets().filter((p) => p.id !== preset.id);
  const next = [preset, ...prev].slice(0, 12);
  if (typeof window !== "undefined") {
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
    } catch {
      // Quota / private mode — keep in-memory list for this session.
    }
  }
  return next;
}

export function deleteCustomPreset(id: string): ParamChartPreset[] {
  const next = loadCustomPresets().filter((p) => p.id !== id);
  if (typeof window !== "undefined") {
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
    } catch {
      // ignore
    }
  }
  return next;
}
