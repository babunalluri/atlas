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
  | "iron_condor";

export type StrategyTemplate = {
  id: StrategyTemplateId;
  label: string;
  hint: string;
  usesWidth: boolean;
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
  { id: "iron_condor", label: "Iron Condor", hint: "Range / short vol", usesWidth: true },
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

export function buildPayoffCurve(
  legs: StrategyLeg[],
  {
    strikeStep,
    wings = 18,
  }: {
    spot?: number;
    strikeStep: number;
    wings?: number;
  },
): PayoffPoint[] {
  if (legs.length === 0) return [];
  const step = Math.max(1, strikeStep);
  const legStrikes = legs.map((leg) => leg.strike);
  const minLeg = Math.min(...legStrikes);
  const maxLeg = Math.max(...legStrikes);
  const gridLo = Math.floor((minLeg - wings * step) / step) * step;
  const gridHi = Math.ceil((maxLeg + wings * step) / step) * step;
  const points: PayoffPoint[] = [];
  for (let s = gridLo; s <= gridHi + 0.001; s += step) {
    points.push({ spot: s, pnl: totalPayoffAtSpot(legs, s) });
  }
  return points;
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
    default:
      return [];
  }
}
