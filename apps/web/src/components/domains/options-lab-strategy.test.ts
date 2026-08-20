import { describe, expect, it } from "vitest";

import {
  buildPayoffCurve,
  buildStrategyFromTemplate,
  legPayoffAtSpot,
  payoffExtremes,
  summarizeStrategy,
  totalPayoffAtSpot,
  type StrategyLeg,
} from "@/components/domains/options-lab-strategy";

const rows = [
  {
    strike: 24300,
    is_atm: true,
    ce: { symbol: "", ltp: 120, oi: 1, volume: 1, iv: 11, delta: 0.52 },
    pe: { symbol: "", ltp: 55, oi: 1, volume: 1, iv: 12, delta: -0.48 },
  },
  {
    strike: 24350,
    is_atm: false,
    ce: { symbol: "", ltp: 95, oi: 1, volume: 1, iv: 11, delta: 0.45 },
    pe: { symbol: "", ltp: 70, oi: 1, volume: 1, iv: 12, delta: -0.55 },
  },
  {
    strike: 24250,
    is_atm: false,
    ce: { symbol: "", ltp: 150, oi: 1, volume: 1, iv: 11, delta: 0.58 },
    pe: { symbol: "", ltp: 42, oi: 1, volume: 1, iv: 12, delta: -0.42 },
  },
];

describe("options lab strategy", () => {
  it("computes long call payoff at expiry", () => {
    const leg: StrategyLeg = {
      id: "1",
      side: "buy",
      type: "CE",
      strike: 24300,
      premium: 100,
      qty: 1,
      delta: 0.5,
    };
    expect(legPayoffAtSpot(leg, 24300)).toBe(-100);
    expect(legPayoffAtSpot(leg, 24400)).toBe(0);
    expect(legPayoffAtSpot(leg, 24500)).toBe(100);
  });

  it("builds long straddle from chain premiums", () => {
    const legs = buildStrategyFromTemplate("long_straddle", {
      atm: 24300,
      strikeStep: 50,
      rows,
    });
    expect(legs).toHaveLength(2);
    expect(legs[0].premium).toBe(120);
    expect(legs[1].premium).toBe(55);
  });

  it("finds breakevens for long straddle", () => {
    const legs = buildStrategyFromTemplate("long_straddle", {
      atm: 24300,
      strikeStep: 50,
      rows,
    });
    const points = buildPayoffCurve(legs, { spot: 24300, strikeStep: 50, wings: 10 });
    const summary = summarizeStrategy(legs, points);
    expect(summary.breakevens.length).toBeGreaterThanOrEqual(2);
    expect(summary.netPremium).toBeCloseTo(-175, 0);
  });

  it("bull call spread caps upside", () => {
    const legs = buildStrategyFromTemplate("bull_call_spread", {
      atm: 24300,
      strikeStep: 50,
      rows,
      widthSteps: 1,
    });
    const low = totalPayoffAtSpot(legs, 24000);
    const mid = totalPayoffAtSpot(legs, 24350);
    const high = totalPayoffAtSpot(legs, 25000);
    expect(high).toBeCloseTo(mid, 0);
    expect(mid).toBeGreaterThan(low);
  });

  it("short straddle reports unlimited overall max loss", () => {
    const legs = buildStrategyFromTemplate("short_straddle", {
      atm: 24300,
      strikeStep: 50,
      rows,
    });
    const { maxProfit, maxLoss } = payoffExtremes(legs);
    expect(maxLoss).toBeNull();
    expect(maxProfit).toBeCloseTo(175, 0);
  });

  it("short straddle downside (spot=0) is finite even if overall loss is unlimited", () => {
    const legs = buildStrategyFromTemplate("short_straddle", {
      atm: 24300,
      strikeStep: 50,
      rows,
    });
    expect(totalPayoffAtSpot(legs, 0)).toBeCloseTo(-24125, 0);
  });

  it("long straddle reports unlimited max profit", () => {
    const legs = buildStrategyFromTemplate("long_straddle", {
      atm: 24300,
      strikeStep: 50,
      rows,
    });
    const { maxProfit, maxLoss } = payoffExtremes(legs);
    expect(maxProfit).toBeNull();
    expect(maxLoss).toBeCloseTo(-175, 0);
  });

  it("short put has finite downside max loss at spot=0", () => {
    const legs = buildStrategyFromTemplate("short_pe", {
      atm: 24300,
      strikeStep: 50,
      rows,
    });
    const { maxProfit, maxLoss } = payoffExtremes(legs);
    expect(maxProfit).toBeCloseTo(55, 0);
    expect(maxLoss).toBeCloseTo(-24245, 0);
  });

  it("iron condor max profit uses inner plateau, not window edge", () => {
    const legs = buildStrategyFromTemplate("iron_condor", {
      atm: 24300,
      strikeStep: 50,
      rows,
      widthSteps: 1,
    });
    const { maxProfit } = payoffExtremes(legs);
    expect(maxProfit).not.toBeNull();
    expect(maxProfit).toBeGreaterThan(0);
  });

  it("samples payoff curve on strike grid so kinks are exact", () => {
    const legs = buildStrategyFromTemplate("bull_call_spread", {
      atm: 24300,
      strikeStep: 50,
      rows,
      widthSteps: 1,
    });
    const points = buildPayoffCurve(legs, { strikeStep: 50, wings: 4 });
    expect(points.some((p) => p.spot === 24300)).toBe(true);
    expect(points.some((p) => p.spot === 24350)).toBe(true);
    expect(points.every((p) => p.spot % 50 === 0)).toBe(true);
  });

  it("flags missing chain quotes instead of defaulting to ₹1", () => {
    const legs = buildStrategyFromTemplate("iron_condor", {
      atm: 24300,
      strikeStep: 50,
      rows,
      widthSteps: 2,
    });
    const missing = legs.filter((leg) => leg.quoteMissing);
    expect(missing.length).toBeGreaterThan(0);
    for (const leg of missing) {
      expect(leg.premium).toBe(0);
    }
  });
});
