/** Pure merge for Param Chart SSE ``stream_patch`` frames. */

import type { ParamChartMonthSnapshot } from "@/lib/api/admin";

function packInterval(snap: ParamChartMonthSnapshot | null | undefined): string {
  return String(snap?.interval || snap?.config?.interval || "1D");
}

/**
 * Merge a lean SSE today-patch into a REST-loaded month pack.
 *
 * Silent corruption (wiped chart) is worse than a crash — every branch is
 * intentional. Keep this free of React so the five cases stay unit-testable.
 */
export function mergeMonthStreamPatch(
  prev: ParamChartMonthSnapshot | null,
  patch: ParamChartMonthSnapshot,
): ParamChartMonthSnapshot | null {
  const patchLive = {
    live_metrics: patch.live_metrics ?? prev?.live_metrics,
    kite_live: patch.kite_live ?? prev?.kite_live,
    metrics_by_day: {
      ...(prev?.metrics_by_day || {}),
      ...(patch.metrics_by_day || {}),
    },
    fetched_at: patch.fetched_at ?? prev?.fetched_at,
  };

  // 1) No prior hist — keep stub; REST fills the month.
  if (!prev?.days?.length) {
    return prev ? { ...prev, ...patchLive } : prev;
  }

  // 2) Different year / month / interval — never splice foreign today bars.
  const prevIv = packInterval(prev);
  const patchIv = packInterval(patch);
  if (
    prev.year !== patch.year ||
    prev.month !== patch.month ||
    prevIv !== patchIv
  ) {
    return { ...prev, ...patchLive };
  }

  // 3) Building or empty today list — do not wipe hist today bars.
  if (patch.building || !(patch.days?.length)) {
    return {
      ...prev,
      ...patchLive,
      building: Boolean(patch.building),
    };
  }

  // 4) Same pack with today bars — replace prior today slice, keep hist.
  const today = String(patch.today || prev.today || "");
  const hist = today
    ? prev.days.filter((d) => !String(d.date).startsWith(today))
    : prev.days;

  return {
    ...prev,
    ...patch,
    stream_patch: undefined,
    building: false,
    days: [...hist, ...(patch.days || [])],
    metrics_by_day: patchLive.metrics_by_day,
    live_metrics: patchLive.live_metrics,
    kite_live: patchLive.kite_live,
  };
}
