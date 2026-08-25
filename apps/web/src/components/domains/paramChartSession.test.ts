import { describe, expect, it } from "vitest";

import {
  isNoTradeBand,
  isSessionOpenBar,
  pctFromWindowOpen,
} from "./paramChartSession";

describe("paramChartSession", () => {
  it("detects open / no-trade on 1m", () => {
    expect(isSessionOpenBar("2026-08-25T09:15:00", "1m")).toBe(true);
    expect(isNoTradeBand("2026-08-25T09:17:00", "1m")).toBe(true);
    expect(isNoTradeBand("2026-08-25T10:00:00", "1m")).toBe(false);
    expect(isNoTradeBand("2026-08-25T15:25:00", "1m")).toBe(true);
  });

  it("computes % from window open", () => {
    const closes = [100, 101, 102, null, 110];
    const pct = pctFromWindowOpen(closes, 0, 5);
    expect(pct[0]).toBe(0);
    expect(pct[1]).toBeCloseTo(1);
    expect(pct[4]).toBeCloseTo(10);
  });
});
