/** Session / no-trade helpers for Param Chart (IST cash session). */

/** NSE cash-ish session cues used for shading / markers. */
export const SESSION_OPEN = "09:15";
export const SESSION_CLOSE = "15:29";
/** First-minutes / last-minutes “be careful” bands (desk Timing vibe). */
export const NO_TRADE_OPEN_END = "09:20";
export const NO_TRADE_CLOSE_START = "15:20";

export function barHm(date: string): string | null {
  if (!date.includes("T") || date.length < 16) return null;
  return date.slice(11, 16);
}

export function isIntradayInterval(interval: string): boolean {
  return interval === "1m" || interval === "5m" || interval === "15m" || interval === "1H";
}

/** True when this bar starts a cash session (09:15). */
export function isSessionOpenBar(date: string, interval: string): boolean {
  if (!isIntradayInterval(interval)) return false;
  const hm = barHm(date);
  if (!hm) return false;
  if (interval === "1H") return hm.startsWith("09");
  return hm === SESSION_OPEN;
}

export function isSessionCloseBar(date: string, interval: string): boolean {
  if (!isIntradayInterval(interval)) return false;
  const hm = barHm(date);
  if (!hm) return false;
  if (interval === "1H") return hm.startsWith("15");
  return hm >= "15:25" && hm <= SESSION_CLOSE;
}

/** Open band 09:15–09:20 or close band 15:20–15:30. */
export function isNoTradeBand(date: string, interval: string): boolean {
  if (!isIntradayInterval(interval)) return false;
  const hm = barHm(date);
  if (!hm) return false;
  if (interval === "1H") return hm.startsWith("09") || hm.startsWith("15");
  return (
    (hm >= SESSION_OPEN && hm < NO_TRADE_OPEN_END) ||
    (hm >= NO_TRADE_CLOSE_START && hm <= "15:30")
  );
}

/**
 * Spot % change from the first non-null close in the visible window
 * (TradingView-style “% scale” for the active underlying).
 */
export function pctFromWindowOpen(
  closes: Array<number | null | undefined>,
  winStart: number,
  winEnd: number,
): Array<number | null> {
  let base: number | null = null;
  for (let i = winStart; i < winEnd; i++) {
    const v = closes[i];
    if (v != null && Number.isFinite(Number(v)) && Number(v) !== 0) {
      base = Number(v);
      break;
    }
  }
  if (base == null) return closes.map(() => null);
  return closes.map((v, i) => {
    if (i < winStart || i >= winEnd) return null;
    if (v == null || !Number.isFinite(Number(v))) return null;
    return ((Number(v) - base) / base) * 100;
  });
}
