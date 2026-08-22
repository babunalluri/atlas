import type { OptionsChainRow } from "@/lib/api/admin";

export type OptionSide = "CE" | "PE";
export type TradeSide = "buy" | "sell";

export type StrategyLeg = {
  id: string;
  side: TradeSide;
  type: OptionSide;
  strike: number;
  premium: number;
  qty: number;
  delta: number | null;
  /** Chain had no LTP at this strike when the leg was built. */
  quoteMissing?: boolean;
};

export type StrategyTemplateId =
  | "long_ce"
  | "long_pe"
  | "short_ce"
  | "short_pe"
  | "long_straddle"
  | "short_straddle"
  | "long_strangle"
  | "bull_call_spread"
  | "bear_put_spread"
  | "bull_put_spread"
  | "bear_call_spread"
  | "iron_condor"
  | "iron_butterfly"
  | "long_butterfly_ce"
  | "call_ratio"
  | "put_ratio"
  | "calendar_call";

export type StrategyTemplate = {
  id: StrategyTemplateId;
  label: string;
  hint: string;
  usesWidth: boolean;
  /** True when Lab only has a single expiry chain — show disabled until dual-FUT exists. */
  gated?: boolean;
  gateHint?: string;
};

export const STRATEGY_TEMPLATES: StrategyTemplate[] = [
  { id: "long_ce", label: "Long Call", hint: "Bullish", usesWidth: false },
  { id: "long_pe", label: "Long Put", hint: "Bearish", usesWidth: false },
  { id: "short_ce", label: "Short Call", hint: "Bearish / range", usesWidth: false },
  { id: "short_pe", label: "Short Put", hint: "Bullish / range", usesWidth: false },
  { id: "long_straddle", label: "Long Straddle", hint: "Long vol", usesWidth: false },
  { id: "short_straddle", label: "Short Straddle", hint: "Short vol", usesWidth: false },
  { id: "long_strangle", label: "Long Strangle", hint: "Long vol OTM", usesWidth: true },
  { id: "bull_call_spread", label: "Bull Call Spread", hint: "Bullish capped", usesWidth: true },
  { id: "bear_put_spread", label: "Bear Put Spread", hint: "Bearish capped", usesWidth: true },
  { id: "bull_put_spread", label: "Bull Put Spread", hint: "Bullish credit", usesWidth: true },
  { id: "bear_call_spread", label: "Bear Call Spread", hint: "Bearish credit", usesWidth: true },
  { id: "iron_condor", label: "Iron Condor", hint: "Range / short vol", usesWidth: true },
  { id: "iron_butterfly", label: "Iron Butterfly", hint: "ATM short vol", usesWidth: true },
  { id: "long_butterfly_ce", label: "Call Butterfly", hint: "Pinned ATM", usesWidth: true },
  { id: "call_ratio", label: "Call Ratio 1×2", hint: "Bullish ratio", usesWidth: true },
  { id: "put_ratio", label: "Put Ratio 1×2", hint: "Bearish ratio", usesWidth: true },
  {
    id: "calendar_call",
    label: "Call Calendar",
    hint: "Near vs far expiry",
    usesWidth: false,
    gated: true,
    gateHint: "Needs a second FUT/expiry — Lab chain is single-month today",
  },
];

export type PayoffPoint = { spot: number; pnl: number };

export type StrategySummary = {
  netPremium: number;
  netDelta: number | null;
  breakevens: number[];
  maxProfit: number | null;
  maxLoss: number | null;
};

function legSign(side: TradeSide) {
  return side === "buy" ? 1 : -1;
}

/** Floor DTE for σ√T numerics — ~30 minutes, not half a day. */
export const MIN_DTE_DAYS = 1 / 48;

export function yearsFromDte(daysToExpiry: number): number {
  return Math.max(daysToExpiry, MIN_DTE_DAYS) / 365;
}

export function legPayoffAtSpot(leg: StrategyLeg, spot: number): number {
  const intrinsic =
    leg.type === "CE"
      ? Math.max(0, spot - leg.strike)
      : Math.max(0, leg.strike - spot);
  const cashflow = legSign(leg.side) * (intrinsic - leg.premium) * leg.qty;
  return cashflow;
}

export function totalPayoffAtSpot(legs: StrategyLeg[], spot: number): number {
  return legs.reduce((sum, leg) => sum + legPayoffAtSpot(leg, spot), 0);
}

/** True when remaining life is effectively expiry (use intrinsic payoff). */
export function isExpiryHorizon(remainingDaysToExpiry: number): boolean {
  return !(remainingDaysToExpiry > MIN_DTE_DAYS * 2);
}

/**
 * Mark-to-model P&L at a future spot before expiry (Black-76, r=0, F≈spot).
 * At/near expiry falls back to intrinsic payoff.
 */
export function legMarkPnlAtSpot(
  leg: StrategyLeg,
  spot: number,
  {
    remainingDaysToExpiry,
    ivPct,
  }: {
    remainingDaysToExpiry: number;
    ivPct: number;
  },
): number {
  if (!(spot > 0) || usableIvPct(ivPct) == null || isExpiryHorizon(remainingDaysToExpiry)) {
    return legPayoffAtSpot(leg, spot);
  }
  const mark = black76Price(
    spot,
    leg.strike,
    yearsFromDte(remainingDaysToExpiry),
    ivPct / 100,
    leg.type,
  );
  return legSign(leg.side) * (mark - leg.premium) * leg.qty;
}

/** Per-leg IV map (leg id → IV %). */
export type LegIvMap = Record<string, number>;

export function buildLegIvMap(
  legs: StrategyLeg[],
  rows: OptionsChainRow[],
  {
    atmIv,
    forward,
    daysToExpiry,
    ivShockPts = 0,
  }: {
    atmIv?: number | null;
    forward?: number | null;
    daysToExpiry?: number | null;
    /** Applied to every resolved leg IV (scenario slider). */
    ivShockPts?: number;
  } = {},
): LegIvMap | null {
  if (legs.length === 0) return null;
  const out: LegIvMap = {};
  for (const leg of legs) {
    const resolved = resolveLegIv(rows, leg, {
      atmIv,
      forward,
      daysToExpiry,
      premium: leg.quoteMissing ? null : leg.premium,
    });
    const base = usableIvPct(resolved?.iv);
    if (base == null) return null;
    out[leg.id] = Math.max(MIN_USABLE_IV_PCT, base + ivShockPts);
  }
  return out;
}

export function totalMarkPnlAtSpot(
  legs: StrategyLeg[],
  spot: number,
  opts: {
    remainingDaysToExpiry: number;
    /** Fallback when a leg is missing from legIvById. */
    ivPct: number;
    legIvById?: LegIvMap | null;
  },
): number {
  return legs.reduce((sum, leg) => {
    const iv = opts.legIvById?.[leg.id] ?? opts.ivPct;
    return sum + legMarkPnlAtSpot(leg, spot, {
      remainingDaysToExpiry: opts.remainingDaysToExpiry,
      ivPct: iv,
    });
  }, 0);
}

export function payoffGridBounds(
  legs: StrategyLeg[],
  strikeStep: number,
  wings = 18,
): { gridLo: number; gridHi: number; step: number } {
  const step = Math.max(1, strikeStep);
  if (legs.length === 0) return { gridLo: 0, gridHi: 0, step };
  const legStrikes = legs.map((leg) => leg.strike);
  const minLeg = Math.min(...legStrikes);
  const maxLeg = Math.max(...legStrikes);
  return {
    step,
    gridLo: Math.floor((minLeg - wings * step) / step) * step,
    gridHi: Math.ceil((maxLeg + wings * step) / step) * step,
  };
}

export function buildPayoffCurve(
  legs: StrategyLeg[],
  {
    spot,
    strikeStep,
    wings = 18,
    remainingDaysToExpiry = 0,
    ivPct,
    legIvById,
    extraSpots,
  }: {
    spot?: number;
    strikeStep: number;
    wings?: number;
    /** Days from target date to expiry. 0 ⇒ expiry intrinsic curve. */
    remainingDaysToExpiry?: number;
    /** Required for pre-expiry marks; ignored at expiry horizon. */
    ivPct?: number | null;
    /** Per-leg IV overrides blended σ*. */
    legIvById?: LegIvMap | null;
    /** Extra x values that must fall inside the plotted domain (σ edges, etc.). */
    extraSpots?: number[];
  },
): PayoffPoint[] {
  if (legs.length === 0) return [];
  const bounds = payoffGridBounds(legs, strikeStep, wings);
  let gridLo = bounds.gridLo;
  let gridHi = bounds.gridHi;
  const step = bounds.step;
  const anchors = [
    ...(spot != null && Number.isFinite(spot) && spot > 0 ? [spot] : []),
    ...(extraSpots ?? []).filter((v) => Number.isFinite(v) && v > 0),
  ];
  for (const anchor of anchors) {
    gridLo = Math.min(gridLo, Math.floor(anchor / step) * step);
    gridHi = Math.max(gridHi, Math.ceil(anchor / step) * step);
  }
  const useMark =
    !isExpiryHorizon(remainingDaysToExpiry) &&
    ((legIvById != null && Object.keys(legIvById).length > 0) ||
      (ivPct != null && usableIvPct(ivPct) != null));
  // When useMark is true, either legIvById or a usable ivPct is present — never invent 20%.
  const markIv = usableIvPct(ivPct) ?? 0;
  const points: PayoffPoint[] = [];
  for (let s = gridLo; s <= gridHi + 0.001; s += step) {
    const pnl = useMark
      ? totalMarkPnlAtSpot(legs, s, {
          remainingDaysToExpiry,
          ivPct: markIv,
          legIvById,
        })
      : totalPayoffAtSpot(legs, s);
    points.push({ spot: s, pnl });
  }
  return points;
}

/**
 * ±1σ / ±2σ spot bands under Black–Scholes lognormal dynamics:
 * F · exp(±k·σ·√T). Additive F ± k·σ√T can go negative for high vol / long T.
 * `move1` is the +1σ absolute move (F·e^{σ√T} − F).
 */
export function volatilitySpotBands(
  forward: number,
  ivPct: number,
  daysToHorizon: number,
): { sd1: [number, number]; sd2: [number, number]; move1: number } | null {
  if (!(forward > 0) || usableIvPct(ivPct) == null || !(daysToHorizon > 0)) {
    return null;
  }
  const vol = (ivPct / 100) * Math.sqrt(yearsFromDte(daysToHorizon));
  if (!(vol > 0) || !Number.isFinite(vol)) return null;
  const up1 = forward * Math.exp(vol);
  const dn1 = forward * Math.exp(-vol);
  const up2 = forward * Math.exp(2 * vol);
  const dn2 = forward * Math.exp(-2 * vol);
  const move1 = up1 - forward;
  if (!(move1 > 0) || !(dn2 > 0)) return null;
  return {
    move1,
    sd1: [dn1, up1],
    sd2: [dn2, up2],
  };
}

export type PnlTable = {
  spots: number[];
  /** Remaining DTE columns (days from target → expiry). */
  remainingDtes: number[];
  /** cells[rowSpot][colDte] */
  cells: number[][];
};

/** Spot × remaining-DTE P&L matrix (Sensibull-style payoff table). */
export function buildPnlTable(
  legs: StrategyLeg[],
  {
    strikeStep,
    remainingDtes,
    ivPct,
    legIvById,
    wings = 10,
    spotStepMultiplier = 1,
  }: {
    strikeStep: number;
    remainingDtes: number[];
    ivPct: number | null | undefined;
    legIvById?: LegIvMap | null;
    wings?: number;
    spotStepMultiplier?: number;
  },
): PnlTable {
  if (legs.length === 0 || remainingDtes.length === 0) {
    return { spots: [], remainingDtes: [], cells: [] };
  }
  const { gridLo, gridHi, step } = payoffGridBounds(legs, strikeStep, wings);
  const spotStep = step * Math.max(1, spotStepMultiplier);
  const spots: number[] = [];
  for (let s = gridLo; s <= gridHi + 0.001; s += spotStep) spots.push(s);
  const hasIv =
    (legIvById != null && Object.keys(legIvById).length > 0) ||
    (ivPct != null && usableIvPct(ivPct) != null);
  const markIv = usableIvPct(ivPct) ?? 0;
  const cells = spots.map((spot) =>
    remainingDtes.map((remaining) => {
      if (isExpiryHorizon(remaining) || !hasIv) {
        return round2(totalPayoffAtSpot(legs, spot));
      }
      return round2(
        totalMarkPnlAtSpot(legs, spot, {
          remainingDaysToExpiry: remaining,
          ivPct: markIv,
          legIvById,
        }),
      );
    }),
  );
  return { spots, remainingDtes, cells };
}

/** Infer NSE lot size from underlying label/symbol when API omits it. */
export function estimateLotSize(underlying?: string | null): number {
  const u = String(underlying || "").toUpperCase();
  // Longer roots first so BANKNIFTY / MIDCPNIFTY are not matched as NIFTY.
  if (u.includes("BANKNIFTY") || u.includes("BANK NIFTY")) return 15;
  if (u.includes("FINNIFTY") || u.includes("FIN NIFTY")) return 25;
  if (u.includes("NIFTYNXT50") || u.includes("NIFTY NEXT")) return 25;
  if (u.includes("MIDCPNIFTY") || u.includes("MIDCP")) return 50;
  if (u.includes("SENSEX")) return 10;
  return 75;
}

export type FundsMarginsEstimate = {
  fundsNeeded: number;
  marginNeeded: number;
  premiumDebit: number;
  premiumCredit: number;
  shortSpanProxy: number;
  lotSize: number;
  /** Heuristic only — not broker SPAN/ELM. */
  estimated: true;
};

/**
 * Desk-side funds/margin proxy (not exchange SPAN).
 * Long premium debit + crude short SPAN (~12% of spot × lots).
 */
export function estimateFundsAndMargins(
  legs: StrategyLeg[],
  {
    spot,
    lotSize,
  }: {
    spot: number;
    lotSize: number;
  },
): FundsMarginsEstimate | null {
  if (legs.length === 0 || !(spot > 0) || !(lotSize > 0)) return null;
  let premiumDebit = 0;
  let premiumCredit = 0;
  let shortSpanProxy = 0;
  for (const leg of legs) {
    const premiumCash = leg.premium * leg.qty * lotSize;
    if (leg.side === "buy") {
      premiumDebit += premiumCash;
    } else {
      premiumCredit += premiumCash;
      shortSpanProxy += 0.12 * spot * leg.qty * lotSize + premiumCash;
    }
  }
  const netDebit = Math.max(0, premiumDebit - premiumCredit);
  const marginNeeded = round2(netDebit + shortSpanProxy);
  return {
    fundsNeeded: marginNeeded,
    marginNeeded,
    premiumDebit: round2(premiumDebit),
    premiumCredit: round2(premiumCredit),
    shortSpanProxy: round2(shortSpanProxy),
    lotSize,
    estimated: true,
  };
}

export type ChargesEstimate = {
  brokerage: number;
  stt: number;
  exchangeTxn: number;
  gst: number;
  total: number;
  turnover: number;
  estimated: true;
};

/** Rough NSE F&O option charges (educational estimate, not broker invoice). */
export function estimateStrategyCharges(
  legs: StrategyLeg[],
  lotSize: number,
): ChargesEstimate | null {
  if (legs.length === 0 || !(lotSize > 0)) return null;
  let turnover = 0;
  let sellPremium = 0;
  for (const leg of legs) {
    const cash = leg.premium * leg.qty * lotSize;
    turnover += cash;
    if (leg.side === "sell") sellPremium += cash;
  }
  const brokerage = legs.length * 20;
  const stt = sellPremium * 0.001;
  const exchangeTxn = turnover * 0.00053;
  const gst = (brokerage + exchangeTxn) * 0.18;
  const total = round2(brokerage + stt + exchangeTxn + gst);
  return {
    brokerage: round2(brokerage),
    stt: round2(stt),
    exchangeTxn: round2(exchangeTxn),
    gst: round2(gst),
    total,
    turnover: round2(turnover),
    estimated: true,
  };
}

/** Live MTM vs builder premiums (per lot unit). Multiply by lot size for rupee P&L. */
export function bookedStrategyPnl(
  legs: StrategyLeg[],
  rows: OptionsChainRow[],
): number | null {
  if (legs.length === 0) return null;
  let total = 0;
  for (const leg of legs) {
    const quote = chainLegPremium(rows, leg.strike, leg.type);
    if (quote.premium == null) return null;
    total += legSign(leg.side) * (quote.premium - leg.premium) * leg.qty;
  }
  return round2(total);
}

/** Rupee booked P&L = per-unit booked × lot size. */
export function bookedStrategyPnlRupees(
  legs: StrategyLeg[],
  rows: OptionsChainRow[],
  lotSize: number,
): number | null {
  const perUnit = bookedStrategyPnl(legs, rows);
  if (perUnit == null || !(lotSize > 0)) return null;
  return round2(perUnit * lotSize);
}

export type PayoffDistributionStats = {
  pop: number;
  expectedPnl: number;
  /** Mass where PnL ≥ 95% of finite max profit (null if max unlimited). */
  pMaxProfit: number | null;
};

/**
 * Richer expiry stats: PoP, E[PnL], P(near max profit) via lognormal density.
 * Integrate on a vol-aware domain (±6σ ∪ strike wings), then renormalize by
 * captured mass so truncation cannot flip the sign of E[PnL].
 */
export function estimatePayoffDistributionStats(
  legs: StrategyLeg[],
  {
    forward,
    ivPct,
    daysToExpiry,
    strikeStep,
    maxProfit,
  }: {
    forward: number;
    ivPct: number;
    daysToExpiry: number;
    strikeStep: number;
    maxProfit: number | null;
  },
): PayoffDistributionStats | null {
  if (legs.length === 0) return null;
  if (!(forward > 0) || !(ivPct > 0) || !(daysToExpiry > 0) || !(strikeStep > 0)) {
    return null;
  }
  const pop = estimateProbabilityOfProfit(legs, {
    forward,
    ivPct,
    daysToExpiry,
    strikeStep,
  });
  if (pop == null) return null;

  const years = yearsFromDte(daysToExpiry);
  const sigma = ivPct / 100;
  const step = Math.max(1, strikeStep);
  const strikeBounds = payoffGridBounds(legs, strikeStep, 24);
  const volWing = forward * sigma * Math.sqrt(years) * 6;
  const gridLo = Math.max(
    0,
    Math.floor(Math.min(strikeBounds.gridLo, forward - volWing) / step) * step,
  );
  const gridHi =
    Math.ceil(
      Math.max(strikeBounds.gridHi, forward + volWing, forward * 1.4, forward + 1) /
        step,
    ) * step;

  let expected = 0;
  let mass = 0;
  let massMax = 0;
  const threshold =
    maxProfit != null && Number.isFinite(maxProfit) ? maxProfit * 0.95 : null;

  for (let s = gridLo; s <= gridHi + 0.001; s += step) {
    const lo = Math.max(0, s - step / 2);
    const hi = s + step / 2;
    const p =
      lognormalCdf(hi, forward, sigma, years) - lognormalCdf(lo, forward, sigma, years);
    if (p <= 0) continue;
    const pnl = totalPayoffAtSpot(legs, s);
    expected += pnl * p;
    mass += p;
    if (threshold != null && pnl >= threshold) massMax += p;
  }

  if (mass > 1e-12) {
    expected /= mass;
    if (threshold != null) massMax /= mass;
  }

  return {
    pop,
    expectedPnl: round2(expected),
    pMaxProfit: threshold == null ? null : round2(massMax * 1000) / 10,
  };
}

/** Expiry payoff extremes from leg geometry (not sampled window edges). */
export function payoffExtremes(legs: StrategyLeg[]): {
  maxProfit: number | null;
  maxLoss: number | null;
} {
  if (legs.length === 0) return { maxProfit: null, maxLoss: null };

  let rightSlope = 0;
  for (const leg of legs) {
    const sign = legSign(leg.side);
    if (leg.type === "CE") rightSlope += sign * leg.qty;
  }

  const rightLossUnbounded = rightSlope < 0;
  const rightProfitUnbounded = rightSlope > 0;

  const strikes = [...new Set(legs.map((leg) => leg.strike))].sort((a, b) => a - b);
  const critical = new Set<number>([0, ...strikes]);
  for (let i = 0; i < strikes.length - 1; i += 1) {
    critical.add((strikes[i] + strikes[i + 1]) / 2);
  }

  const pnls = [...critical].map((spot) => totalPayoffAtSpot(legs, spot));
  let maxProfit: number | null = Math.max(...pnls);
  let maxLoss: number | null = Math.min(...pnls);

  if (rightProfitUnbounded) maxProfit = null;
  if (rightLossUnbounded) maxLoss = null;

  return {
    maxProfit: maxProfit != null ? round2(maxProfit) : null,
    maxLoss: maxLoss != null ? round2(maxLoss) : null,
  };
}

function findBreakevens(points: PayoffPoint[]): number[] {
  const out: number[] = [];
  for (let i = 1; i < points.length; i += 1) {
    const prev = points[i - 1];
    const curr = points[i];
    if (prev.pnl === 0) out.push(prev.spot);
    if (prev.pnl === 0 || curr.pnl === 0) continue;
    if (prev.pnl * curr.pnl < 0) {
      const ratio = prev.pnl / (prev.pnl - curr.pnl);
      out.push(Math.round(prev.spot + ratio * (curr.spot - prev.spot)));
    }
  }
  const last = points[points.length - 1];
  if (last?.pnl === 0) out.push(last.spot);
  return [...new Set(out)];
}

export function summarizeStrategy(
  legs: StrategyLeg[],
  points: PayoffPoint[],
): StrategySummary {
  if (legs.length === 0 || points.length === 0) {
    return {
      netPremium: 0,
      netDelta: null,
      breakevens: [],
      maxProfit: null,
      maxLoss: null,
    };
  }

  const netPremium = legs.reduce(
    (sum, leg) => sum - legSign(leg.side) * leg.premium * leg.qty,
    0,
  );

  let netDelta: number | null = 0;
  for (const leg of legs) {
    if (leg.delta == null) {
      netDelta = null;
      break;
    }
    netDelta += legSign(leg.side) * leg.delta * leg.qty;
  }

  const { maxProfit, maxLoss } = payoffExtremes(legs);

  return {
    netPremium: round2(netPremium),
    netDelta: netDelta != null ? round4(netDelta) : null,
    breakevens: findBreakevens(points),
    maxProfit,
    maxLoss,
  };
}

function round2(n: number) {
  return Math.round(n * 100) / 100;
}

function round4(n: number) {
  return Math.round(n * 10_000) / 10_000;
}

const MONTH_INDEX: Record<string, number> = {
  JAN: 0,
  FEB: 1,
  MAR: 2,
  APR: 3,
  MAY: 4,
  JUN: 5,
  JUL: 6,
  AUG: 7,
  SEP: 8,
  OCT: 9,
  NOV: 10,
  DEC: 11,
};

/** NSE weekly month codes for letter months: O=Oct, N=Nov, D=Dec.
 *  Digits 1–9 are NOT included here — digit months live in YYMDD / YYMMDD codes.
 *  Including 1–9 would swallow `251127` as weekly-alpha `25112` (12 Jan).
 */
const WEEKLY_MONTH_CODE: Record<string, number> = {
  O: 9,
  N: 10,
  D: 11,
};

const INDEX_OPTION_ROOTS = [
  "MIDCPNIFTY",
  "BANKNIFTY",
  "FINNIFTY",
  "NIFTYNXT50",
  "NIFTY",
  "SENSEX",
] as const;

/** Generous ATM±OTM bands — disambiguate Jan weekly E5+S5 vs false YYMMDD+S4. */
const ROOT_STRIKE_BANDS: Record<(typeof INDEX_OPTION_ROOTS)[number], [number, number]> = {
  NIFTY: [5_000, 50_000],
  BANKNIFTY: [25_000, 80_000],
  FINNIFTY: [10_000, 50_000],
  NIFTYNXT50: [1_000, 120_000],
  MIDCPNIFTY: [5_000, 25_000],
  SENSEX: [40_000, 120_000],
};

/** Last Thursday of calendar month (historical NSE monthly F&O convention).
 *  Product/year calendars differ — see docs/options-lab-market-profile.md.
 *  Anchor 10:00 UTC ≈ 15:30 IST cash close for intraday DTE fractions.
 */
export function lastThursdayOfMonth(year: number, monthIndex: number): Date {
  const end = new Date(Date.UTC(year, monthIndex + 1, 0, 10, 0, 0));
  const day = end.getUTCDay(); // 0 Sun … 4 Thu
  const offset = (day + 3) % 7; // days since Thursday
  end.setUTCDate(end.getUTCDate() - offset);
  return end;
}

function calendarDaysUntil(expiry: Date, now: Date): number | null {
  const ms = expiry.getTime() - now.getTime();
  // Past / unknown expiry → null so callers can fall back (FUT / default),
  // instead of silently clamping to 0.5d and collapsing PoP.
  if (ms <= 0) return null;
  return ms / (24 * 60 * 60 * 1000);
}

/** Expiry instant at 10:00 UTC (15:30 IST close). */
function utcDate(year: number, monthIndex: number, day: number): Date | null {
  if (monthIndex < 0 || monthIndex > 11 || day < 1 || day > 31) return null;
  const d = new Date(Date.UTC(year, monthIndex, day, 10, 0, 0));
  if (d.getUTCFullYear() !== year || d.getUTCMonth() !== monthIndex || d.getUTCDate() !== day) {
    return null;
  }
  return d;
}

/** IST hour bucket `YYYY-MM-DDTHH` so expiry-day DTE/PoP recompute intraday. */
export function istSessionHourKey(now: Date = new Date()): string {
  const istMs = now.getTime() + 5.5 * 60 * 60 * 1000;
  return new Date(istMs).toISOString().slice(0, 13);
}

/** Parse monthly expiry token `26AUG` → last Thursday of that month. */
export function expiryDateFromMonthlyCode(code: string): Date | null {
  const match = code.toUpperCase().match(/^(\d{2})([A-Z]{3})$/);
  if (!match) return null;
  const year = 2000 + Number(match[1]);
  const monthIndex = MONTH_INDEX[match[2]];
  if (monthIndex == null || Number.isNaN(year)) return null;
  return lastThursdayOfMonth(year, monthIndex);
}

/** Parse weekly alpha token `25N11` → 11 Nov 2025 (O/N/D months only). */
export function expiryDateFromWeeklyAlphaCode(code: string): Date | null {
  const match = code.toUpperCase().match(/^(\d{2})([OND])(\d{2})$/);
  if (!match) return null;
  const year = 2000 + Number(match[1]);
  const monthIndex = WEEKLY_MONTH_CODE[match[2]];
  const day = Number(match[3]);
  if (monthIndex == null || Number.isNaN(year)) return null;
  return utcDate(year, monthIndex, day);
}

/** Calendar decode for YYMDD / YYMMDD — weekday policy lives in the scorer. */
function parseWeeklyDigitsDate(code: string): Date | null {
  if (!/^\d+$/.test(code)) return null;
  if (code.length === 5) {
    const year = 2000 + Number(code.slice(0, 2));
    const monthIndex = Number(code.slice(2, 3)) - 1;
    const day = Number(code.slice(3, 5));
    return utcDate(year, monthIndex, day);
  }
  if (code.length === 6) {
    const year = 2000 + Number(code.slice(0, 2));
    const monthIndex = Number(code.slice(2, 4)) - 1;
    const day = Number(code.slice(4, 6));
    return utcDate(year, monthIndex, day);
  }
  return null;
}

/** UTC day-of-week sets per root (0=Sun … 6=Sat). */
function weeklyUtcDaysForRoot(root: string): Set<number> {
  const key = root.toUpperCase();
  if (key === "MIDCPNIFTY") return new Set([1, 2, 4]); // Mon, Tue, Thu
  if (key === "SENSEX") return new Set([4, 5]); // Thu, Fri
  return new Set([2, 3, 4]); // NIFTY / BANKNIFTY / FINNIFTY / NIFTYNXT50: Tue, Wed, Thu
}

/** Parse weekly digit token `25807` (YYMDD) or `250807` (YYMMDD). */
export function expiryDateFromWeeklyDigitsCode(code: string): Date | null {
  return parseWeeklyDigitsDate(code);
}

export function expiryDateFromExpiryCode(code: string): Date | null {
  const raw = code.trim().toUpperCase();
  if (!raw) return null;
  return (
    expiryDateFromMonthlyCode(raw) ??
    expiryDateFromWeeklyAlphaCode(raw) ??
    expiryDateFromWeeklyDigitsCode(raw)
  );
}

function looksYymmddExpiry(code: string): boolean {
  return code.length === 6 && parseWeeklyDigitsDate(code) != null;
}

function strikeInRootBand(root: string, strike: number): boolean {
  const band = ROOT_STRIKE_BANDS[root as keyof typeof ROOT_STRIKE_BANDS];
  if (!band) return true;
  return strike >= band[0] && strike <= band[1];
}

/**
 * Score weekly digit splits — in-band E5+S5 outranks YYMMDD+S4 (NIFTY 25500 vs 5500).
 * Per-root weekday gate rejects false rivals (e.g. NIFTY Mon 25120 vs Thu 251204).
 */
function weeklyDigitsParseScore(
  root: string,
  expiryPart: string,
  strikeLen: number,
  strike: number,
): number {
  if (expiryPart.length !== 5 && expiryPart.length !== 6) return 0;
  const d = parseWeeklyDigitsDate(expiryPart);
  if (d == null) return 0;
  if (!weeklyUtcDaysForRoot(root).has(d.getUTCDay())) return 0;
  const inBand = strikeInRootBand(root, strike);
  if (looksYymmddExpiry(expiryPart) && strikeLen === 5) return inBand ? 100 : 25;
  // In-band E5+S5 outranks YYMMDD+S4 — NIFTY 25500 truncates to in-band 5500.
  if (expiryPart.length === 5 && strikeLen === 5 && strike >= 1000 && strike <= 99999) {
    return inBand ? 95 : 20;
  }
  // YYMMDD+S4 when longer 5-digit strike is out of band, or E5 fails weekday gate.
  if (
    looksYymmddExpiry(expiryPart) &&
    strikeLen === 4 &&
    strike >= 1000 &&
    strike <= 9999
  ) {
    return inBand ? 90 : 15;
  }
  if (expiryPart.length === 5 && strikeLen === 6 && strike >= 100000) {
    // 6-digit strikes are rare edge cases; do not apply ATM band.
    return 70;
  }
  if (expiryPart.length === 5 && strikeLen === 4) return inBand ? 40 : 12;
  if (looksYymmddExpiry(expiryPart) && strikeLen !== 5) return 10;
  return 20;
}

function parseWeeklyDigitsExpiryAndStrike(
  root: string,
  tail: string,
): { expiryCode: string; strike: string } | null {
  let best: { score: number; expiryCode: string; strike: string } | null = null;
  for (const strikeLen of [4, 5, 6]) {
    if (tail.length <= strikeLen + 4) continue;
    const expiryPart = tail.slice(0, -strikeLen);
    const strikePart = tail.slice(-strikeLen);
    if (!/^\d+$/.test(expiryPart) || expiryPart.length < 5) continue;
    const strike = Number(strikePart);
    const score = weeklyDigitsParseScore(root, expiryPart, strikeLen, strike);
    if (score <= 0) continue;
    if (!best || score > best.score) {
      best = { score, expiryCode: expiryPart, strike: String(strike) };
    }
  }
  return best ? { expiryCode: best.expiryCode, strike: best.strike } : null;
}

/**
 * Extract expiry code from NSE-style option symbols, e.g.
 * `NFO:NIFTY26AUG24500CE`, `NIFTY25N1124500CE`, `NIFTY2580724500CE`.
 */
export function expiryCodeFromOptionSymbol(symbol: string): string | null {
  return parseOptionSymbolParts(symbol)?.expiry ?? null;
}

export type ParsedOptionSymbolParts = {
  underlier: string;
  expiry: string;
  strike: number;
  side: "CE" | "PE";
  monthlyToken: boolean;
};

/** Structured parse shared by labels, DTE, and cross-language fixture tests. */
export function parseOptionSymbolParts(
  symbol: string | null | undefined,
): ParsedOptionSymbolParts | null {
  if (!symbol) return null;
  let raw = symbol.trim().toUpperCase();
  if (!raw) return null;
  if (raw.includes(":")) raw = raw.split(":", 2)[1] ?? raw;
  if (!raw.endsWith("CE") && !raw.endsWith("PE")) return null;
  const side = raw.slice(-2) as "CE" | "PE";
  const body = raw.slice(0, -2);

  let underlier: string | null = null;
  let expiry: string | null = null;
  let strikeRaw: string | null = null;
  let monthlyToken = false;

  const monthly = body.match(/^([A-Z]+)(\d{2}[A-Z]{3})(\d+)$/);
  if (monthly) {
    underlier = monthly[1];
    expiry = monthly[2];
    strikeRaw = monthly[3];
    monthlyToken = true;
  } else {
    const weeklyAlpha = body.match(/^([A-Z]+)(\d{2}[OND]\d{2})(\d+)$/);
    if (weeklyAlpha) {
      underlier = weeklyAlpha[1];
      expiry = weeklyAlpha[2];
      strikeRaw = weeklyAlpha[3];
    } else {
      for (const root of INDEX_OPTION_ROOTS) {
        if (!body.startsWith(root)) continue;
        const tail = body.slice(root.length);
        if (!/^\d+$/.test(tail)) continue;
        const parsed = parseWeeklyDigitsExpiryAndStrike(root, tail);
        if (parsed) {
          underlier = root;
          expiry = parsed.expiryCode;
          strikeRaw = parsed.strike;
          break;
        }
      }
    }
  }

  if (!underlier || !expiry || !strikeRaw) return null;
  const strike = Number(strikeRaw);
  if (!Number.isFinite(strike)) return null;
  return { underlier, expiry, strike, side, monthlyToken };
}

/**
 * Parse `NFO:NIFTY26AUGFUT` / `NIFTY26AUGFUT` → calendar days to monthly expiry.
 * Returns null when the symbol is not a monthly FUT code.
 */
export function daysToExpiryFromFutSymbol(
  futSymbol: string | null | undefined,
  now: Date = new Date(),
): number | null {
  const raw = String(futSymbol || "")
    .trim()
    .toUpperCase();
  if (!raw) return null;
  const body = raw.includes(":") ? raw.split(":", 2)[1] : raw;
  const match = body.match(/(\d{2})([A-Z]{3})FUT$/);
  if (!match) return null;
  const expiry = expiryDateFromMonthlyCode(`${match[1]}${match[2]}`);
  if (!expiry) return null;
  return calendarDaysUntil(expiry, now);
}

export function daysToExpiryFromOptionSymbol(
  symbol: string | null | undefined,
  now: Date = new Date(),
): number | null {
  if (!symbol) return null;
  const code = expiryCodeFromOptionSymbol(symbol);
  if (!code) return null;
  const expiry = expiryDateFromExpiryCode(code);
  if (!expiry) return null;
  return calendarDaysUntil(expiry, now);
}

/**
 * Prefer option-leg expiries (nearest), then monthly FUT.
 * If option symbols decode but are all past expiry, return null (do not
 * silently jump to monthly FUT DTE — that wrecks same-day PoP).
 */
export function resolveDaysToExpiry(
  {
    futSymbol,
    optionSymbols = [],
  }: {
    futSymbol?: string | null;
    optionSymbols?: Array<string | null | undefined>;
  },
  now: Date = new Date(),
): number | null {
  const fromOptions: number[] = [];
  let decodedOptionExpiry = false;
  for (const symbol of optionSymbols) {
    if (!symbol) continue;
    const code = expiryCodeFromOptionSymbol(symbol);
    if (!code) continue;
    const expiry = expiryDateFromExpiryCode(code);
    if (!expiry) continue;
    decodedOptionExpiry = true;
    const days = calendarDaysUntil(expiry, now);
    if (days != null) fromOptions.push(days);
  }
  if (fromOptions.length > 0) return Math.min(...fromOptions);
  if (decodedOptionExpiry) return null;
  return daysToExpiryFromFutSymbol(futSymbol, now);
}

/** Standard normal CDF via erf approximation. */
export function normCdf(x: number): number {
  const t = 1 / (1 + 0.2316419 * Math.abs(x));
  const d = 0.3989423 * Math.exp((-x * x) / 2);
  const p =
    d *
    t *
    (0.3193815 +
      t * (-0.3565638 + t * (1.781478 + t * (-1.821256 + t * 1.330274))));
  return x >= 0 ? 1 - p : p;
}

/** Standard normal PDF. */
export function normPdf(x: number): number {
  return 0.3989422804014327 * Math.exp((-x * x) / 2);
}

/** Reject junk / near-zero IV that would explode Γ (and step-function PoP). */
export const MIN_USABLE_IV_PCT = 0.5;

export function usableIvPct(iv: number | null | undefined): number | null {
  if (iv == null || Number.isNaN(iv) || !(iv >= MIN_USABLE_IV_PCT)) return null;
  return iv;
}

function formatExpiryDayLabel(expiry: Date): string {
  const months = [
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
  ];
  const day = String(expiry.getUTCDate()).padStart(2, "0");
  return `${day} ${months[expiry.getUTCMonth()]} ${String(expiry.getUTCFullYear()).slice(-2)}`;
}

/** `26AUG` → `Aug 26` — month token only; do not invent last-Thursday calendar day. */
function formatMonthlyCodeLabel(code: string): string | null {
  const match = code.toUpperCase().match(/^(\d{2})([A-Z]{3})$/);
  if (!match) return null;
  const mon = match[2][0] + match[2].slice(1).toLowerCase();
  return `${mon} ${match[1]}`;
}

/**
 * Human-readable contract label from an NSE-style option symbol.
 * Weekly codes decode to a calendar day; monthly codes keep month+year only
 * (last-Thursday is a modelling assumption — not user-facing as a day).
 * `NFO:NIFTY26AUG24500CE` → `NIFTY Aug 26 24500 CE`
 * `NIFTY25112724500CE` → `NIFTY 27 Nov 25 24500 CE`
 */
export function formatOptionContractName(symbol: string | null | undefined): string | null {
  if (!symbol) return null;
  let raw = symbol.trim().toUpperCase();
  if (!raw) return null;
  if (raw.includes(":")) raw = raw.split(":", 2)[1] ?? raw;
  if (!raw.endsWith("CE") && !raw.endsWith("PE")) return raw;

  const parts = parseOptionSymbolParts(symbol);
  if (!parts) return raw;

  let expiryLabel: string;
  if (parts.monthlyToken) {
    expiryLabel = formatMonthlyCodeLabel(parts.expiry) ?? parts.expiry;
  } else {
    const expiry = expiryDateFromExpiryCode(parts.expiry);
    expiryLabel = expiry ? formatExpiryDayLabel(expiry) : parts.expiry;
  }
  return `${parts.underlier} ${expiryLabel} ${parts.strike} ${parts.side}`;
}

function lognormalCdf(spot: number, forward: number, sigma: number, years: number): number {
  if (!(spot > 0) || !(forward > 0) || !(sigma > 0) || !(years > 0)) return 0;
  const vol = sigma * Math.sqrt(years);
  if (vol < 1e-12) return spot >= forward ? 1 : 0;
  const z = (Math.log(spot / forward) + 0.5 * sigma * sigma * years) / vol;
  return normCdf(z);
}

function findProfitableRanges(
  legs: StrategyLeg[],
  strikeStep: number,
  forward: number,
  {
    ivPct = 20,
    daysToExpiry = 7,
  }: { ivPct?: number; daysToExpiry?: number } = {},
): Array<{ lo: number; hi: number }> {
  const step = Math.max(1, strikeStep);
  const maxStrike = Math.max(forward, ...legs.map((leg) => leg.strike));
  const years = yearsFromDte(daysToExpiry);
  const sigma = Math.max(ivPct, 1) / 100;
  // Cover ~5σ of the lognormal mass so far OTM profit regions aren't truncated.
  const volWing = forward * sigma * Math.sqrt(years) * 5;
  const hi =
    Math.ceil(
      Math.max(maxStrike + 80 * step, forward + volWing, forward * 1.4) / step,
    ) * step;
  const ranges: Array<{ lo: number; hi: number }> = [];
  let inProfit = false;
  let start = 0;
  let prevSpot = 0;
  let prevPnl = totalPayoffAtSpot(legs, 0);

  for (let spot = 0; spot <= hi + 0.001; spot += step) {
    const pnl = totalPayoffAtSpot(legs, spot);
    const profitable = pnl > 1e-9;
    if (profitable && !inProfit) {
      if (spot > 0 && prevPnl <= 0) {
        const ratio = prevPnl / (prevPnl - pnl);
        start = prevSpot + ratio * (spot - prevSpot);
      } else {
        start = spot;
      }
      inProfit = true;
    } else if (!profitable && inProfit) {
      let end = spot;
      if (prevPnl > 0) {
        const ratio = prevPnl / (prevPnl - pnl);
        end = prevSpot + ratio * (spot - prevSpot);
      }
      ranges.push({ lo: start, hi: end });
      inProfit = false;
    }
    prevSpot = spot;
    prevPnl = pnl;
  }
  if (inProfit) ranges.push({ lo: start, hi: Number.POSITIVE_INFINITY });
  return ranges;
}

/**
 * IV-implied Probability of Profit at expiry (0–100).
 * Uses Black-76 style lognormal with forward ≈ futures/synthetic F and an effective IV %.
 * Returns null when inputs are incomplete or invalid.
 */
export function estimateProbabilityOfProfit(
  legs: StrategyLeg[],
  {
    forward,
    ivPct,
    daysToExpiry,
    strikeStep,
  }: {
    forward: number;
    ivPct: number;
    daysToExpiry: number;
    strikeStep: number;
  },
): number | null {
  if (legs.length === 0) return null;
  if (!(forward > 0) || !(ivPct > 0) || !(daysToExpiry > 0) || !(strikeStep > 0)) {
    return null;
  }

  const years = yearsFromDte(daysToExpiry);
  const sigma = ivPct / 100;
  const ranges = findProfitableRanges(legs, strikeStep, forward, {
    ivPct,
    daysToExpiry,
  });
  if (ranges.length === 0) return 0;

  let mass = 0;
  for (const { lo, hi } of ranges) {
    const pHi =
      hi === Number.POSITIVE_INFINITY ? 1 : lognormalCdf(hi, forward, sigma, years);
    const pLo = lo <= 0 ? 0 : lognormalCdf(lo, forward, sigma, years);
    mass += Math.max(0, pHi - pLo);
  }

  const pct = Math.max(0, Math.min(100, mass * 100));
  return Math.round(pct * 10) / 10;
}

/**
 * P(mark-to-model PnL > 0) at a target date before expiry.
 * Spot diffuses for `daysToTarget`; marks use `remainingDaysToExpiry` of option life.
 * Do not reuse expiry-PoP with a shorter DTE — that still scores intrinsic payoff.
 */
export function estimateTargetDateProbabilityOfProfit(
  legs: StrategyLeg[],
  {
    forward,
    ivPct,
    daysToTarget,
    remainingDaysToExpiry,
    strikeStep,
    legIvById,
  }: {
    forward: number;
    ivPct: number;
    daysToTarget: number;
    remainingDaysToExpiry: number;
    strikeStep: number;
    legIvById?: LegIvMap | null;
  },
): number | null {
  if (legs.length === 0) return null;
  if (!(forward > 0) || !(ivPct > 0) || !(strikeStep > 0)) return null;
  if (usableIvPct(ivPct) == null) return null;

  if (isExpiryHorizon(remainingDaysToExpiry)) {
    const totalDte = Math.max(daysToTarget + remainingDaysToExpiry, MIN_DTE_DAYS);
    return estimateProbabilityOfProfit(legs, {
      forward,
      ivPct,
      daysToExpiry: totalDte,
      strikeStep,
    });
  }

  const markOpts = {
    remainingDaysToExpiry,
    ivPct,
    legIvById,
  };

  // Near "now": point-mass at forward.
  if (!(daysToTarget > MIN_DTE_DAYS * 2)) {
    const pnl = totalMarkPnlAtSpot(legs, forward, markOpts);
    return pnl > 1e-9 ? 100 : 0;
  }

  const step = Math.max(1, strikeStep);
  const yearsMove = yearsFromDte(daysToTarget);
  const sigma = ivPct / 100;
  const volWing = forward * sigma * Math.sqrt(yearsMove) * 5;
  const maxStrike = Math.max(forward, ...legs.map((leg) => leg.strike));
  const hi =
    Math.ceil(
      Math.max(maxStrike + 80 * step, forward + volWing, forward * 1.4) / step,
    ) * step;

  let mass = 0;
  let inProfit = false;
  let start = 0;
  let prevSpot = 0;
  // Match expiry PoP: include the left tail (short puts are profitable near 0).
  let prevPnl = totalMarkPnlAtSpot(legs, 0, markOpts);

  for (let spot = 0; spot <= hi + 0.001; spot += step) {
    const pnl = totalMarkPnlAtSpot(legs, spot, markOpts);
    const profitable = pnl > 1e-9;
    if (profitable && !inProfit) {
      if (spot > 0 && prevPnl <= 0) {
        const denom = prevPnl - pnl;
        start =
          Math.abs(denom) < 1e-12
            ? spot
            : prevSpot + (prevPnl / denom) * (spot - prevSpot);
      } else {
        start = spot;
      }
      inProfit = true;
    } else if (!profitable && inProfit) {
      let end = spot;
      if (prevPnl > 0) {
        const denom = prevPnl - pnl;
        end =
          Math.abs(denom) < 1e-12
            ? spot
            : prevSpot + (prevPnl / denom) * (spot - prevSpot);
      }
      const pHi = lognormalCdf(end, forward, sigma, yearsMove);
      const pLo = start <= 0 ? 0 : lognormalCdf(start, forward, sigma, yearsMove);
      mass += Math.max(0, pHi - pLo);
      inProfit = false;
    }
    prevSpot = spot;
    prevPnl = pnl;
  }
  if (inProfit) {
    const pLo = start <= 0 ? 0 : lognormalCdf(start, forward, sigma, yearsMove);
    mass += Math.max(0, 1 - pLo);
  }

  const pct = Math.max(0, Math.min(100, mass * 100));
  return Math.round(pct * 10) / 10;
}

/**
 * Futures-style synthetic forward from ATM straddle: F ≈ K + (CE − PE).
 * Guard band scales with DTE so short-dated PoP isn't moved by stale ATM prints.
 */
export function syntheticForwardFromChain(
  rows: OptionsChainRow[],
  atm: number | null | undefined,
  spot: number | null | undefined,
  daysToExpiry: number | null | undefined = 7,
): number | null {
  const k = atm ?? rows.find((row) => row.is_atm)?.strike ?? null;
  if (k == null) return spot ?? null;
  const row = rows.find((item) => item.strike === k) ?? rows.find((item) => item.is_atm);
  const ce = row?.ce.ltp;
  const pe = row?.pe.ltp;
  if (ce != null && pe != null && ce > 0 && pe > 0) {
    const forward = Math.round((k + (ce - pe)) * 100) / 100;
    const anchor = spot != null && spot > 0 ? spot : k;
    const dte = daysToExpiry != null && daysToExpiry > 0 ? daysToExpiry : 7;
    // ~0.1% floor, ~1% at 30d, capped at old ±1.5% for quarterlies/LEAPS.
    const band =
      Math.min(0.015, Math.max(0.001, 0.12 * yearsFromDte(dte))) * anchor;
    if (Math.abs(forward - anchor) <= band) {
      return forward;
    }
  }
  return spot ?? k;
}

export type LegIvSource = "chain" | "parity" | "interp" | "ltp" | "atm";

export type ResolvedLegIv = {
  iv: number;
  source: LegIvSource;
};

export type BlendedStrategyIv = {
  /** Effective IV % for PoP (average of resolved leg IVs). */
  ivPct: number;
  legs: number;
  /** Legs that used raw own-side chain IV. */
  chainLegs: number;
  /** Legs that used same-strike opposite-side (parity) IV. */
  parityLegs: number;
  /** Legs filled by strike interpolation. */
  interpLegs: number;
  /** Legs filled by IV inverted from LTP. */
  ltpLegs: number;
  /** Legs that fell back to ATM IV. */
  atmFallbackLegs: number;
};

export type ResolveLegIvOptions = {
  atmIv?: number | null;
  forward?: number | null;
  daysToExpiry?: number | null;
  /** Prefer this premium (edited builder premium) over chain LTP. */
  premium?: number | null;
};

function chainSideQuote(
  rows: OptionsChainRow[],
  strike: number,
  type: OptionSide,
): { iv: number | null; ltp: number | null } {
  const row = rows.find((item) => item.strike === strike);
  if (!row) return { iv: null, ltp: null };
  const leg = type === "CE" ? row.ce : row.pe;
  const iv = usableIvPct(leg.iv);
  const ltp = leg.ltp != null && leg.ltp > 0 ? leg.ltp : null;
  return { iv, ltp };
}

/** True only for deep ITM (ill-conditioned for IV invert), not near-ATM ITM. */
export function isDeepItmForIvInvert(
  forward: number,
  strike: number,
  type: OptionSide,
  {
    premium,
    daysToExpiry,
    atmIv,
  }: {
    premium?: number | null;
    daysToExpiry?: number | null;
    atmIv?: number | null;
  } = {},
): boolean {
  if (!(forward > 0) || !(strike > 0)) return false;
  const intrinsic =
    type === "CE" ? Math.max(0, forward - strike) : Math.max(0, strike - forward);
  if (intrinsic <= 0) return false;

  if (premium != null && premium > 0 && intrinsic / premium > 0.9) {
    return true;
  }

  const years = yearsFromDte(daysToExpiry ?? 7);
  const sigma = Math.max(atmIv ?? 20, 1) / 100;
  const volMove = sigma * Math.sqrt(years);
  if (volMove < 1e-8) return intrinsic > 0;
  return Math.abs(Math.log(strike / forward)) > 1.5 * volMove;
}

function black76Price(
  forward: number,
  strike: number,
  years: number,
  sigma: number,
  type: OptionSide,
): number {
  if (!(forward > 0) || !(strike > 0) || !(years > 0) || !(sigma > 0)) return 0;
  const vol = sigma * Math.sqrt(years);
  if (vol < 1e-12) {
    return type === "CE" ? Math.max(0, forward - strike) : Math.max(0, strike - forward);
  }
  const d1 = (Math.log(forward / strike) + 0.5 * sigma * sigma * years) / vol;
  const d2 = d1 - vol;
  if (type === "CE") return forward * normCdf(d1) - strike * normCdf(d2);
  return strike * normCdf(-d2) - forward * normCdf(-d1);
}

export type Black76Greeks = {
  /** Forward delta (r=0 Black-76). */
  delta: number;
  /** ∂²V/∂F² per point of forward. */
  gamma: number;
  /** Premium change per calendar day. */
  theta: number;
  /** Premium change per 1 vol point (1%). */
  vega: number;
};

/**
 * Black-76 greeks with r=0 (index F&O style).
 * Theta is per calendar day when DTE ≥ 1d, otherwise per hour (avoids /day blow-ups).
 * Vega is per 1 percentage point of IV.
 */
export function black76Greeks({
  forward,
  strike,
  daysToExpiry,
  ivPct,
  type,
}: {
  forward: number;
  strike: number;
  daysToExpiry: number;
  ivPct: number;
  type: OptionSide;
}): Black76Greeks | null {
  if (
    !(forward > 0) ||
    !(strike > 0) ||
    !(daysToExpiry > 0) ||
    usableIvPct(ivPct) == null
  ) {
    return null;
  }
  const years = yearsFromDte(daysToExpiry);
  const sigma = ivPct / 100;
  const sqrtT = Math.sqrt(years);
  const vol = sigma * sqrtT;
  if (vol < 1e-12) return null;

  const d1 = (Math.log(forward / strike) + 0.5 * sigma * sigma * years) / vol;
  const density = normPdf(d1);
  const delta = type === "CE" ? normCdf(d1) : normCdf(d1) - 1;
  const gamma = density / (forward * vol);
  // Calendar-day theta (Black-76, r=0): leading term is the same for CE/PE.
  const thetaPerDay = (-(forward * density * sigma) / (2 * sqrtT)) / 365;
  const theta = daysToExpiry < 1 ? thetaPerDay / 24 : thetaPerDay;
  const vega = (forward * density * sqrtT) / 100;

  return {
    delta: round4(delta),
    gamma: Math.round(gamma * 1e6) / 1e6,
    theta: round2(theta),
    vega: round2(vega),
  };
}

export type StrategyLegGreeks = {
  id: string;
  delta: number | null;
  gamma: number | null;
  theta: number | null;
  vega: number | null;
};

export type StrategyGreeksSummary = {
  legs: StrategyLegGreeks[];
  netDelta: number | null;
  netGamma: number | null;
  netTheta: number | null;
  netVega: number | null;
  /** True when theta is reported per hour (DTE &lt; 1). */
  thetaPerHour: boolean;
};

/** Per-leg + net Black-76 greeks using the same IV resolution as PoP. */
export function estimateStrategyGreeks(
  legs: StrategyLeg[],
  rows: OptionsChainRow[],
  {
    forward,
    daysToExpiry,
    atmIv,
  }: {
    forward: number | null | undefined;
    daysToExpiry: number | null | undefined;
    atmIv?: number | null;
  },
): StrategyGreeksSummary {
  const thetaPerHour = daysToExpiry != null && daysToExpiry > 0 && daysToExpiry < 1;
  const empty: StrategyGreeksSummary = {
    legs: [],
    netDelta: null,
    netGamma: null,
    netTheta: null,
    netVega: null,
    thetaPerHour,
  };
  if (
    legs.length === 0 ||
    forward == null ||
    !(forward > 0) ||
    daysToExpiry == null ||
    !(daysToExpiry > 0)
  ) {
    return empty;
  }

  const outLegs: StrategyLegGreeks[] = [];
  let netDelta = 0;
  let netGamma = 0;
  let netTheta = 0;
  let netVega = 0;
  let haveModel = true;

  for (const leg of legs) {
    const resolved = resolveLegIv(rows, leg, {
      atmIv,
      forward,
      daysToExpiry,
      premium: leg.premium,
    });
    const model =
      resolved != null
        ? black76Greeks({
            forward,
            strike: leg.strike,
            daysToExpiry,
            ivPct: resolved.iv,
            type: leg.type,
          })
        : null;

    // Model-only Δ — never mix broker chain delta with Black-76.
    if (model == null) {
      haveModel = false;
      outLegs.push({
        id: leg.id,
        delta: null,
        gamma: null,
        theta: null,
        vega: null,
      });
      continue;
    }

    const sign = legSign(leg.side);
    netDelta += sign * model.delta * leg.qty;
    netGamma += sign * model.gamma * leg.qty;
    netTheta += sign * model.theta * leg.qty;
    netVega += sign * model.vega * leg.qty;
    outLegs.push({
      id: leg.id,
      delta: model.delta,
      gamma: model.gamma,
      theta: model.theta,
      vega: model.vega,
    });
  }

  return {
    legs: outLegs,
    netDelta: haveModel ? round4(netDelta) : null,
    netGamma: haveModel ? Math.round(netGamma * 1e6) / 1e6 : null,
    netTheta: haveModel ? round2(netTheta) : null,
    netVega: haveModel ? round2(netVega) : null,
    thetaPerHour,
  };
}

/**
 * Invert Black-76 IV (%) from option LTP. Returns null if premium is outside
 * arb bounds or search fails.
 */
export function impliedVolFromLtp({
  premium,
  forward,
  strike,
  type,
  daysToExpiry,
}: {
  premium: number;
  forward: number;
  strike: number;
  type: OptionSide;
  daysToExpiry: number;
}): number | null {
  if (!(premium > 0) || !(forward > 0) || !(strike > 0) || !(daysToExpiry > 0)) {
    return null;
  }
  const years = yearsFromDte(daysToExpiry);
  const intrinsic = type === "CE" ? Math.max(0, forward - strike) : Math.max(0, strike - forward);
  // Discounted forward measure with r=0 → premium should sit above intrinsic.
  if (premium + 1e-8 < intrinsic) return null;
  const maxPrice = type === "CE" ? forward : strike;
  if (premium > maxPrice * 1.0001) return null;

  let lo = 1e-4; // 0.01%
  let hi = 5; // 500%
  const target = premium;
  const priceLo = black76Price(forward, strike, years, lo, type);
  let priceHi = black76Price(forward, strike, years, hi, type);
  if (target > priceHi + 1e-6) {
    hi = 10; // 1000%
    priceHi = black76Price(forward, strike, years, hi, type);
  }
  // Still outside the monotone price bracket → cannot invert reliably.
  if (target < priceLo - 1e-6 || target > priceHi + 1e-6) return null;

  let mid = lo;
  for (let i = 0; i < 60; i += 1) {
    mid = 0.5 * (lo + hi);
    const price = black76Price(forward, strike, years, mid, type);
    if (Math.abs(price - target) < 1e-4) break;
    if (price > target) hi = mid;
    else lo = mid;
  }
  const ivPct = mid * 100;
  if (!(ivPct > 0) || ivPct > 1000) return null;
  return Math.round(ivPct * 100) / 100;
}

/**
 * Linear IV interpolation across strikes for one option side (CE or PE).
 * Only interpolates strictly between known points — no wing clamp — so
 * IV-from-LTP can run for strikes outside the observed smile.
 */
export function interpolateSideIv(
  rows: OptionsChainRow[],
  strike: number,
  type: OptionSide,
): number | null {
  const points = rows
    .map((row) => {
      const iv = type === "CE" ? row.ce.iv : row.pe.iv;
      return usableIvPct(iv) != null ? { strike: row.strike, iv: iv as number } : null;
    })
    .filter((item): item is { strike: number; iv: number } => item != null)
    .sort((a, b) => a.strike - b.strike);

  if (points.length === 0) return null;
  if (points.length === 1) {
    return points[0].strike === strike ? points[0].iv : null;
  }

  if (strike < points[0].strike || strike > points[points.length - 1].strike) {
    return null;
  }

  for (let i = 0; i < points.length - 1; i += 1) {
    const left = points[i];
    const right = points[i + 1];
    if (strike === left.strike) return left.iv;
    if (strike === right.strike) return right.iv;
    if (strike > left.strike && strike < right.strike) {
      const span = right.strike - left.strike;
      if (span <= 0) return left.iv;
      const t = (strike - left.strike) / span;
      return left.iv + t * (right.iv - left.iv);
    }
  }
  return null;
}

/**
 * Resolve one leg's IV: own-side chain → same-strike parity → smile interp →
 * IV-from-LTP (skip deep ITM) → ATM.
 */
export function resolveLegIv(
  rows: OptionsChainRow[],
  leg: Pick<StrategyLeg, "strike" | "type">,
  opts: ResolveLegIvOptions = {},
): ResolvedLegIv | null {
  const quote = chainSideQuote(rows, leg.strike, leg.type);
  if (quote.iv != null) return { iv: quote.iv, source: "chain" };

  // Put-call parity: same-strike opposite IV is preferred over interp/LTP.
  const oppositeType: OptionSide = leg.type === "CE" ? "PE" : "CE";
  const opposite = chainSideQuote(rows, leg.strike, oppositeType);
  if (opposite.iv != null) return { iv: opposite.iv, source: "parity" };

  const interp = interpolateSideIv(rows, leg.strike, leg.type);
  if (interp != null) return { iv: interp, source: "interp" };

  const premium =
    opts.premium != null && opts.premium > 0 ? opts.premium : quote.ltp;
  const forward = opts.forward;
  const daysToExpiry = opts.daysToExpiry;
  const deepItm =
    forward != null &&
    forward > 0 &&
    isDeepItmForIvInvert(forward, leg.strike, leg.type, {
      premium,
      daysToExpiry,
      atmIv: opts.atmIv,
    });

  if (
    !deepItm &&
    premium != null &&
    premium > 0 &&
    forward != null &&
    forward > 0 &&
    daysToExpiry != null &&
    daysToExpiry > 0
  ) {
    const fromLtp = impliedVolFromLtp({
      premium,
      forward,
      strike: leg.strike,
      type: leg.type,
      daysToExpiry,
    });
    const usableLtp = usableIvPct(fromLtp);
    if (usableLtp != null) return { iv: usableLtp, source: "ltp" };
  }

  const atm = usableIvPct(opts.atmIv);
  if (atm != null) return { iv: atm, source: "atm" };
  return null;
}

/** Vega-weighted average of resolved leg IVs (equal-weight fallback). */
export function blendStrategyIv(
  legs: StrategyLeg[],
  rows: OptionsChainRow[],
  atmIv: number | null | undefined,
  opts?: {
    forward?: number | null;
    daysToExpiry?: number | null;
  },
): BlendedStrategyIv | null {
  if (legs.length === 0) return null;

  const resolved: ResolvedLegIv[] = [];
  for (const leg of legs) {
    const item = resolveLegIv(rows, leg, {
      atmIv,
      forward: opts?.forward,
      daysToExpiry: opts?.daysToExpiry,
      premium: leg.quoteMissing ? null : leg.premium,
    });
    if (!item) return null;
    resolved.push(item);
  }

  const forward = opts?.forward;
  const daysToExpiry = opts?.daysToExpiry;
  const canVegaWeight =
    forward != null &&
    forward > 0 &&
    daysToExpiry != null &&
    daysToExpiry > 0;

  let ivPct: number;
  if (canVegaWeight) {
    const weights: number[] = [];
    let allWeighted = true;
    for (let i = 0; i < resolved.length; i++) {
      const leg = legs[i];
      const item = resolved[i];
      const model = black76Greeks({
        forward,
        strike: leg.strike,
        daysToExpiry,
        ivPct: item.iv,
        type: leg.type,
      });
      if (model == null || !Number.isFinite(model.vega) || !(leg.qty > 0)) {
        allWeighted = false;
        break;
      }
      weights.push(Math.abs(model.vega) * leg.qty);
    }
    if (allWeighted && weights.some((w) => w > 0)) {
      let weighted = 0;
      let weightSum = 0;
      for (let i = 0; i < resolved.length; i++) {
        weighted += resolved[i].iv * weights[i];
        weightSum += weights[i];
      }
      ivPct = weighted / weightSum;
    } else {
      ivPct =
        resolved.reduce((sum, item) => sum + item.iv, 0) / resolved.length;
    }
  } else {
    ivPct =
      resolved.reduce((sum, item) => sum + item.iv, 0) / resolved.length;
  }

  return {
    ivPct: Math.round(ivPct * 100) / 100,
    legs: resolved.length,
    chainLegs: resolved.filter((item) => item.source === "chain").length,
    parityLegs: resolved.filter((item) => item.source === "parity").length,
    interpLegs: resolved.filter((item) => item.source === "interp").length,
    ltpLegs: resolved.filter((item) => item.source === "ltp").length,
    atmFallbackLegs: resolved.filter((item) => item.source === "atm").length,
  };
}

export function chainLegPremium(
  rows: OptionsChainRow[],
  strike: number,
  type: OptionSide,
): { premium: number | null; delta: number | null } {
  const row = rows.find((item) => item.strike === strike);
  if (!row) return { premium: null, delta: null };
  const leg = type === "CE" ? row.ce : row.pe;
  return { premium: leg.ltp, delta: leg.delta };
}

/** Chain click cycle: add buy → flip sell → remove. */
export function cycleChainLeg(
  legs: StrategyLeg[],
  rows: OptionsChainRow[],
  strike: number,
  type: OptionSide,
): StrategyLeg[] {
  const existing = legs.find((leg) => leg.strike === strike && leg.type === type);
  if (!existing) {
    const { premium, delta } = chainLegPremium(rows, strike, type);
    return [
      ...legs,
      {
        id: `chain-${type}-${strike}`,
        side: "buy",
        type,
        strike,
        premium: premium ?? 0,
        qty: 1,
        delta,
        quoteMissing: premium == null,
      },
    ];
  }
  if (existing.side === "buy") {
    return legs.map((leg) =>
      leg.strike === strike && leg.type === type ? { ...leg, side: "sell" } : leg,
    );
  }
  return legs.filter((leg) => !(leg.strike === strike && leg.type === type));
}

function mkLeg(
  partial: Omit<StrategyLeg, "id"> & { id?: string },
  index: number,
): StrategyLeg {
  return {
    id: partial.id ?? `leg-${index}`,
    side: partial.side,
    type: partial.type,
    strike: partial.strike,
    premium: partial.premium,
    qty: partial.qty,
    delta: partial.delta ?? null,
    quoteMissing: partial.quoteMissing ?? false,
  };
}

type LegFromChainParams = {
  side: TradeSide;
  type: OptionSide;
  strike: number;
  qty: number;
  index: number;
};

function legFromChain(rows: OptionsChainRow[], params: LegFromChainParams): StrategyLeg {
  const { side, type, strike, qty, index } = params;
  const { premium, delta } = chainLegPremium(rows, strike, type);
  const quoteMissing = premium == null;
  return mkLeg(
    {
      side,
      type,
      strike,
      premium: premium ?? 0,
      qty,
      delta,
      quoteMissing,
    },
    index,
  );
}

export function buildStrategyFromTemplate(
  templateId: StrategyTemplateId,
  {
    atm,
    strikeStep,
    rows,
    shiftSteps = 0,
    widthSteps = 1,
  }: {
    atm: number;
    strikeStep: number;
    rows: OptionsChainRow[];
    shiftSteps?: number;
    widthSteps?: number;
  },
): StrategyLeg[] {
  const step = Math.max(1, strikeStep);
  const center = atm + shiftSteps * step;
  const width = Math.max(1, widthSteps) * step;

  switch (templateId) {
    case "long_ce":
      return [legFromChain(rows, { side: "buy", type: "CE", strike: center, qty: 1, index: 0 })];
    case "long_pe":
      return [legFromChain(rows, { side: "buy", type: "PE", strike: center, qty: 1, index: 0 })];
    case "short_ce":
      return [legFromChain(rows, { side: "sell", type: "CE", strike: center, qty: 1, index: 0 })];
    case "short_pe":
      return [legFromChain(rows, { side: "sell", type: "PE", strike: center, qty: 1, index: 0 })];
    case "long_straddle":
      return [
        legFromChain(rows, { side: "buy", type: "CE", strike: center, qty: 1, index: 0 }),
        legFromChain(rows, { side: "buy", type: "PE", strike: center, qty: 1, index: 1 }),
      ];
    case "short_straddle":
      return [
        legFromChain(rows, { side: "sell", type: "CE", strike: center, qty: 1, index: 0 }),
        legFromChain(rows, { side: "sell", type: "PE", strike: center, qty: 1, index: 1 }),
      ];
    case "long_strangle":
      return [
        legFromChain(rows, {
          side: "buy",
          type: "CE",
          strike: center + width,
          qty: 1,
          index: 0,
        }),
        legFromChain(rows, {
          side: "buy",
          type: "PE",
          strike: center - width,
          qty: 1,
          index: 1,
        }),
      ];
    case "bull_call_spread":
      return [
        legFromChain(rows, { side: "buy", type: "CE", strike: center, qty: 1, index: 0 }),
        legFromChain(rows, {
          side: "sell",
          type: "CE",
          strike: center + width,
          qty: 1,
          index: 1,
        }),
      ];
    case "bear_put_spread":
      return [
        legFromChain(rows, { side: "buy", type: "PE", strike: center, qty: 1, index: 0 }),
        legFromChain(rows, {
          side: "sell",
          type: "PE",
          strike: center - width,
          qty: 1,
          index: 1,
        }),
      ];
    case "bull_put_spread":
      return [
        legFromChain(rows, { side: "sell", type: "PE", strike: center, qty: 1, index: 0 }),
        legFromChain(rows, {
          side: "buy",
          type: "PE",
          strike: center - width,
          qty: 1,
          index: 1,
        }),
      ];
    case "bear_call_spread":
      return [
        legFromChain(rows, { side: "sell", type: "CE", strike: center, qty: 1, index: 0 }),
        legFromChain(rows, {
          side: "buy",
          type: "CE",
          strike: center + width,
          qty: 1,
          index: 1,
        }),
      ];
    case "iron_condor": {
      const putShort = center - width;
      const putLong = center - width * 2;
      const callShort = center + width;
      const callLong = center + width * 2;
      return [
        legFromChain(rows, { side: "sell", type: "PE", strike: putShort, qty: 1, index: 0 }),
        legFromChain(rows, { side: "buy", type: "PE", strike: putLong, qty: 1, index: 1 }),
        legFromChain(rows, { side: "sell", type: "CE", strike: callShort, qty: 1, index: 2 }),
        legFromChain(rows, { side: "buy", type: "CE", strike: callLong, qty: 1, index: 3 }),
      ];
    }
    case "iron_butterfly":
      return [
        legFromChain(rows, { side: "buy", type: "PE", strike: center - width, qty: 1, index: 0 }),
        legFromChain(rows, { side: "sell", type: "PE", strike: center, qty: 1, index: 1 }),
        legFromChain(rows, { side: "sell", type: "CE", strike: center, qty: 1, index: 2 }),
        legFromChain(rows, { side: "buy", type: "CE", strike: center + width, qty: 1, index: 3 }),
      ];
    case "long_butterfly_ce":
      return [
        legFromChain(rows, { side: "buy", type: "CE", strike: center - width, qty: 1, index: 0 }),
        legFromChain(rows, { side: "sell", type: "CE", strike: center, qty: 2, index: 1 }),
        legFromChain(rows, { side: "buy", type: "CE", strike: center + width, qty: 1, index: 2 }),
      ];
    case "call_ratio":
      return [
        legFromChain(rows, { side: "buy", type: "CE", strike: center, qty: 1, index: 0 }),
        legFromChain(rows, {
          side: "sell",
          type: "CE",
          strike: center + width,
          qty: 2,
          index: 1,
        }),
      ];
    case "put_ratio":
      return [
        legFromChain(rows, { side: "buy", type: "PE", strike: center, qty: 1, index: 0 }),
        legFromChain(rows, {
          side: "sell",
          type: "PE",
          strike: center - width,
          qty: 2,
          index: 1,
        }),
      ];
    case "calendar_call":
      // Gated until Lab supports dual-expiry chains — do not fake same-month legs.
      return [];
    default:
      return [];
  }
}
