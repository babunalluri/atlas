import type { StrategyLeg } from "@/components/domains/options-lab-strategy";
import type { OptionsPortfolioLeg } from "@/lib/api/admin";

export function strategyLegsToPortfolioLegs(legs: StrategyLeg[]): OptionsPortfolioLeg[] {
  return legs.map((leg) => ({
    id: leg.id,
    side: leg.side,
    type: leg.type,
    strike: leg.strike,
    qty: leg.qty,
    entry_premium: leg.premium,
  }));
}

export function formatMtm(value: number | null | undefined) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const rounded = Math.round(value * 100) / 100;
  if (rounded > 0) return `+${rounded.toFixed(2)}`;
  return rounded.toFixed(2);
}

export function mtmTone(value: number | null | undefined): "profit" | "loss" | "flat" {
  if (value == null || Number.isNaN(value) || value === 0) return "flat";
  return value > 0 ? "profit" : "loss";
}
