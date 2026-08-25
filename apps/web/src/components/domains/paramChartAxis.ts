/** Sparse X-axis / scrubber labels for Param Chart. */

export function axisLabelStep(barCount: number, interval: string): number {
  const n = Math.max(0, Math.floor(barCount));
  if (n <= 1) return 1;
  if (n <= 8) return 1;
  if (n <= 14) return 2;
  // Aim for ~6–8 readable labels across the window.
  const target =
    interval === "1m" || interval === "5m" || interval === "15m"
      ? 6
      : interval === "1H"
        ? 6
        : interval === "1D"
          ? 8
          : 8;
  return Math.max(2, Math.ceil(n / target));
}

/** Show label on first/last, every step, and the focused bar. */
export function shouldShowAxisLabel(
  offset: number,
  barCount: number,
  interval: string,
  isFocus = false,
): boolean {
  if (barCount <= 0) return false;
  if (isFocus) return true;
  if (offset === 0 || offset === barCount - 1) return true;
  const step = axisLabelStep(barCount, interval);
  return offset % step === 0;
}

const MONTH_SHORT = [
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
] as const;

/** Compact axis text; for 1H prefer hour-only between day changes. */
export function barAxisLabel(
  date: string,
  interval: string,
  opts?: { prevDate?: string | null },
): string {
  const prevDate = opts?.prevDate;
  if (interval === "1M") {
    const m = Number(date.slice(5, 7));
    return MONTH_SHORT[(m || 1) - 1] || date.slice(5, 7);
  }
  if (interval === "1W") {
    const day = date.slice(8, 10);
    const m = Number(date.slice(5, 7));
    return `${Number(day) || day} ${MONTH_SHORT[(m || 1) - 1] || ""}`.trim();
  }
  if (interval === "1H" || interval === "1m" || interval === "5m" || interval === "15m") {
    const day = date.slice(8, 10);
    const hour = date.includes("T") ? date.slice(11, 13) : "";
    const minute =
      (interval === "1m" || interval === "5m" || interval === "15m") &&
      date.includes("T")
        ? date.slice(14, 16)
        : "";
    const prevDay = prevDate?.slice(8, 10);
    const dayChanged = !prevDate || prevDay !== day;
    if (!hour) return day;
    if (interval !== "1H") {
      const hm = minute ? `${hour}:${minute}` : `${hour}h`;
      if (dayChanged) return `${Number(day) || day}·${hm}`;
      return hm;
    }
    // Sparse ticks: show "2·14h" on day change, else "14h".
    if (dayChanged) return `${Number(day) || day}·${hour}h`;
    return `${hour}h`;
  }
  // 1D — day of month
  return String(Number(date.slice(8, 10)) || date.slice(8, 10));
}
