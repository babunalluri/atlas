"use client";

import { useMemo } from "react";

import type { StrategyTemplateId } from "@/components/domains/options-lab-strategy";
import type { OptionsScreenerRow, OptionsScreenerSnapshot } from "@/lib/api/admin";
import { cn } from "@/lib/utils";

type Idea = {
  row: OptionsScreenerRow;
  templateId: StrategyTemplateId;
  templateLabel: string;
  score: number;
  reason: string;
};

function rankIdeas(rows: OptionsScreenerRow[]): Idea[] {
  const ideas: Idea[] = [];
  for (const row of rows) {
    if (row.error || row.pcr == null) continue;
    const pcr = row.pcr;
    const ivp = row.ivp;
    const oiChg = row.oi_pct_chg ?? 0;
    // Do not invent IVP=50 when missing — PCR/IV ideas need a real percentile.
    if (ivp != null && pcr < 0.85 && ivp < 60) {
      ideas.push({
        row,
        templateId: "bull_call_spread",
        templateLabel: "Bull Call Spread",
        score: (1 - pcr) * 40 + (60 - Math.min(ivp, 60)) * 0.5 + Math.max(0, oiChg),
        reason: `PCR ${pcr.toFixed(2)} · IVP ${ivp.toFixed(0)}`,
      });
    } else if (ivp != null && pcr > 1.15 && ivp < 60) {
      ideas.push({
        row,
        templateId: "bear_put_spread",
        templateLabel: "Bear Put Spread",
        score: (pcr - 1) * 40 + (60 - Math.min(ivp, 60)) * 0.5 + Math.max(0, oiChg),
        reason: `PCR ${pcr.toFixed(2)} · IVP ${ivp.toFixed(0)}`,
      });
    } else if (ivp != null && ivp > 70) {
      ideas.push({
        row,
        templateId: "iron_condor",
        templateLabel: "Iron Condor",
        score: ivp - 50 + Math.abs(oiChg) * 0.3,
        reason: `Elevated IVP ${ivp.toFixed(0)}`,
      });
    } else if (Math.abs(oiChg) > 5) {
      ideas.push({
        row,
        templateId: "long_straddle",
        templateLabel: "Long Straddle",
        score: Math.abs(oiChg),
        reason: `OI Δ ${oiChg > 0 ? "+" : ""}${oiChg.toFixed(1)}%`,
      });
    }
  }
  return ideas.sort((a, b) => b.score - a.score).slice(0, 24);
}

export function OptionsLabIdeasPanel({
  snapshot,
  onApplyIdea,
}: {
  snapshot: OptionsScreenerSnapshot | null;
  onApplyIdea: (row: OptionsScreenerRow, templateId: StrategyTemplateId) => void;
}) {
  const ideas = useMemo(() => rankIdeas(snapshot?.rows ?? []), [snapshot?.rows]);

  return (
    <div className="flex flex-col gap-3 pt-3">
      <p className="text-sm text-slate-muted">
        Heuristic ranks (not live EV). Click opens the chain and loads the suggested template into
        the strategy rail.
      </p>
      {ideas.length === 0 ? (
        <p className="py-10 text-center text-sm text-slate-muted">
          No ideas yet — refresh Screener / Heat map first.
        </p>
      ) : (
        <ul className="divide-y divide-line rounded-lg border border-line">
          {ideas.map((idea) => (
            <li key={`${idea.row.underlying_symbol}-${idea.templateId}`}>
              <button
                type="button"
                onClick={() => onApplyIdea(idea.row, idea.templateId)}
                className="flex w-full items-center justify-between gap-3 px-3 py-2.5 text-left hover:bg-raised/50"
              >
                <div className="min-w-0">
                  <p className="truncate text-sm font-semibold text-ink">
                    {idea.row.underlying_label}{" "}
                    <span className="font-normal text-slate-muted">· {idea.templateLabel}</span>
                  </p>
                  <p className="text-xs text-slate-muted">{idea.reason}</p>
                </div>
                <span className={cn("shrink-0 text-xs tabular-nums text-teal")}>
                  {idea.score.toFixed(1)}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
