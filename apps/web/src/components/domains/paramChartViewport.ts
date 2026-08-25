/** Viewport math for Param Chart drag-pan / wheel-zoom. */

export function clampInt(n: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, n));
}

/** Normalize a [start, count) window over ``n`` bars. */
export function clampView(
  start: number,
  count: number,
  n: number,
): { start: number; count: number } {
  if (n <= 0) return { start: 0, count: 0 };
  const c = clampInt(Math.round(count), 1, n);
  const s = clampInt(Math.round(start), 0, n - c);
  return { start: s, count: c };
}

/**
 * Zoom ``count`` by ``factor`` keeping ``anchor`` (bar index) stable in the
 * window. ``factor`` < 1 zooms in; ``factor`` > 1 zooms out.
 */
export function zoomView(
  start: number,
  count: number,
  n: number,
  anchor: number,
  factor: number,
  minCount = 5,
): { start: number; count: number } {
  if (n <= 0) return { start: 0, count: 0 };
  const cur = clampView(start, count, n);
  const floor = Math.min(minCount, n);
  let nextCount = Math.round(cur.count * factor);
  nextCount = clampInt(nextCount, floor, n);
  if (nextCount === cur.count) return cur;
  const a = clampInt(Math.round(anchor), 0, n - 1);
  const rel =
    cur.count > 0 ? (a - cur.start + 0.5) / cur.count : 0.5;
  const nextStart = a + 0.5 - rel * nextCount;
  return clampView(nextStart, nextCount, n);
}

/** Pan by a fractional window width (negative = drag right / content left). */
export function panView(
  start: number,
  count: number,
  n: number,
  deltaBars: number,
): { start: number; count: number } {
  const cur = clampView(start, count, n);
  return clampView(cur.start + deltaBars, cur.count, n);
}

/** Ensure ``index`` is inside the window; pan minimally if needed. */
export function ensureIndexVisible(
  start: number,
  count: number,
  n: number,
  index: number,
): { start: number; count: number } {
  const cur = clampView(start, count, n);
  if (n <= 0) return cur;
  const i = clampInt(index, 0, n - 1);
  if (i < cur.start) return clampView(i, cur.count, n);
  if (i >= cur.start + cur.count) {
    return clampView(i - cur.count + 1, cur.count, n);
  }
  return cur;
}
