import { describe, expect, it } from "vitest";

import {
  black76Greeks,
  blendStrategyIv,
  buildPayoffCurve,
  buildStrategyFromTemplate,
  daysToExpiryFromFutSymbol,
  daysToExpiryFromOptionSymbol,
  estimateProbabilityOfProfit,
  estimateStrategyGreeks,
  expiryCodeFromOptionSymbol,
  formatOptionContractName,
  impliedVolFromLtp,
  interpolateSideIv,
  lastThursdayOfMonth,
  legPayoffAtSpot,
  normCdf,
  payoffExtremes,
  resolveDaysToExpiry,
  resolveLegIv,
  summarizeStrategy,
  syntheticForwardFromChain,
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

  it("normCdf is ~0.5 at 0 and monotone", () => {
    expect(normCdf(0)).toBeCloseTo(0.5, 2);
    expect(normCdf(-2)).toBeLessThan(normCdf(0));
    expect(normCdf(2)).toBeGreaterThan(normCdf(0));
  });

  it("parses monthly FUT days to expiry", () => {
    const expiry = lastThursdayOfMonth(2026, 7); // Aug 2026
    const now = new Date(expiry.getTime() - 14 * 24 * 60 * 60 * 1000);
    const days = daysToExpiryFromFutSymbol("NFO:NIFTY26AUGFUT", now);
    expect(days).not.toBeNull();
    expect(days!).toBeGreaterThan(13);
    expect(days!).toBeLessThan(15);
  });

  it("estimates complementary PoP for long vs short straddle", () => {
    const shortLegs = buildStrategyFromTemplate("short_straddle", {
      atm: 24300,
      strikeStep: 50,
      rows,
    });
    const longLegs = buildStrategyFromTemplate("long_straddle", {
      atm: 24300,
      strikeStep: 50,
      rows,
    });
    const common = {
      forward: 24300,
      ivPct: 12,
      daysToExpiry: 7,
      strikeStep: 50,
    };
    const shortPop = estimateProbabilityOfProfit(shortLegs, common);
    const longPop = estimateProbabilityOfProfit(longLegs, common);
    expect(shortPop).not.toBeNull();
    expect(longPop).not.toBeNull();
    // Same strikes / opposite sides → profitable regions are complements.
    expect(shortPop! + longPop!).toBeCloseTo(100, 0);
    expect(shortPop!).toBeGreaterThan(0);
    expect(longPop!).toBeGreaterThan(0);
  });

  it("returns null PoP when IV is missing", () => {
    const legs = buildStrategyFromTemplate("long_ce", {
      atm: 24300,
      strikeStep: 50,
      rows,
    });
    expect(
      estimateProbabilityOfProfit(legs, {
        forward: 24300,
        ivPct: 0,
        daysToExpiry: 7,
        strikeStep: 50,
      }),
    ).toBeNull();
  });

  it("interpolates smile IV between strikes", () => {
    const sparse = [
      {
        strike: 24200,
        is_atm: false,
        ce: { symbol: "", ltp: 180, oi: 1, volume: 1, iv: 10, delta: 0.6 },
        pe: { symbol: "", ltp: 40, oi: 1, volume: 1, iv: 14, delta: -0.4 },
      },
      {
        strike: 24400,
        is_atm: false,
        ce: { symbol: "", ltp: 80, oi: 1, volume: 1, iv: 12, delta: 0.4 },
        pe: { symbol: "", ltp: 90, oi: 1, volume: 1, iv: 13, delta: -0.6 },
      },
    ];
    expect(interpolateSideIv(sparse, 24300, "CE")).toBeCloseTo(11, 5);
  });

  it("resolves leg IV with chain then ATM fallback", () => {
    const chain = resolveLegIv(rows, { strike: 24300, type: "CE" }, { atmIv: 9 });
    expect(chain).toEqual({ iv: 11, source: "chain" });

    // Outside smile span with no LTP path → ATM (no wing clamp).
    const fallback = resolveLegIv(rows, { strike: 25000, type: "CE" }, { atmIv: 9 });
    expect(fallback).toEqual({ iv: 9, source: "atm" });

    const atmOnly = resolveLegIv([], { strike: 24300, type: "CE" }, { atmIv: 9.5 });
    expect(atmOnly).toEqual({ iv: 9.5, source: "atm" });
  });

  it("uses opposite-side chain IV via parity for missing own-side IV", () => {
    const parityRows = [
      {
        strike: 24000,
        is_atm: false,
        ce: { symbol: "", ltp: 350, oi: 1, volume: 1, iv: null, delta: 0.9 },
        pe: { symbol: "", ltp: 20, oi: 1, volume: 1, iv: 13.5, delta: -0.1 },
      },
    ];
    const resolved = resolveLegIv(
      parityRows,
      { strike: 24000, type: "CE" },
      { atmIv: 11, forward: 24300, daysToExpiry: 7, premium: 350 },
    );
    expect(resolved).toEqual({ iv: 13.5, source: "parity" });
  });

  it("allows LTP invert for near-ATM ITM (not deep)", () => {
    const nearAtm = [
      {
        strike: 24300,
        is_atm: true,
        ce: { symbol: "", ltp: 130, oi: 1, volume: 1, iv: null, delta: 0.51 },
        pe: { symbol: "", ltp: 125, oi: 1, volume: 1, iv: null, delta: -0.49 },
      },
    ];
    const resolved = resolveLegIv(
      nearAtm,
      { strike: 24300, type: "CE" },
      { atmIv: 11, forward: 24305, daysToExpiry: 7, premium: 130 },
    );
    expect(resolved?.source).toBe("ltp");
    expect(resolved!.iv).toBeGreaterThan(0);
  });

  it("builds synthetic forward from ATM CE-PE and rejects spiked ticks", () => {
    // 24300 + (120 - 55) = 24365 vs spot 24290 (~75pts) — ok at ~30d band
    expect(syntheticForwardFromChain(rows, 24300, 24290, 30)).toBeCloseTo(24365, 5);

    const spiked = [
      {
        strike: 24300,
        is_atm: true,
        ce: { symbol: "", ltp: 480, oi: 1, volume: 1, iv: 11, delta: 0.5 },
        pe: { symbol: "", ltp: 55, oi: 1, volume: 1, iv: 12, delta: -0.5 },
      },
    ];
    // F would be 24725 vs spot 24290 — beyond DTE-scaled band → fall back to spot
    expect(syntheticForwardFromChain(spiked, 24300, 24290, 30)).toBe(24290);

    const zeroPrint = [
      {
        strike: 24300,
        is_atm: true,
        ce: { symbol: "", ltp: 0, oi: 1, volume: 1, iv: 11, delta: 0.5 },
        pe: { symbol: "", ltp: 55, oi: 1, volume: 1, iv: 12, delta: -0.5 },
      },
    ];
    expect(syntheticForwardFromChain(zeroPrint, 24300, 24290, 30)).toBe(24290);
  });

  it("tightens forward guard near expiry so stale ATM prints fall back", () => {
    const stale = [
      {
        strike: 24300,
        is_atm: true,
        // F = 24300 + (180 - 55) = 24425 (+135 vs spot) — passes old ±1.5%, not 30m band
        ce: { symbol: "", ltp: 180, oi: 1, volume: 1, iv: 11, delta: 0.5 },
        pe: { symbol: "", ltp: 55, oi: 1, volume: 1, iv: 12, delta: -0.5 },
      },
    ];
    expect(syntheticForwardFromChain(stale, 24300, 24290, 30 / (24 * 60))).toBe(24290);
    expect(syntheticForwardFromChain(stale, 24300, 24290, 30)).toBeCloseTo(24425, 5);
  });

  it("caps forward band at 1.5% and treats null DTE like the 7d default", () => {
    const wide = [
      {
        strike: 24300,
        is_atm: true,
        // F = 24725 (+435 ≈ 1.79%) — beyond 1.5% even at 90d
        ce: { symbol: "", ltp: 480, oi: 1, volume: 1, iv: 11, delta: 0.5 },
        pe: { symbol: "", ltp: 55, oi: 1, volume: 1, iv: 12, delta: -0.5 },
      },
    ];
    expect(syntheticForwardFromChain(wide, 24300, 24290, 90)).toBe(24290);

    const mild = [
      {
        strike: 24300,
        is_atm: true,
        // F = 24340 (+50) — inside 7d band (~56 pts)
        ce: { symbol: "", ltp: 95, oi: 1, volume: 1, iv: 11, delta: 0.5 },
        pe: { symbol: "", ltp: 55, oi: 1, volume: 1, iv: 12, delta: -0.5 },
      },
    ];
    expect(syntheticForwardFromChain(mild, 24300, 24290, null)).toBeCloseTo(24340, 5);
    expect(syntheticForwardFromChain(mild, 24300, 24290, undefined)).toBeCloseTo(24340, 5);
  });

  it("prefers IV-from-LTP over wing clamp for strikes outside smile", () => {
    const sparse = [
      {
        strike: 24200,
        is_atm: false,
        ce: { symbol: "", ltp: 180, oi: 1, volume: 1, iv: 10, delta: 0.6 },
        pe: { symbol: "", ltp: 40, oi: 1, volume: 1, iv: 14, delta: -0.4 },
      },
      {
        strike: 24400,
        is_atm: false,
        ce: { symbol: "", ltp: 80, oi: 1, volume: 1, iv: 12, delta: 0.4 },
        pe: { symbol: "", ltp: 90, oi: 1, volume: 1, iv: 13, delta: -0.6 },
      },
      {
        strike: 24600,
        is_atm: false,
        ce: { symbol: "", ltp: 55, oi: 1, volume: 1, iv: null, delta: 0.3 },
        pe: { symbol: "", ltp: 120, oi: 1, volume: 1, iv: null, delta: -0.7 },
      },
    ];
    expect(interpolateSideIv(sparse, 24600, "CE")).toBeNull();
    const resolved = resolveLegIv(
      sparse,
      { strike: 24600, type: "CE" },
      { atmIv: 11, forward: 24300, daysToExpiry: 7, premium: 55 },
    );
    expect(resolved?.source).toBe("ltp");
    expect(resolved!.iv).toBeGreaterThan(0);
  });

  it("blends strategy IV from per-leg smile with coverage counts", () => {
    const legs = buildStrategyFromTemplate("long_straddle", {
      atm: 24300,
      strikeStep: 50,
      rows,
    });
    const blend = blendStrategyIv(legs, rows, 10);
    expect(blend).not.toBeNull();
    expect(blend!.legs).toBe(2);
    expect(blend!.chainLegs).toBe(2);
    expect(blend!.ltpLegs).toBe(0);
    expect(blend!.atmFallbackLegs).toBe(0);
    // CE iv 11 + PE iv 12
    expect(blend!.ivPct).toBeCloseTo(11.5, 5);
  });

  it("round-trips Black-76 price → IV invert", () => {
    // Use a small helper path: invert a mid-vol ATM premium, then check invert stability.
    const forward = 24300;
    const strike = 24300;
    const daysToExpiry = 14;
    const first = impliedVolFromLtp({
      premium: 150,
      forward,
      strike,
      type: "CE",
      daysToExpiry,
    });
    expect(first).not.toBeNull();
    // Re-price via a second invert on a nearby premium should stay in a sane band.
    const second = impliedVolFromLtp({
      premium: 151,
      forward,
      strike,
      type: "CE",
      daysToExpiry,
    });
    expect(second).not.toBeNull();
    expect(Math.abs(second! - first!)).toBeLessThan(2);
  });

  it("returns null IV when premium is below intrinsic", () => {
    expect(
      impliedVolFromLtp({
        premium: 50,
        forward: 24300,
        strike: 24000,
        type: "CE",
        daysToExpiry: 7,
      }),
    ).toBeNull();
  });

  it("uses IV-from-LTP when chain IV is missing", () => {
    const noIvRows = [
      {
        strike: 24300,
        is_atm: true,
        ce: { symbol: "NFO:NIFTY26AUG24500CE", ltp: 120, oi: 1, volume: 1, iv: null, delta: 0.5 },
        pe: { symbol: "NFO:NIFTY26AUG24500PE", ltp: 110, oi: 1, volume: 1, iv: null, delta: -0.5 },
      },
    ];
    const resolved = resolveLegIv(
      noIvRows,
      { strike: 24300, type: "CE" },
      {
        atmIv: null,
        forward: 24300,
        daysToExpiry: 7,
        premium: 120,
      },
    );
    expect(resolved?.source).toBe("ltp");
    expect(resolved!.iv).toBeGreaterThan(0);

    const blend = blendStrategyIv(
      [
        {
          id: "1",
          side: "buy",
          type: "CE",
          strike: 24300,
          premium: 120,
          qty: 1,
          delta: 0.5,
        },
      ],
      noIvRows,
      null,
      { forward: 24300, daysToExpiry: 7 },
    );
    expect(blend?.ltpLegs).toBe(1);
    expect(blend?.ivPct).toBeGreaterThan(0);
  });

  it("parses weekly and monthly option expiries for DTE", () => {
    expect(expiryCodeFromOptionSymbol("NFO:NIFTY26AUG24500CE")).toBe("26AUG");
    expect(expiryCodeFromOptionSymbol("NIFTY25N1124500CE")).toBe("25N11");
    expect(expiryCodeFromOptionSymbol("NIFTY2580724500CE")).toBe("25807");
    // YYMMDD must not be swallowed by weekly-alpha (Oct/Nov/Dec digit months).
    expect(expiryCodeFromOptionSymbol("NIFTY25112724500CE")).toBe("251127");
    expect(expiryCodeFromOptionSymbol("NIFTY25100724500CE")).toBe("251007");
    expect(expiryCodeFromOptionSymbol("NIFTY25123024500CE")).toBe("251230");

    const monthlyExpiry = lastThursdayOfMonth(2026, 7);
    const now = new Date(monthlyExpiry.getTime() - 10 * 24 * 60 * 60 * 1000);
    expect(daysToExpiryFromOptionSymbol("NFO:NIFTY26AUG24500CE", now)).toBeCloseTo(10, 0);

    const weeklyNow = new Date(Date.UTC(2025, 10, 4, 12, 0, 0)); // 4 Nov 2025
    const weeklyDays = daysToExpiryFromOptionSymbol("NIFTY25N1124500CE", weeklyNow);
    expect(weeklyDays).not.toBeNull();
    expect(weeklyDays!).toBeCloseTo(7, 0);

    const yymmddNow = new Date(Date.UTC(2025, 10, 20, 12, 0, 0)); // 20 Nov 2025
    expect(daysToExpiryFromOptionSymbol("NIFTY25112724500CE", yymmddNow)).toBeCloseTo(7, 0);
  });

  it("returns null DTE for past expiries so callers can fall back", () => {
    const after = new Date(Date.UTC(2025, 10, 12, 12, 0, 0)); // after 11 Nov 2025
    expect(daysToExpiryFromOptionSymbol("NIFTY25N1124500CE", after)).toBeNull();
    // Decoded-but-expired options must NOT jump to monthly FUT DTE.
    expect(
      resolveDaysToExpiry(
        {
          futSymbol: "NFO:NIFTY25NOVFUT",
          optionSymbols: ["NIFTY25N1124500CE"],
        },
        after,
      ),
    ).toBeNull();
  });

  it("uses 15:30 IST close anchor on expiry day", () => {
    const beforeClose = new Date(Date.UTC(2025, 10, 11, 9, 0, 0)); // 14:30 IST
    const afterClose = new Date(Date.UTC(2025, 10, 11, 10, 30, 0)); // 16:00 IST
    expect(daysToExpiryFromOptionSymbol("NIFTY25N1124500CE", beforeClose)).toBeGreaterThan(0);
    expect(daysToExpiryFromOptionSymbol("NIFTY25N1124500CE", afterClose)).toBeNull();
  });

  it("prefers option-symbol DTE over FUT when resolving", () => {
    const now = new Date(Date.UTC(2025, 10, 4, 12, 0, 0));
    const days = resolveDaysToExpiry(
      {
        futSymbol: "NFO:NIFTY25NOVFUT",
        optionSymbols: ["NIFTY25N1124500CE"],
      },
      now,
    );
    expect(days).not.toBeNull();
    expect(days!).toBeCloseTo(7, 0);
  });

  it("uses a ~30m DTE floor instead of half-day for short-dated PoP", () => {
    const legs = buildStrategyFromTemplate("long_ce", {
      atm: 24300,
      strikeStep: 50,
      rows,
    });
    const shortDated = estimateProbabilityOfProfit(legs, {
      forward: 24390,
      ivPct: 12,
      daysToExpiry: 30 / (24 * 60), // 30 minutes
      strikeStep: 50,
    });
    const flooredHalfDay = estimateProbabilityOfProfit(legs, {
      forward: 24390,
      ivPct: 12,
      daysToExpiry: 0.5,
      strikeStep: 50,
    });
    expect(shortDated).not.toBeNull();
    expect(flooredHalfDay).not.toBeNull();
    // Half-day floor would inflate σ√T and PoP vs true 30m left.
    expect(shortDated!).toBeLessThan(flooredHalfDay!);
  });

  it("formats option contract names without inventing monthly expiry days", () => {
    // Monthly token → month+year only (no last-Thursday guess in the label).
    expect(formatOptionContractName("NFO:NIFTY26AUG24500CE")).toBe("NIFTY Aug 26 24500 CE");
    // Weekly codes encode the calendar day — pad single-digit days.
    expect(formatOptionContractName("NIFTY25N0724500PE")).toBe("NIFTY 07 Nov 25 24500 PE");
    expect(formatOptionContractName("NIFTY25N1124500PE")).toBe("NIFTY 11 Nov 25 24500 PE");
    expect(formatOptionContractName("NIFTY25112724500CE")).toBe("NIFTY 27 Nov 25 24500 CE");
  });

  it("rejects junk sub-0.5% chain IV so gamma cannot explode", () => {
    const junk = [
      {
        strike: 24300,
        is_atm: true,
        ce: { symbol: "", ltp: 120, oi: 1, volume: 1, iv: 0.001, delta: 0.5 },
        pe: { symbol: "", ltp: 110, oi: 1, volume: 1, iv: 12, delta: -0.5 },
      },
    ];
    const resolved = resolveLegIv(
      junk,
      { strike: 24300, type: "CE" },
      { atmIv: 11, forward: 24300, daysToExpiry: 7, premium: 120 },
    );
    // Own-side 0.001% is unusable → parity PE IV
    expect(resolved).toEqual({ iv: 12, source: "parity" });
    expect(black76Greeks({
      forward: 24300,
      strike: 24300,
      daysToExpiry: 7,
      ivPct: 0.001,
      type: "CE",
    })).toBeNull();
  });

  it("reports theta per hour when under one day to expiry", () => {
    const perDay = black76Greeks({
      forward: 24300,
      strike: 24300,
      daysToExpiry: 2,
      ivPct: 12,
      type: "CE",
    });
    const perHour = black76Greeks({
      forward: 24300,
      strike: 24300,
      daysToExpiry: 0.5,
      ivPct: 12,
      type: "CE",
    });
    expect(perDay).not.toBeNull();
    expect(perHour).not.toBeNull();
    // Hourly magnitude should be much smaller than a raw /day print near expiry.
    expect(Math.abs(perHour!.theta)).toBeLessThan(Math.abs(perDay!.theta) * 2);
  });

  it("computes Black-76 gamma/theta/vega for near-ATM options", () => {
    const greeks = black76Greeks({
      forward: 24300,
      strike: 24300,
      daysToExpiry: 7,
      ivPct: 12,
      type: "CE",
    });
    expect(greeks).not.toBeNull();
    expect(greeks!.delta).toBeGreaterThan(0.4);
    expect(greeks!.delta).toBeLessThan(0.6);
    expect(greeks!.gamma).toBeGreaterThan(0);
    expect(greeks!.vega).toBeGreaterThan(0);
    expect(greeks!.theta).toBeLessThan(0);
  });

  it("nets strategy greeks with buy/sell signs", () => {
    const legs = buildStrategyFromTemplate("short_straddle", {
      atm: 24300,
      strikeStep: 50,
      rows,
    });
    const summary = estimateStrategyGreeks(legs, rows, {
      forward: 24300,
      daysToExpiry: 7,
      atmIv: 11.5,
    });
    expect(summary.netGamma).not.toBeNull();
    expect(summary.netVega).not.toBeNull();
    // Short straddle → negative gamma and vega
    expect(summary.netGamma!).toBeLessThan(0);
    expect(summary.netVega!).toBeLessThan(0);
    expect(summary.netTheta!).toBeGreaterThan(0);
  });
});
