"use client";

import { useMemo, useState } from "react";

import {
  rankIdeasWithPop,
  type IdeaFilters,
} from "@/components/domains/options-lab-ideas";
import type { StrategyTemplateId } from "@/components/domains/options-lab-strategy";
import type { OptionsScreenerRow, OptionsScreenerSnapshot } from "@/lib/api/admin";
import { cn } from "@/lib/utils";

/**
 * Trade Ideas overlay — model PoP / E[PnL] ranks (not SmartPricing).
 * Open → Lab builder; Backtest → load template then open Backtest overlay.
 */
export function OptionsLabIdeasPanel({
  snapshot,
  onApplyIdea,
  onSendToBacktest,
}: {
  snapshot: OptionsScreenerSnapshot | null;
  onApplyIdea: (row: OptionsScreenerRow, templateId: StrategyTemplateId) => void;
  onSendToBacktest?: (row: OptionsScreenerRow, templateId: StrategyTemplateId) => void;
}) {
  const [universe, setUniverse] = useState<IdeaFilters["universe"]>("all");
  const [minPop, setMinPop] = useState(0);
  const [maxIvp, setMaxIvp] = useState<number | "">("");

  const ideas = useMemo(
    () =>
      rankIdeasWithPop(snapshot?.rows ?? [], {
        universe,
        minPop: minPop > 0 ? minPop : null,
        maxIvp: maxIvp === "" ? null : Number(maxIvp),
      }),
    [snapshot?.rows, universe, minPop, maxIvp],
  );

  return (
    <div className="flex flex-col gap-3 pt-3">
      <p className="text-sm text-slate-muted">
        Model PoP / E[PnL] on suggested templates (IV-implied at expiry). Not live fill EV.
        Missing IVP/PCR/spot skips the card. Use{" "}
        <span className="text-ink">Open</span> or{" "}
        <span className="text-ink">Backtest</span> explicitly.
      </p>
      <div className="flex flex-wrap items-end gap-2 text-xs text-slate-muted">
        <label>
          Universe
          <select
            value={universe}
            onChange={(e) => setUniverse(e.target.value as IdeaFilters["universe"])}
            className="ml-1 rounded border border-line bg-canvas px-2 py-1 text-ink"
          >
            <option value="all">all</option>
            <option value="indices">indices</option>
            <option value="equities">equities</option>
          </select>
        </label>
        <label>
          Min PoP %
          <input
            type="number"
            min={0}
            max={100}
            value={minPop}
            onChange={(e) => setMinPop(Number(e.target.value) || 0)}
            className="ml-1 w-16 rounded border border-line bg-canvas px-2 py-1 text-ink"
          />
        </label>
        <label>
          Max IVP
          <input
            type="number"
            min={0}
            max={100}
            placeholder="—"
            value={maxIvp}
            onChange={(e) =>
              setMaxIvp(e.target.value === "" ? "" : Number(e.target.value))
            }
            className="ml-1 w-16 rounded border border-line bg-canvas px-2 py-1 text-ink"
          />
        </label>
      </div>
      {ideas.length === 0 ? (
        <p className="py-10 text-center text-sm text-slate-muted">
          No ideas yet — refresh Screener / Heat map first (need spot, ATM IV, IVP, PCR, FUT).
        </p>
      ) : (
        <ul className="divide-y divide-line rounded-lg border border-line">
          {ideas.map((idea) => (
            <li
              key={`${idea.row.underlying_symbol}-${idea.templateId}`}
              className="flex flex-wrap items-center justify-between gap-2 px-3 py-2.5"
            >
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-semibold text-ink">
                  {idea.row.underlying_label}{" "}
                  <span className="font-normal text-slate-muted">
                    · {idea.templateLabel}
                  </span>
                </p>
                <p className="text-xs text-slate-muted">
                  {idea.reason}
                  {idea.dte != null ? ` · ${idea.dte}d` : ""}
                  {idea.rewardRisk != null ? ` · RR ${idea.rewardRisk}` : ""}
                </p>
                <div className="mt-0.5 flex gap-3 text-xs tabular-nums">
                  <span className="font-medium text-teal">
                    PoP {idea.pop != null ? `${idea.pop.toFixed(0)}%` : "—"}
                  </span>
                  <span className="text-slate-muted">
                    E[PnL]{" "}
                    {idea.expectedPnl != null ? idea.expectedPnl.toFixed(0) : "—"}
                  </span>
                </div>
              </div>
              <div className="flex shrink-0 flex-wrap items-center gap-1.5">
                <button
                  type="button"
                  className="rounded border border-line px-2 py-1 text-xs font-medium text-ink hover:bg-raised/50"
                  onClick={() => onApplyIdea(idea.row, idea.templateId)}
                >
                  Open
                </button>
                {onSendToBacktest ? (
                  <button
                    type="button"
                    className="rounded border border-teal/40 bg-teal/10 px-2 py-1 text-xs font-medium text-teal hover:bg-teal/20"
                    onClick={() => onSendToBacktest(idea.row, idea.templateId)}
                  >
                    Backtest
                  </button>
                ) : null}
              </div>
            </li>
          ))}
        </ul>
      )}
      {ideas.length > 0 ? (
        <p className={cn("text-[11px] text-slate-muted")}>
          Ranked by model PoP + E[PnL] on stub premiums — Open uses live chain quotes; Backtest
          runs the model path on the loaded legs.
        </p>
      ) : null}
    </div>
  );
}
