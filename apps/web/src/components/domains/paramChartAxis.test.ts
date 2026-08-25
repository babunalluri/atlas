import { describe, expect, it } from "vitest";

import {
  axisLabelStep,
  barAxisLabel,
  shouldShowAxisLabel,
} from "./paramChartAxis";

describe("paramChartAxis", () => {
  it("keeps sparse steps for dense 1H windows", () => {
    expect(axisLabelStep(6, "1H")).toBe(1);
    expect(axisLabelStep(60, "1H")).toBeGreaterThanOrEqual(6);
    const shown = Array.from({ length: 60 }, (_, i) =>
      shouldShowAxisLabel(i, 60, "1H"),
    ).filter(Boolean).length;
    expect(shown).toBeLessThanOrEqual(12);
    expect(shown).toBeGreaterThanOrEqual(6);
  });

  it("shortens 1H labels between day changes", () => {
    expect(barAxisLabel("2026-08-01T09:00", "1H")).toBe("1·09h");
    expect(
      barAxisLabel("2026-08-01T14:00", "1H", {
        prevDate: "2026-08-01T09:00",
      }),
    ).toBe("14h");
    expect(
      barAxisLabel("2026-08-02T10:00", "1H", {
        prevDate: "2026-08-01T15:00",
      }),
    ).toBe("2·10h");
  });

  it("formats 1m labels with HH:MM", () => {
    expect(barAxisLabel("2026-08-01T09:15", "1m")).toBe("1·09:15");
    expect(
      barAxisLabel("2026-08-01T14:30", "1m", {
        prevDate: "2026-08-01T09:15",
      }),
    ).toBe("14:30");
  });

  it("always labels focus + edges", () => {
    expect(shouldShowAxisLabel(3, 40, "1H", true)).toBe(true);
    expect(shouldShowAxisLabel(0, 40, "1H")).toBe(true);
    expect(shouldShowAxisLabel(39, 40, "1H")).toBe(true);
  });
});
