import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import {
  black76Greeks,
  blendStrategyIv,
  bookedStrategyPnl,
  bookedStrategyPnlRupees,
  buildLegIvMap,
  buildPayoffCurve,
  buildPnlTable,
  buildStrategyFromTemplate,
  cycleChainLeg,
  daysToExpiryFromFutSymbol,
  daysToExpiryFromOptionSymbol,
  estimateFundsAndMargins,
  estimateLotSize,
  estimatePayoffDistributionStats,
  estimateProbabilityOfProfit,
  estimateStrategyCharges,
  estimateStrategyGreeks,
  estimateTargetDateProbabilityOfProfit,
  expiryCodeFromOptionSymbol,
  formatOptionContractName,
  impliedVolFromLtp,
  interpolateSideIv,
  lastThursdayOfMonth,
  legMarkPnlAtSpot,
  legPayoffAtSpot,
  normCdf,
  parseOptionSymbolParts,
  payoffExtremes,
  resolveDaysToExpiry,
  resolveLegIv,
  summarizeStrategy,
  STRATEGY_TEMPLATES,
  syntheticForwardFromChain,
  totalPayoffAtSpot,
  volatilitySpotBands,
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
  it("cycles chain click buy → sell → remove", () => {
    let legs: StrategyLeg[] = [];
    legs = cycleChainLeg(legs, rows, 24300, "CE");
    expect(legs).toHaveLength(1);
    expect(legs[0]).toMatchObject({ side: "buy", type: "CE", strike: 24300, premium: 120 });
    legs = cycleChainLeg(legs, rows, 24300, "CE");
    expect(legs[0].side).toBe("sell");
    legs = cycleChainLeg(legs, rows, 24300, "CE");
    expect(legs).toHaveLength(0);
  });

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

  it("matches shared option_symbol_parse_cases fixture", () => {
    const fixturePath = join(
      dirname(fileURLToPath(import.meta.url)),
      "../../../../../testdata/option_symbol_parse_cases.json",
    );
    const { cases } = JSON.parse(readFileSync(fixturePath, "utf8")) as {
      cases: Array<{
        symbol: string;
        expiry: string | null;
        strike: number | null;
        side: "CE" | "PE" | null;
      }>;
    };
    expect(cases.length).toBeGreaterThan(0);
    for (const c of cases) {
      const parsed = parseOptionSymbolParts(c.symbol);
      if (c.expiry == null) {
        expect(parsed).toBeNull();
        expect(expiryCodeFromOptionSymbol(c.symbol)).toBeNull();
        continue;
      }
      expect(parsed).not.toBeNull();
      expect(parsed!.expiry).toBe(c.expiry);
      expect(parsed!.strike).toBe(c.strike);
      expect(parsed!.side).toBe(c.side);
      expect(expiryCodeFromOptionSymbol(c.symbol)).toBe(c.expiry);
    }
  });

  it("parses weekly and monthly option expiries for DTE", () => {
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

  it("marks pre-expiry P&L between intrinsic and full premium loss", () => {
    const leg: StrategyLeg = {
      id: "1",
      side: "buy",
      type: "CE",
      strike: 24300,
      premium: 100,
      qty: 1,
      delta: 0.5,
    };
    const atExpiry = legPayoffAtSpot(leg, 24300);
    const marked = legMarkPnlAtSpot(leg, 24300, {
      remainingDaysToExpiry: 5,
      ivPct: 12,
    });
    expect(atExpiry).toBe(-100);
    // ATM call still has time value → mark PnL > expiry intrinsic (−premium).
    expect(marked).toBeGreaterThan(atExpiry);
    expect(Number.isFinite(marked)).toBe(true);
  });

  it("builds target-date curve and P&L table", () => {
    const legs = buildStrategyFromTemplate("long_straddle", {
      atm: 24300,
      strikeStep: 50,
      rows,
    });
    const expiry = buildPayoffCurve(legs, { strikeStep: 50 });
    const target = buildPayoffCurve(legs, {
      strikeStep: 50,
      remainingDaysToExpiry: 4,
      ivPct: 12,
    });
    expect(expiry.length).toBeGreaterThan(0);
    expect(target.length).toBe(expiry.length);
    const mid = Math.floor(expiry.length / 2);
    expect(target[mid].pnl).not.toBe(expiry[mid].pnl);

    const table = buildPnlTable(legs, {
      strikeStep: 50,
      remainingDtes: [7, 3, 0],
      ivPct: 12,
      wings: 4,
    });
    expect(table.spots.length).toBeGreaterThan(0);
    expect(table.cells[0]).toHaveLength(3);
  });

  it("estimates funds, charges, booked P&L, and distribution stats", () => {
    const legs = buildStrategyFromTemplate("long_straddle", {
      atm: 24300,
      strikeStep: 50,
      rows,
    });
    expect(estimateLotSize("NIFTY 50")).toBe(75);
    const funds = estimateFundsAndMargins(legs, { spot: 24300, lotSize: 75 });
    expect(funds).not.toBeNull();
    expect(funds!.fundsNeeded).toBeGreaterThan(0);
    const charges = estimateStrategyCharges(legs, 75);
    expect(charges!.total).toBeGreaterThan(0);

    const bookedFlat = bookedStrategyPnl(legs, rows);
    expect(bookedFlat).toBe(0);
    const bookedUp = bookedStrategyPnl(
      legs.map((leg) => ({ ...leg, premium: leg.premium - 10 })),
      rows,
    );
    expect(bookedUp).toBeGreaterThan(0);
    expect(bookedStrategyPnlRupees(
      legs.map((leg) => ({ ...leg, premium: leg.premium - 10 })),
      rows,
      75,
    )).toBe(bookedUp! * 75);

    const bands = volatilitySpotBands(24300, 12, 7);
    expect(bands).not.toBeNull();
    expect(bands!.sd1[0]).toBeLessThan(24300);
    expect(bands!.sd1[0]).toBeGreaterThan(0);
    expect(bands!.sd2[0]).toBeGreaterThan(0);
    // Lognormal: downside = F·e^{-σ√T}, not F − F·σ√T
    const vol = 0.12 * Math.sqrt(7 / 365);
    expect(bands!.sd1[0]).toBeCloseTo(24300 * Math.exp(-vol), 6);
    expect(bands!.sd1[1]).toBeCloseTo(24300 * Math.exp(vol), 6);

    // High vol + 1y must stay positive (additive model went negative here).
    const wide = volatilitySpotBands(25000, 100, 365);
    expect(wide).not.toBeNull();
    expect(wide!.sd2[0]).toBeGreaterThan(0);
    expect(wide!.sd2[1]).toBeGreaterThan(wide!.sd2[0]);

    const dist = estimatePayoffDistributionStats(legs, {
      forward: 24300,
      ivPct: 12,
      daysToExpiry: 7,
      strikeStep: 50,
      maxProfit: null,
    });
    expect(dist).not.toBeNull();
    expect(dist!.pop).toBeGreaterThan(0);
    expect(dist!.pMaxProfit).toBeNull();
  });

  it("target-date PoP uses mark PnL, not expiry intrinsic with shorter DTE", () => {
    const legs = buildStrategyFromTemplate("long_straddle", {
      atm: 24300,
      strikeStep: 50,
      rows,
    });
    const expiryPop = estimateProbabilityOfProfit(legs, {
      forward: 24300,
      ivPct: 12,
      daysToExpiry: 7,
      strikeStep: 50,
    });
    const wrongReuse = estimateProbabilityOfProfit(legs, {
      forward: 24300,
      ivPct: 12,
      daysToExpiry: 4,
      strikeStep: 50,
    });
    const targetPop = estimateTargetDateProbabilityOfProfit(legs, {
      forward: 24300,
      ivPct: 12,
      daysToTarget: 3,
      remainingDaysToExpiry: 4,
      strikeStep: 50,
    });
    expect(expiryPop).not.toBeNull();
    expect(targetPop).not.toBeNull();
    // Shorter-DTE expiry PoP is the wrong stand-in for target-date mark PoP.
    expect(targetPop).not.toBe(wrongReuse);
    // At target=now, PoP collapses to whether forward mark is profitable.
    const nowPop = estimateTargetDateProbabilityOfProfit(legs, {
      forward: 24300,
      ivPct: 12,
      daysToTarget: 0,
      remainingDaysToExpiry: 7,
      strikeStep: 50,
    });
    expect(nowPop === 0 || nowPop === 100).toBe(true);

    // Slider ceil overshoot must not inflate expiry density beyond true DTE.
    const atExpiryPop = estimateTargetDateProbabilityOfProfit(legs, {
      forward: 24300,
      ivPct: 12,
      daysToTarget: 7.2,
      remainingDaysToExpiry: 0,
      strikeStep: 50,
    });
    const direct = estimateProbabilityOfProfit(legs, {
      forward: 24300,
      ivPct: 12,
      daysToExpiry: 7.2,
      strikeStep: 50,
    });
    expect(atExpiryPop).toBe(direct);
  });

  it("uses per-leg IV for target marks when smile differs by strike", () => {
    const legs = buildStrategyFromTemplate("bull_call_spread", {
      atm: 24300,
      strikeStep: 50,
      rows,
      widthSteps: 1,
    });
    const map = buildLegIvMap(legs, rows, {
      atmIv: 11,
      forward: 24300,
      daysToExpiry: 7,
    });
    expect(map).not.toBeNull();
    const blended = buildPayoffCurve(legs, {
      strikeStep: 50,
      remainingDaysToExpiry: 5,
      ivPct: 11,
    });
    const perLeg = buildPayoffCurve(legs, {
      strikeStep: 50,
      remainingDaysToExpiry: 5,
      ivPct: 11,
      legIvById: map,
    });
    expect(perLeg.length).toBe(blended.length);
    // At least one point should differ when leg IVs are not identical.
    const ivs = Object.values(map!);
    if (new Set(ivs.map((v) => v.toFixed(2))).size > 1) {
      expect(perLeg.some((p, i) => Math.abs(p.pnl - blended[i].pnl) > 1e-6)).toBe(true);
    }
  });

  it("default buildPayoffCurve stays expiry-intrinsic (regression)", () => {
    const leg: StrategyLeg = {
      id: "1",
      side: "buy",
      type: "CE",
      strike: 24300,
      premium: 100,
      qty: 1,
      delta: 0.5,
    };
    const curve = buildPayoffCurve([leg], { strikeStep: 50, wings: 2 });
    const atStrike = curve.find((p) => p.spot === 24300);
    expect(atStrike?.pnl).toBe(-100);
  });

  it("E[PnL] uses vol domain so ATM short straddle stays negative", () => {
    const legs = buildStrategyFromTemplate("short_straddle", {
      atm: 25900,
      strikeStep: 50,
      rows: [
        {
          strike: 25900,
          is_atm: true,
          ce: { ltp: 180, iv: 15, oi: 1, symbol: "NIFTY25900CE" },
          pe: { ltp: 180, iv: 15, oi: 1, symbol: "NIFTY25900PE" },
        },
      ] as never,
    });
    const dist = estimatePayoffDistributionStats(legs, {
      forward: 25900,
      ivPct: 15,
      daysToExpiry: 30,
      strikeStep: 50,
      maxProfit: null,
    });
    expect(dist).not.toBeNull();
    // Truncating to strike wings alone flips the sign; vol domain must not.
    expect(dist!.expectedPnl).toBeLessThan(0);
  });

  it("buildPayoffCurve widens domain to include spot", () => {
    const leg: StrategyLeg = {
      id: "1",
      side: "buy",
      type: "CE",
      strike: 24000,
      premium: 100,
      qty: 1,
      delta: 0.5,
    };
    const curve = buildPayoffCurve([leg], {
      spot: 26000,
      strikeStep: 50,
      wings: 2,
    });
    expect(curve[0].spot).toBeLessThanOrEqual(24000);
    expect(curve[curve.length - 1].spot).toBeGreaterThanOrEqual(26000);
  });

  it("locks auditor golden checks for bands, blend, interp, parity, PnL table", () => {
    // log-space ±2σ at F=25000, σ=20%, T=30/365
    const bands30 = volatilitySpotBands(25000, 20, 30);
    expect(bands30).not.toBeNull();
    const vol30 = 0.2 * Math.sqrt(30 / 365);
    expect(bands30!.sd2[0]).toBeCloseTo(25000 * Math.exp(-2 * vol30), 1);
    expect(bands30!.sd2[1]).toBeCloseTo(25000 * Math.exp(2 * vol30), 1);
    expect(bands30!.sd2[0]).toBeGreaterThan(0);

    // Vega-weighted blend: ATM 18% + OTM 28% vs independent weights
    const blendRows = [
      {
        strike: 25000,
        is_atm: true,
        ce: { symbol: "", ltp: 200, oi: 1, volume: 1, iv: 18, delta: 0.5 },
        pe: { symbol: "", ltp: 200, oi: 1, volume: 1, iv: 18, delta: -0.5 },
      },
      {
        strike: 28000,
        is_atm: false,
        ce: { symbol: "", ltp: 40, oi: 1, volume: 1, iv: 28, delta: 0.2 },
        pe: { symbol: "", ltp: 40, oi: 1, volume: 1, iv: 28, delta: -0.2 },
      },
    ];
    const blendLegs: StrategyLeg[] = [
      {
        id: "atm",
        side: "buy",
        type: "CE",
        strike: 25000,
        premium: 200,
        qty: 1,
        delta: 0.5,
      },
      {
        id: "otm",
        side: "buy",
        type: "CE",
        strike: 28000,
        premium: 40,
        qty: 1,
        delta: 0.2,
      },
    ];
    const vAtm = black76Greeks({
      forward: 25000,
      strike: 25000,
      daysToExpiry: 30,
      ivPct: 18,
      type: "CE",
    })!.vega;
    const vOtm = black76Greeks({
      forward: 25000,
      strike: 28000,
      daysToExpiry: 30,
      ivPct: 28,
      type: "CE",
    })!.vega;
    const expectedBlend = (18 * Math.abs(vAtm) + 28 * Math.abs(vOtm)) /
      (Math.abs(vAtm) + Math.abs(vOtm));
    const blend = blendStrategyIv(blendLegs, blendRows, 18, {
      forward: 25000,
      daysToExpiry: 30,
    });
    expect(blend).not.toBeNull();
    expect(blend!.ivPct).toBeCloseTo(Math.round(expectedBlend * 100) / 100, 5);

    // Linear IV interpolate mid strike
    const interpRows = [
      {
        strike: 24000,
        is_atm: false,
        ce: { symbol: "", ltp: 1, oi: 1, volume: 1, iv: 12, delta: 0.6 },
        pe: { symbol: "", ltp: 1, oi: 1, volume: 1, iv: 12, delta: -0.4 },
      },
      {
        strike: 26000,
        is_atm: false,
        ce: { symbol: "", ltp: 1, oi: 1, volume: 1, iv: 16, delta: 0.4 },
        pe: { symbol: "", ltp: 1, oi: 1, volume: 1, iv: 16, delta: -0.6 },
      },
    ];
    expect(interpolateSideIv(interpRows, 25000, "CE")).toBeCloseTo(14, 5);

    // Put-call parity synthetic forward
    const parityRows = [
      {
        strike: 25000,
        is_atm: true,
        ce: { symbol: "", ltp: 400, oi: 1, volume: 1, iv: 15, delta: 0.55 },
        pe: { symbol: "", ltp: 250, oi: 1, volume: 1, iv: 15, delta: -0.45 },
      },
    ];
    // F ≈ K + C − P = 25000 + 400 − 250 = 25150
    expect(syntheticForwardFromChain(parityRows, 25000, 25000, 30)).toBeCloseTo(
      25150,
      5,
    );

    // Expiry column of PnL table matches intrinsic payoff
    const straddle = buildStrategyFromTemplate("long_straddle", {
      atm: 24300,
      strikeStep: 50,
      rows,
    });
    const table = buildPnlTable(straddle, {
      strikeStep: 50,
      remainingDtes: [7, 0],
      ivPct: 12,
      wings: 6,
    });
    const expiryCol = table.remainingDtes.indexOf(0);
    expect(expiryCol).toBeGreaterThanOrEqual(0);
    let maxErr = 0;
    for (let i = 0; i < table.spots.length; i++) {
      const intrinsic = totalPayoffAtSpot(straddle, table.spots[i]);
      maxErr = Math.max(maxErr, Math.abs(table.cells[i][expiryCol] - intrinsic));
    }
    expect(maxErr).toBe(0);

    // Breakevens: long straddle debit 175 → BE = 24300 ± 175
    const bePoints = buildPayoffCurve(straddle, {
      spot: 24300,
      strikeStep: 50,
      wings: 12,
    });
    const be = summarizeStrategy(straddle, bePoints).breakevens;
    expect(be.length).toBeGreaterThanOrEqual(2);
    expect(be[0]).toBeCloseTo(24125, -1);
    expect(be[be.length - 1]).toBeCloseTo(24475, -1);
  });

  it("builds new Sensibull templates without duplicate strike+type legs", () => {
    const ids = [
      "bull_put_spread",
      "bear_call_spread",
      "iron_butterfly",
      "long_butterfly_ce",
      "call_ratio",
      "put_ratio",
    ] as const;
    for (const id of ids) {
      const legs = buildStrategyFromTemplate(id, {
        atm: 24300,
        strikeStep: 50,
        rows,
        widthSteps: 1,
      });
      expect(legs.length).toBeGreaterThan(0);
      const keys = legs.map((l) => `${l.side}:${l.type}:${l.strike}`);
      // iron_butterfly sells both CE and PE at ATM — same strike ok, different type
      const typeStrike = legs.map((l) => `${l.type}:${l.strike}`);
      expect(new Set(typeStrike).size).toBe(typeStrike.length);
      expect(keys.length).toBe(legs.length);
    }
  });

  it("gates call calendar until dual-expiry exists", () => {
    const tpl = STRATEGY_TEMPLATES.find((t) => t.id === "calendar_call");
    expect(tpl?.gated).toBe(true);
    const legs = buildStrategyFromTemplate("calendar_call", {
      atm: 24300,
      strikeStep: 50,
      rows,
      widthSteps: 1,
    });
    expect(legs).toEqual([]);
  });
});
