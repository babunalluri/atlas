/** Shared Param Chart overlay / sidebar helpers (panel + tests). */

export type ChartSeriesId = "close" | "total" | "ce" | "pe" | `metric:${string}`;

export const MAX_OVERLAYS = 6;

/** Price-like overlays stay on the main OHLC pane (right axis). */
export const PRICE_OVERLAY_IDS: ReadonlySet<string> = new Set([
  "total",
  "ce",
  "pe",
]);

/** Checklist / shared-param overlays go to the lower metrics pane. */
export function isMetricSeries(id: string): boolean {
  return id.startsWith("metric:");
}

export function isPriceOverlay(id: string): boolean {
  return PRICE_OVERLAY_IDS.has(id);
}

/** Split selected overlays into price-pane vs metrics-pane series. */
export function partitionOverlays(ids: ChartSeriesId[]): {
  price: ChartSeriesId[];
  metrics: ChartSeriesId[];
} {
  const price: ChartSeriesId[] = [];
  const metrics: ChartSeriesId[] = [];
  for (const id of ids) {
    if (isMetricSeries(id)) metrics.push(id);
    else if (id !== "close") price.push(id);
  }
  return { price, metrics };
}

/** Category filter used by the bottom/side param list (empty = all). */
export function filterByCategory<T extends { category: string }>(
  metrics: T[],
  category: string,
): T[] {
  return metrics.filter((m) => !category || m.category === category);
}

/**
 * Toggle an overlay series. ``close`` clears overlays (close stays the base axis).
 * Caps at ``max`` by dropping the oldest selection.
 */
export function toggleOverlay(
  prev: ChartSeriesId[],
  id: ChartSeriesId,
  max = MAX_OVERLAYS,
): ChartSeriesId[] {
  if (id === "close") return [];
  if (prev.includes(id)) return prev.filter((x) => x !== id);
  if (prev.length >= max) return [...prev.slice(1), id];
  return [...prev, id];
}
