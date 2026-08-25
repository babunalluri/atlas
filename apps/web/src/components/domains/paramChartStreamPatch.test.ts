import { describe, expect, it } from "vitest";

import type { ParamChartDay, ParamChartMonthSnapshot } from "@/lib/api/admin";

import { mergeMonthStreamPatch } from "./paramChartStreamPatch";

function day(date: string, close = 1): ParamChartDay {
  return {
    date,
    day_index: 1,
    weekday: "Mon",
    open: close,
    high: close,
    low: close,
    close,
    ce: null,
    pe: null,
    total: null,
    pct_vs_entry: null,
    metrics: {},
  };
}

function basePack(
  overrides: Partial<ParamChartMonthSnapshot> = {},
): ParamChartMonthSnapshot {
  return {
    ok: true,
    year: 2026,
    month: 8,
    interval: "1m",
    days: [
      day("2026-08-24T15:29", 100),
      day("2026-08-25T09:15", 101),
      day("2026-08-25T09:16", 102),
    ],
    today: "2026-08-25",
    metrics_by_day: {
      "2026-08-24": { chk_008: { id: "chk_008", value: 1.1 } },
    },
    ...overrides,
  };
}

describe("mergeMonthStreamPatch", () => {
  it("keeps stub when prev has no days", () => {
    const prev = basePack({ days: [] });
    const next = mergeMonthStreamPatch(prev, {
      ...basePack(),
      stream_patch: true,
      days: [day("2026-08-25T10:00", 110)],
      live_metrics: { chk_008: { id: "chk_008", value: 1.5 } },
    });
    expect(next?.days).toEqual([]);
    expect(next?.live_metrics?.chk_008?.value).toBe(1.5);
  });

  it("returns null when prev is null", () => {
    expect(
      mergeMonthStreamPatch(null, {
        ...basePack(),
        stream_patch: true,
      }),
    ).toBeNull();
  });

  it("does not splice foreign interval into hist", () => {
    const prev = basePack({ interval: "1D" });
    const next = mergeMonthStreamPatch(prev, {
      ...basePack(),
      stream_patch: true,
      interval: "1m",
      days: [day("2026-08-25T10:00", 999)],
      live_metrics: { chk_008: { id: "chk_008", value: 2 } },
    });
    expect(next?.days).toEqual(prev.days);
    expect(next?.interval).toBe("1D");
    expect(next?.live_metrics?.chk_008?.value).toBe(2);
  });

  it("does not wipe today bars on building / empty patch", () => {
    const prev = basePack();
    const building = mergeMonthStreamPatch(prev, {
      ...basePack(),
      stream_patch: true,
      building: true,
      days: [],
      live_metrics: { chk_008: { id: "chk_008", value: 1.7 } },
    });
    expect(building?.days).toEqual(prev.days);
    expect(building?.building).toBe(true);
    expect(building?.live_metrics?.chk_008?.value).toBe(1.7);

    const empty = mergeMonthStreamPatch(prev, {
      ...basePack(),
      stream_patch: true,
      building: false,
      days: [],
    });
    expect(empty?.days).toEqual(prev.days);
  });

  it("replaces today slice and keeps prior hist", () => {
    const prev = basePack();
    const next = mergeMonthStreamPatch(prev, {
      ...basePack(),
      stream_patch: true,
      days: [day("2026-08-25T15:29", 200)],
      live_metrics: { chk_008: { id: "chk_008", value: 1.9 } },
      metrics_by_day: {
        "2026-08-25": { chk_008: { id: "chk_008", value: 1.9 } },
      },
    });
    expect(next?.days.map((d) => d.date)).toEqual([
      "2026-08-24T15:29",
      "2026-08-25T15:29",
    ]);
    expect(next?.days[1]?.close).toBe(200);
    expect(next?.stream_patch).toBeUndefined();
    expect(next?.building).toBe(false);
    expect(next?.metrics_by_day?.["2026-08-24"]?.chk_008?.value).toBe(1.1);
    expect(next?.metrics_by_day?.["2026-08-25"]?.chk_008?.value).toBe(1.9);
  });
});
