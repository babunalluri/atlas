/**
 * Rate-limit aware retry helpers for live desk streams.
 *
 * The backend limits each user to `RATE_LIMIT_PER_MINUTE` requests in a sliding
 * window. A stream loop that reconnects on a sub-second backoff will keep that
 * window permanently full, so the panel can never recover on its own — every
 * retry is itself another request. Treat 429 as "stop asking for a while",
 * not as an ordinary transient error.
 */

/**
 * True when an API error is a rate-limit rejection (HTTP 429).
 *
 * Matched on the shapes our clients actually throw — `(429)`, `(429):`, or a
 * trailing status — rather than a bare `\b429\b`, which also fires on any
 * message that happens to contain the number (a strike, an order id, a price).
 * A false positive here costs a 30 s stall, so keep it narrow.
 */
export function isRateLimited(error: unknown): boolean {
  const message =
    error instanceof Error ? error.message : typeof error === "string" ? error : "";
  return /[(\s:]429[)\s:,.]|[(\s:]429$/.test(message) || /rate limit/i.test(message);
}

/** Long enough for the server's 60s sliding window to actually drain. */
export const RATE_LIMIT_BACKOFF_MS = 30_000;

/**
 * Next reconnect delay for a stream loop.
 *
 * Ordinary failures ramp 500ms → 8s. A 429 jumps straight to a window-sized
 * wait, because retrying sooner is what keeps the limit tripped.
 */
export function nextStreamBackoffMs(
  currentMs: number,
  error: unknown,
  { maxMs = 8_000 }: { maxMs?: number } = {},
): number {
  if (isRateLimited(error)) return RATE_LIMIT_BACKOFF_MS;
  return Math.min(Math.max(500, currentMs * 2), maxMs);
}
