/**
 * Trade Ideas ranking with model PoP / E[PnL] (Wave 3).
 * Uses Lab probability helpers — not live fill EV / SmartPricing.
 */

import {
  buildStrategyFromTemplate,
  daysToExpiryFromFutSymbol,
  estimatePayoffDistributionStats,
  payoffExtremes,
  summarizeStrategy,
  buildPayoffCurve,
  type StrategyTemplateId,
} from "@/components/domains/options-lab-strategy";
import type { OptionsChainRow, OptionsScreenerRow } from "@/lib/api/admin";

export type IdeaFilters = {
  /** indices | equities | all — when row has no universe, treated as indices. */
  universe?: "indices" | "equities" | "all";
  minIvp?: number | null;
  maxIvp?: number | null;
  minPcr?: number | null;
  maxPcr?: number | null;
  minDte?: number | null;
  maxDte?: number | null;
  minPop?: number | null;
};

export type RankedIdea = {
  row: OptionsScreenerRow;
  templateId: StrategyTemplateId;
  templateLabel: string;
  score: number;
  reason: string;
  pop: number | null;
  expectedPnl: number | null;
  rewardRisk: number | null;
  dte: number | null;
};

const INDEX_HINTS = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX"];

function rowUniverse(row: OptionsScreenerRow): "indices" | "equities" {
  const sym = `${row.underlying_symbol} ${row.underlying_label}`.toUpperCase();
  if (INDEX_HINTS.some((h) => sym.includes(h))) return "indices";
  return "equities";
}

function passesFilters(
  row: OptionsScreenerRow,
  dte: number | null,
  filters: IdeaFilters,
): boolean {
  const uni = filters.universe ?? "all";
  if (uni !== "all" && rowUniverse(row) !== uni) return false;
  if (row.ivp == null) return false;
  if (filters.minIvp != null && row.ivp < filters.minIvp) return false;
  if (filters.maxIvp != null && row.ivp > filters.maxIvp) return false;
  if (row.pcr == null) return false;
  if (filters.minPcr != null && row.pcr < filters.minPcr) return false;
  if (filters.maxPcr != null && row.pcr > filters.maxPcr) return false;
  if (filters.minDte != null && (dte == null || dte < filters.minDte)) return false;
  if (filters.maxDte != null && (dte == null || dte > filters.maxDte)) return false;
  return true;
}

function suggestTemplate(row: OptionsScreenerRow): {
  templateId: StrategyTemplateId;
  templateLabel: string;
  reason: string;
} | null {
  const pcr = row.pcr;
  const ivp = row.ivp;
  const oiChg = row.oi_pct_chg ?? 0;
  if (pcr == null || ivp == null) return null;
  if (pcr < 0.85 && ivp < 60) {
    return {
      templateId: "bull_call_spread",
      templateLabel: "Bull Call Spread",
      reason: `PCR ${pcr.toFixed(2)} · IVP ${ivp.toFixed(0)}`,
    };
  }
  if (pcr > 1.15 && ivp < 60) {
    return {
      templateId: "bear_put_spread",
      templateLabel: "Bear Put Spread",
      reason: `PCR ${pcr.toFixed(2)} · IVP ${ivp.toFixed(0)}`,
    };
  }
  if (ivp > 70) {
    return {
      templateId: "iron_condor",
      templateLabel: "Iron Condor",
      reason: `Elevated IVP ${ivp.toFixed(0)}`,
    };
  }
  if (Math.abs(oiChg) > 5) {
    return {
      templateId: "long_straddle",
      templateLabel: "Long Straddle",
      reason: `OI Δ ${oiChg > 0 ? "+" : ""}${oiChg.toFixed(1)}%`,
    };
  }
  return null;
}

/** Synthetic chain rows at ATM ± width for template premium stubs. */
function stubRows(atm: number, strikeStep: number, atmIv: number): OptionsChainRow[] {
  const step = Math.max(1, strikeStep);
  const rows: OptionsChainRow[] = [];
  for (let i = -4; i <= 4; i += 1) {
    const strike = atm + i * step;
    const dist = Math.abs(i);
    const ce = Math.max(1, 80 - dist * 12);
    const pe = Math.max(1, 70 - dist * 11);
    rows.push({
      strike,
      is_atm: i === 0,
      ce: {
        symbol: `CE${strike}`,
        ltp: ce,
        oi: null,
        volume: null,
        iv: atmIv,
        delta: null,
      },
      pe: {
        symbol: `PE${strike}`,
        ltp: pe,
        oi: null,
        volume: null,
        iv: atmIv,
        delta: null,
      },
    });
  }
  return rows;
}

export function rankIdeasWithPop(
  rows: OptionsScreenerRow[],
  filters: IdeaFilters = {},
  now: Date = new Date(),
): RankedIdea[] {
  const ideas: RankedIdea[] = [];
  for (const row of rows) {
    if (row.error) continue;
    if (row.pcr == null || row.ivp == null) continue;
    if (row.spot == null || row.atm == null || row.atm_iv == null) continue;
    if (!(row.spot > 0) || !(row.atm > 0) || !(row.atm_iv > 0)) continue;

    const dte = daysToExpiryFromFutSymbol(row.fut_symbol, now);
    if (!passesFilters(row, dte, filters)) continue;
    if (dte == null || !(dte > 0)) continue;

    const suggestion = suggestTemplate(row);
    if (!suggestion) continue;

    const strikeStep = 50;
    const legs = buildStrategyFromTemplate(suggestion.templateId, {
      atm: row.atm,
      strikeStep,
      rows: stubRows(row.atm, strikeStep, row.atm_iv),
      widthSteps: 1,
    });
    if (!legs.length) continue;

    const extremes = payoffExtremes(legs);
    const dist = estimatePayoffDistributionStats(legs, {
      forward: row.spot,
      ivPct: row.atm_iv,
      daysToExpiry: dte,
      strikeStep,
      maxProfit: extremes.maxProfit,
    });
    if (!dist) continue;
    if (filters.minPop != null && dist.pop < filters.minPop) continue;

    let rewardRisk: number | null = null;
    if (
      extremes.maxProfit != null &&
      extremes.maxLoss != null &&
      extremes.maxLoss < 0 &&
      Number.isFinite(extremes.maxProfit)
    ) {
      rewardRisk = Math.round((extremes.maxProfit / Math.abs(extremes.maxLoss)) * 100) / 100;
    }

    // Score: PoP weight + EV sign + heuristic bump from original reason strength.
    const curve = buildPayoffCurve(legs, { spot: row.spot, strikeStep, wings: 8 });
    const summary = summarizeStrategy(legs, curve);
    const net = summary.netPremium;
    const score =
      dist.pop * 0.45 +
      Math.max(-40, Math.min(40, dist.expectedPnl)) * 0.35 +
      Math.abs(row.oi_pct_chg ?? 0) * 0.2 +
      (net < 0 ? 5 : 0);

    ideas.push({
      row,
      templateId: suggestion.templateId,
      templateLabel: suggestion.templateLabel,
      score,
      reason: suggestion.reason,
      pop: dist.pop,
      expectedPnl: dist.expectedPnl,
      rewardRisk,
      dte,
    });
  }
  return ideas.sort((a, b) => b.score - a.score).slice(0, 24);
}
