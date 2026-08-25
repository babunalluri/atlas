import { describe, expect, it } from "vitest";

import {
  filterByCategory,
  toggleOverlay,
} from "./paramChartSeries";

/**
 * Mirrors backend PARAM_CHART_SHARED_CHECK_NOS — keep in sync with
 * apps/backend/src/app/domains/param_chart_constants.py
 */
const SHARED_CHECK_NOS = [
  1, 7, 8, 10, 15, 16, 17, 18, 19, 23, 26, 27, 33, 36, 41, 42, 44, 45, 48, 49,
  50, 51, 52, 57, 59, 61, 64, 68, 69, 70,
] as const;

describe("param chart shared list", () => {
  it("has 30 unique checklist numbers", () => {
    expect(new Set(SHARED_CHECK_NOS).size).toBe(30);
    expect(SHARED_CHECK_NOS).not.toContain(93);
  });

  it("filters params by category for the bottom panel", () => {
    const metrics = [
      { id: "a", category: "Data & Charts Watch", check_no: 1 },
      { id: "b", category: "Global Markets Watch", check_no: 61 },
      { id: "c", category: "Data & Charts Watch", check_no: 10 },
    ];
    expect(filterByCategory(metrics, "").map((m) => m.id)).toEqual([
      "a",
      "b",
      "c",
    ]);
    expect(
      filterByCategory(metrics, "Data & Charts Watch").map((m) => m.id),
    ).toEqual(["a", "c"]);
  });

  it("toggles multiple overlays for graph plotting", () => {
    let selected = ["total"] as ReturnType<typeof toggleOverlay>;
    selected = toggleOverlay(selected, "ce");
    expect(selected).toEqual(["total", "ce"]);
    selected = toggleOverlay(selected, "metric:atm");
    expect(selected).toEqual(["total", "ce", "metric:atm"]);
    selected = toggleOverlay(selected, "ce");
    expect(selected).toEqual(["total", "metric:atm"]);
    selected = toggleOverlay(selected, "close");
    expect(selected).toEqual([]);
  });
});
