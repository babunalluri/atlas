import { describe, expect, it } from "vitest";

import {
  isMetricSeries,
  isPriceOverlay,
  partitionOverlays,
  toggleOverlay,
} from "./paramChartSeries";

describe("paramChartSeries panes", () => {
  it("classifies metric vs price overlays", () => {
    expect(isMetricSeries("metric:pcr")).toBe(true);
    expect(isMetricSeries("total")).toBe(false);
    expect(isPriceOverlay("ce")).toBe(true);
    expect(isPriceOverlay("metric:pcr")).toBe(false);
  });

  it("partitions overlays for dual panes", () => {
    const { price, metrics } = partitionOverlays([
      "total",
      "metric:pcr",
      "ce",
      "metric:oi",
    ]);
    expect(price).toEqual(["total", "ce"]);
    expect(metrics).toEqual(["metric:pcr", "metric:oi"]);
  });

  it("toggle still caps overlays", () => {
    let cur = toggleOverlay([], "total");
    cur = toggleOverlay(cur, "metric:a");
    expect(cur).toEqual(["total", "metric:a"]);
  });
});
