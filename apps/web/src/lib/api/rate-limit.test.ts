import { describe, expect, it } from "vitest";

import {
  RATE_LIMIT_BACKOFF_MS,
  isRateLimited,
  nextStreamBackoffMs,
} from "@/lib/api/rate-limit";

describe("isRateLimited", () => {
  it("matches the shapes our stream clients actually throw", () => {
    expect(
      isRateLimited(new Error("Options Lab stream failed (429): slow down")),
    ).toBe(true);
    expect(isRateLimited(new Error("Upload failed (429)"))).toBe(true);
    expect(isRateLimited(new Error("Request failed: 429"))).toBe(true);
    expect(isRateLimited("rate limit exceeded")).toBe(true);
  });

  it("does not fire on a number that merely contains 429", () => {
    // A stall costs 30s of blank desk, so a strike, price or id must not trip it.
    expect(isRateLimited(new Error("Chain failed for strike 24290"))).toBe(false);
    expect(isRateLimited(new Error("Order 1429007 rejected"))).toBe(false);
    expect(isRateLimited(new Error("stream failed (500)"))).toBe(false);
  });

  it("is false for non-errors", () => {
    expect(isRateLimited(null)).toBe(false);
    expect(isRateLimited(undefined)).toBe(false);
  });
});

describe("nextStreamBackoffMs", () => {
  it("jumps straight to a full window on 429", () => {
    expect(nextStreamBackoffMs(500, new Error("failed (429)"))).toBe(
      RATE_LIMIT_BACKOFF_MS,
    );
  });

  it("doubles ordinary failures up to the cap", () => {
    expect(nextStreamBackoffMs(500, new Error("boom"))).toBe(1_000);
    expect(nextStreamBackoffMs(4_000, new Error("boom"))).toBe(8_000);
    expect(nextStreamBackoffMs(8_000, new Error("boom"))).toBe(8_000);
  });

  it("never returns below the 500ms floor", () => {
    expect(nextStreamBackoffMs(0, new Error("boom"))).toBe(500);
  });
});
