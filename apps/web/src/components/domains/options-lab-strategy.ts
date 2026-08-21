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

const INDEX_OPTION_ROOTS = ["MIDCPNIFTY", "BANKNIFTY", "FINNIFTY", "NIFTY", "SENSEX"] as const;

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

/** Floor DTE for σ√T numerics — ~30 minutes, not half a day. */
export const MIN_DTE_DAYS = 1 / 48;

export function yearsFromDte(daysToExpiry: number): number {
  return Math.max(daysToExpiry, MIN_DTE_DAYS) / 365;
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

/** Parse weekly digit token `25807` (YYMDD) or `250807` (YYMMDD). */
export function expiryDateFromWeeklyDigitsCode(code: string): Date | null {
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

export function expiryDateFromExpiryCode(code: string): Date | null {
  const raw = code.trim().toUpperCase();
  if (!raw) return null;
  return (
    expiryDateFromMonthlyCode(raw) ??
    expiryDateFromWeeklyAlphaCode(raw) ??
    expiryDateFromWeeklyDigitsCode(raw)
  );
}

/**
 * Extract expiry code from NSE-style option symbols, e.g.
 * `NFO:NIFTY26AUG24500CE`, `NIFTY25N1124500CE`, `NIFTY2580724500CE`.
 */
export function expiryCodeFromOptionSymbol(symbol: string): string | null {
  let raw = symbol.trim().toUpperCase();
  if (!raw) return null;
  if (raw.includes(":")) raw = raw.split(":", 2)[1] ?? raw;
  if (!raw.endsWith("CE") && !raw.endsWith("PE")) return null;
  const body = raw.slice(0, -2);

  const monthly = body.match(/^([A-Z]+)(\d{2}[A-Z]{3})(\d+)$/);
  if (monthly) return monthly[2];

  const weeklyAlpha = body.match(/^([A-Z]+)(\d{2}[OND]\d{2})(\d+)$/);
  if (weeklyAlpha) return weeklyAlpha[2];

  for (const root of INDEX_OPTION_ROOTS) {
    if (!body.startsWith(root)) continue;
    const tail = body.slice(root.length);
    if (!/^\d+$/.test(tail)) continue;
    for (const strikeLen of [5, 4, 6]) {
      if (tail.length <= strikeLen + 4) continue;
      const expiryPart = tail.slice(0, -strikeLen);
      if (/^\d+$/.test(expiryPart) && expiryPart.length >= 5) return expiryPart;
    }
  }
  return null;
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
  const side = raw.slice(-2) as "CE" | "PE";
  const body = raw.slice(0, -2);

  let underlier: string | null = null;
  let expiryCode: string | null = null;
  let strike: string | null = null;
  let monthlyToken = false;

  const monthly = body.match(/^([A-Z]+)(\d{2}[A-Z]{3})(\d+)$/);
  if (monthly) {
    underlier = monthly[1];
    expiryCode = monthly[2];
    strike = monthly[3];
    monthlyToken = true;
  } else {
    const weeklyAlpha = body.match(/^([A-Z]+)(\d{2}[OND]\d{2})(\d+)$/);
    if (weeklyAlpha) {
      underlier = weeklyAlpha[1];
      expiryCode = weeklyAlpha[2];
      strike = weeklyAlpha[3];
    } else {
      for (const root of INDEX_OPTION_ROOTS) {
        if (!body.startsWith(root)) continue;
        const tail = body.slice(root.length);
        if (!/^\d+$/.test(tail)) continue;
        for (const strikeLen of [5, 4, 6]) {
          if (tail.length <= strikeLen + 4) continue;
          const expiryPart = tail.slice(0, -strikeLen);
          const strikePart = tail.slice(-strikeLen);
          if (/^\d+$/.test(expiryPart) && expiryPart.length >= 5) {
            underlier = root;
            expiryCode = expiryPart;
            strike = String(Number(strikePart));
            break;
          }
        }
        if (underlier) break;
      }
    }
  }

  if (!underlier || !expiryCode || !strike) return raw;

  let expiryLabel: string;
  if (monthlyToken) {
    expiryLabel = formatMonthlyCodeLabel(expiryCode) ?? expiryCode;
  } else {
    const expiry = expiryDateFromExpiryCode(expiryCode);
    expiryLabel = expiry ? formatExpiryDayLabel(expiry) : expiryCode;
  }
  return `${underlier} ${expiryLabel} ${strike} ${side}`;
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

/** Average resolved leg IVs into one effective σ* for PoP. */
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

  const ivPct =
    resolved.reduce((sum, item) => sum + item.iv, 0) / resolved.length;

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
