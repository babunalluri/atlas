import { describe, expect, it } from "vitest";

import {
  clampView,
  ensureIndexVisible,
  panView,
  zoomView,
} from "./paramChartViewport";

describe("paramChartViewport", () => {
  it("clamps window inside series length", () => {
    expect(clampView(-3, 10, 20)).toEqual({ start: 0, count: 10 });
    expect(clampView(15, 10, 20)).toEqual({ start: 10, count: 10 });
    expect(clampView(0, 99, 20)).toEqual({ start: 0, count: 20 });
  });

  it("zooms in toward an anchor bar", () => {
    const next = zoomView(0, 20, 20, 10, 0.5, 5);
    expect(next.count).toBe(10);
    expect(next.start).toBeGreaterThanOrEqual(0);
    expect(next.start + next.count).toBeLessThanOrEqual(20);
    // Anchor ~10 should stay inside the new window.
    expect(next.start).toBeLessThanOrEqual(10);
    expect(next.start + next.count).toBeGreaterThan(10);
  });

  it("pans by bar delta", () => {
    expect(panView(0, 10, 30, 5)).toEqual({ start: 5, count: 10 });
    expect(panView(0, 10, 30, -3)).toEqual({ start: 0, count: 10 });
  });

  it("ensures selected index stays visible", () => {
    expect(ensureIndexVisible(5, 10, 40, 3)).toEqual({ start: 3, count: 10 });
    expect(ensureIndexVisible(5, 10, 40, 20)).toEqual({ start: 11, count: 10 });
    expect(ensureIndexVisible(5, 10, 40, 8)).toEqual({ start: 5, count: 10 });
  });
});
