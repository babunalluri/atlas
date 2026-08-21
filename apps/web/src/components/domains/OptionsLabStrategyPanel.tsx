"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { OptionsLabPayoffChart } from "@/components/domains/OptionsLabPayoffChart";
import {
  STRATEGY_TEMPLATES,
  blendStrategyIv,
  buildPayoffCurve,
  buildStrategyFromTemplate,
  chainLegPremium,
  estimateProbabilityOfProfit,
  estimateStrategyGreeks,
  formatOptionContractName,
  istSessionHourKey,
  resolveDaysToExpiry,
  summarizeStrategy,
  syntheticForwardFromChain,
  type StrategyLeg,
  type StrategyTemplateId,
} from "@/components/domains/options-lab-strategy";
import { Label } from "@/components/ui/Field";
import { Button } from "@/components/ui/Button";
import type { OptionsChainSnapshot } from "@/lib/api/admin";
import { cn } from "@/lib/utils";

const BUILDER_STATE_KEY = "atlas-options-lab-builder";
const EMPTY_CHAIN_ROWS: NonNullable<OptionsChainSnapshot["rows"]> = [];

type BuilderState = {
  templateId: StrategyTemplateId;
  shiftSteps: number;
  widthSteps: number;
};

function loadBuilderState(): BuilderState {
  try {
    const raw = window.localStorage.getItem(BUILDER_STATE_KEY);
    if (!raw) throw new Error("empty");
    const parsed = JSON.parse(raw) as BuilderState;
    if (!STRATEGY_TEMPLATES.some((t) => t.id === parsed.templateId)) throw new Error("bad");
    return parsed;
  } catch {
    return { templateId: "long_straddle", shiftSteps: 0, widthSteps: 1 };
  }
}

function formatNum(value: number | null | undefined, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  if (Number.isInteger(value)) return value.toLocaleString();
  return value.toFixed(digits);
}

function formatExtreme(value: number | null | undefined) {
  if (value === null || value === undefined) return "Unlimited";
  return formatNum(value);
}

function formatPop(value: number | null | undefined) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${value.toFixed(1)}%`;
}

export function OptionsLabStrategyPanel({
  snapshot,
  strikeStep,
  onQueuePortfolioSave,
}: {
  snapshot: OptionsChainSnapshot | null;
  strikeStep: number;
  onQueuePortfolioSave?: (payload: {
    name: string;
    legs: Array<{
      id: string;
      side: "buy" | "sell";
      type: "CE" | "PE";
      strike: number;
      qty: number;
      entry_premium: number;
      symbol?: string;
    }>;
  }) => void;
}) {
  const [templateId, setTemplateId] = useState<StrategyTemplateId>("long_straddle");
  const [shiftSteps, setShiftSteps] = useState(0);
  const [widthSteps, setWidthSteps] = useState(1);
  const [legs, setLegs] = useState<StrategyLeg[]>([]);
  const touchedPremiums = useRef(new Set<string>());
  const lastLayoutKey = useRef("");
  const lastMarketKey = useRef("");

  const template = STRATEGY_TEMPLATES.find((t) => t.id === templateId) ?? STRATEGY_TEMPLATES[0];
  const layoutKey = useMemo(
    () => `${templateId}:${shiftSteps}:${widthSteps}:${strikeStep}`,
    [shiftSteps, strikeStep, templateId, widthSteps],
  );
  const atm = snapshot?.atm ?? null;
  const spot = snapshot?.spot ?? null;
  const rows = snapshot?.rows ?? EMPTY_CHAIN_ROWS;
  const rowsQuoteKey = useMemo(
    () =>
      rows
        .map(
          (row) =>
            `${row.strike}:${row.ce.ltp ?? ""}:${row.pe.ltp ?? ""}:${row.ce.delta ?? ""}:${row.pe.delta ?? ""}`,
        )
        .join("|"),
    [rows],
  );

  useEffect(() => {
    const saved = loadBuilderState();
    setTemplateId(saved.templateId);
    setShiftSteps(saved.shiftSteps);
    setWidthSteps(saved.widthSteps);
  }, []);

  useEffect(() => {
    try {
      window.localStorage.setItem(
        BUILDER_STATE_KEY,
        JSON.stringify({ templateId, shiftSteps, widthSteps }),
      );
    } catch {
      // private mode
    }
  }, [templateId, shiftSteps, widthSteps]);

  useEffect(() => {
    if (atm == null) {
      if (lastMarketKey.current !== "" || legs.length > 0) {
        setLegs([]);
        lastLayoutKey.current = "";
        lastMarketKey.current = "";
      }
      return;
    }
    const marketKey = `${layoutKey}:${atm}`;
    if (lastMarketKey.current === marketKey) return;

    const layoutChanged = lastLayoutKey.current !== layoutKey;
    lastLayoutKey.current = layoutKey;
    lastMarketKey.current = marketKey;
    if (layoutChanged) touchedPremiums.current.clear();

    setLegs((prev) => {
      const rebuilt = buildStrategyFromTemplate(templateId, {
        atm,
        strikeStep,
        rows,
        shiftSteps,
        widthSteps,
      });
      if (touchedPremiums.current.size === 0) return rebuilt;
      return rebuilt.map((leg) => {
        const prior = prev.find((item) => item.id === leg.id);
        if (prior && touchedPremiums.current.has(leg.id)) {
          return { ...leg, premium: prior.premium };
        }
        return leg;
      });
    });
  }, [atm, layoutKey, shiftSteps, strikeStep, templateId, widthSteps]);

  useEffect(() => {
    if (atm == null || legs.length === 0 || rows.length === 0) return;
    if (lastMarketKey.current !== `${layoutKey}:${atm}`) return;
    setLegs((prev) => {
      let changed = false;
      const next = prev.map((leg) => {
        if (touchedPremiums.current.has(leg.id)) return leg;
        const quote = chainLegPremium(rows, leg.strike, leg.type);
        const premium = quote.premium ?? 0;
        const delta = quote.delta ?? leg.delta;
        const quoteMissing = quote.premium == null;
        if (premium === leg.premium && delta === leg.delta && quoteMissing === leg.quoteMissing) {
          return leg;
        }
        changed = true;
        return { ...leg, premium, delta, quoteMissing };
      });
      return changed ? next : prev;
    });
  }, [atm, layoutKey, legs.length, rows.length, rowsQuoteKey]);

  const payoffPoints = useMemo(
    () =>
      buildPayoffCurve(legs, {
        spot: spot ?? atm ?? 24000,
        strikeStep,
      }),
    [atm, legs, spot, strikeStep],
  );

  const summary = useMemo(
    () => summarizeStrategy(legs, payoffPoints),
    [legs, payoffPoints],
  );

  const atmIv = snapshot?.summary?.atm_iv ?? snapshot?.charts?.iv?.atm_iv ?? null;
  const optionSymbolsKey = useMemo(
    () =>
      legs
        .map((leg) => {
          const row = rows.find((item) => item.strike === leg.strike);
          if (!row) return "";
          return leg.type === "CE" ? row.ce.symbol : row.pe.symbol;
        })
        .join("|"),
    [legs, rows],
  );
  const [pinnedNow, setPinnedNow] = useState(() => new Date());
  useEffect(() => {
    let lastKey = istSessionHourKey();
    const id = window.setInterval(() => {
      const next = istSessionHourKey();
      if (next === lastKey) return;
      lastKey = next;
      setPinnedNow(new Date());
    }, 60_000);
    return () => window.clearInterval(id);
  }, []);

  // Memoize DTE against symbol/FUT + IST hour — expiry-day fractions stay honest.
  const daysToExpiry = useMemo(() => {
    const symbols = optionSymbolsKey
      ? optionSymbolsKey.split("|").filter((symbol) => symbol.length > 0)
      : [];
    const resolved = resolveDaysToExpiry(
      {
        futSymbol: snapshot?.fut_symbol,
        optionSymbols: symbols,
      },
      pinnedNow,
    );
    if (resolved != null) return resolved;
    // Decoded-but-expired options → null (PoP —). Demo with no expiry meta → 7d.
    if (symbols.length > 0) return null;
    return 7;
  }, [pinnedNow, optionSymbolsKey, snapshot?.fut_symbol]);

  // Forward after DTE so the ATM basis guard can tighten near expiry (no cycle).
  const forward = useMemo(
    () => syntheticForwardFromChain(rows, atm, spot, daysToExpiry),
    [atm, daysToExpiry, rows, spot],
  );

  const blendedIv = useMemo(() => {
    if (legs.length === 0 || daysToExpiry == null) return null;
    return blendStrategyIv(legs, rows, atmIv, {
      forward,
      daysToExpiry,
    });
  }, [atmIv, daysToExpiry, forward, legs, rows]);

  const pop = useMemo(() => {
    if (forward == null || blendedIv == null || daysToExpiry == null || legs.length === 0) {
      return null;
    }
    return estimateProbabilityOfProfit(legs, {
      forward,
      ivPct: blendedIv.ivPct,
      daysToExpiry,
      strikeStep,
    });
  }, [blendedIv, daysToExpiry, forward, legs, strikeStep]);

  const greeks = useMemo(
    () =>
      estimateStrategyGreeks(legs, rows, {
        forward,
        daysToExpiry,
        atmIv,
      }),
    [atmIv, daysToExpiry, forward, legs, rows],
  );
  const greeksById = useMemo(() => {
    const map = new Map(greeks.legs.map((leg) => [leg.id, leg]));
    return map;
  }, [greeks.legs]);

  const missingQuotes = legs.some((leg) => leg.quoteMissing);
  // Greeks are premium-independent — keep nets even when some LTPs are missing.
  const summaryForDisplay = missingQuotes
    ? {
        ...summary,
        netPremium: null,
        netDelta: greeks.netDelta,
        netGamma: greeks.netGamma,
        netTheta: greeks.netTheta,
        netVega: greeks.netVega,
        thetaPerHour: greeks.thetaPerHour,
        breakevens: [],
        maxProfit: null,
        maxLoss: null,
        pop: null as number | null,
      }
    : {
        ...summary,
        netDelta: greeks.netDelta,
        netGamma: greeks.netGamma,
        netTheta: greeks.netTheta,
        netVega: greeks.netVega,
        thetaPerHour: greeks.thetaPerHour,
        pop,
      };

  const thetaLabel = summaryForDisplay.thetaPerHour ? "Θ Theta /h" : "Θ Theta /d";
  const formatGamma = (value: number | null | undefined) =>
    formatNum(value == null ? null : value * 100, 3);

  function updateLegPremium(id: string, premium: number) {
    if (Number.isNaN(premium)) return;
    touchedPremiums.current.add(id);
    setLegs((prev) =>
      prev.map((leg) =>
        leg.id === id
          ? { ...leg, premium: Math.max(0, premium), quoteMissing: false }
          : leg,
      ),
    );
  }

  if (!snapshot?.ok || atm == null) {
    return (
      <p className="mt-6 py-10 text-center text-sm text-slate-muted">
        Load a live or mock chain first — builder uses ATM strikes and premiums from the chain.
      </p>
    );
  }

  return (
    <div className="mt-3 flex min-h-0 flex-1 flex-col gap-4">
      <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]">
        <div className="space-y-3 rounded-lg border border-line bg-canvas/40 p-3">
          <div>
            <Label htmlFor="strategy-template">Template</Label>
            <select
              id="strategy-template"
              value={templateId}
              onChange={(e) => setTemplateId(e.target.value as StrategyTemplateId)}
              className="mt-1 w-full rounded-md border border-line bg-raised px-3 py-2 text-sm text-ink"
            >
              {STRATEGY_TEMPLATES.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.label} · {item.hint}
                </option>
              ))}
            </select>
          </div>

          <div>
            <Label htmlFor="strategy-shift">Strike shift (steps)</Label>
            <input
              id="strategy-shift"
              type="range"
              min={-5}
              max={5}
              step={1}
              value={shiftSteps}
              onChange={(e) => setShiftSteps(Number(e.target.value))}
              className="mt-2 w-full"
            />
            <p className="mt-1 text-xs text-slate-muted tabular-nums">
              {shiftSteps >= 0 ? "+" : ""}
              {shiftSteps} × {strikeStep} → center {atm + shiftSteps * strikeStep}
            </p>
          </div>

          {template.usesWidth ? (
            <div>
              <Label htmlFor="strategy-width">Spread width (steps)</Label>
              <input
                id="strategy-width"
                type="range"
                min={1}
                max={6}
                step={1}
                value={widthSteps}
                onChange={(e) => setWidthSteps(Number(e.target.value))}
                className="mt-2 w-full"
              />
              <p className="mt-1 text-xs text-slate-muted tabular-nums">
                {widthSteps} × {strikeStep} = {widthSteps * strikeStep} pts
              </p>
            </div>
          ) : null}

          <div className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
            {[
              ["Net premium", formatNum(summaryForDisplay.netPremium)],
              ["Max profit†", formatExtreme(summaryForDisplay.maxProfit)],
              ["Max loss†", formatExtreme(summaryForDisplay.maxLoss)],
              ["PoP‡", formatPop(summaryForDisplay.pop)],
              ["Δ Delta", formatNum(summaryForDisplay.netDelta, 3)],
              ["Γ Gamma /100pts", formatGamma(summaryForDisplay.netGamma)],
              [thetaLabel, formatNum(summaryForDisplay.netTheta, 2)],
              ["ν Vega /1%", formatNum(summaryForDisplay.netVega, 2)],
            ].map(([label, value]) => (
              <div key={label} className="rounded-md border border-line/70 bg-raised/40 px-2 py-1.5">
                <div className="text-[10px] uppercase tracking-wide text-slate-muted">{label}</div>
                <div className="font-semibold tabular-nums text-ink">{value}</div>
              </div>
            ))}
          </div>

          {summaryForDisplay.breakevens.length > 0 ? (
            <p className="text-xs text-slate-muted">
              Breakevens:{" "}
              <span className="font-semibold tabular-nums text-ink">
                {summaryForDisplay.breakevens.join(", ")}
              </span>
            </p>
          ) : (
            <p className="text-xs text-slate-muted">No breakeven in scanned range.</p>
          )}
          <p className="text-[10px] text-slate-muted">
            † Max profit/loss at expiry; unlimited risk or reward shown as Unlimited.
          </p>
          <p className="text-[10px] text-slate-muted">
            Greeks are Black-76 model estimates (r=0) from leg IV — not exchange prints. Γ is per
            100 pts of forward; Θ is per{" "}
            {summaryForDisplay.thetaPerHour ? "hour when under 1 day to expiry" : "calendar day"}; ν
            is per 1 vol point.
          </p>
          <p className="text-[10px] text-slate-muted">
            ‡ PoP is an IV-implied estimate at expiry
            {blendedIv
              ? ` (σ* ${blendedIv.ivPct.toFixed(1)}% from ${blendedIv.chainLegs}/${blendedIv.legs} leg IV`
              : ""}
            {blendedIv && blendedIv.parityLegs > 0
              ? `, ${blendedIv.parityLegs} parity`
              : ""}
            {blendedIv && blendedIv.interpLegs > 0
              ? `, ${blendedIv.interpLegs} interp`
              : ""}
            {blendedIv && blendedIv.ltpLegs > 0
              ? `, ${blendedIv.ltpLegs} from LTP`
              : ""}
            {blendedIv && blendedIv.atmFallbackLegs > 0
              ? `, ${blendedIv.atmFallbackLegs} ATM fallback`
              : ""}
            {blendedIv ? ")" : ""}
            {`, ~${daysToExpiry != null ? Math.round(daysToExpiry) : "—"}d`} — not a
            guarantee.
          </p>

          {missingQuotes ? (
            <p className="rounded-md border border-amber-500/40 bg-amber-500/10 px-2 py-1.5 text-xs text-amber-800 dark:text-amber-200">
              Some legs have no chain quote at that strike — premiums are 0 until you edit them or
              load matching strikes. Max profit/loss and PoP stay hidden until all legs are quoted.
            </p>
          ) : null}

          {onQueuePortfolioSave && legs.length > 0 ? (
            <Button
              type="button"
              size="sm"
              variant="secondary"
              className="w-full"
              onClick={() =>
                onQueuePortfolioSave({
                  name: `${template.label} @ ${atm ?? "ATM"}`,
                  legs: legs.map((leg) => {
                    const row = rows.find((item) => item.strike === leg.strike);
                    const chainLeg = leg.type === "CE" ? row?.ce : row?.pe;
                    return {
                      id: leg.id,
                      side: leg.side,
                      type: leg.type,
                      strike: leg.strike,
                      qty: leg.qty,
                      entry_premium: leg.premium,
                      symbol: chainLeg?.symbol || undefined,
                    };
                  }),
                })
              }
            >
              Save to portfolio
            </Button>
          ) : null}
        </div>

        <OptionsLabPayoffChart
          points={payoffPoints}
          spot={spot}
          breakevens={summaryForDisplay.breakevens}
        />
      </div>

      <div className="overflow-auto rounded-lg border border-line">
        <table className="min-w-full border-collapse text-left text-xs">
          <thead className="bg-raised/90 text-[10px] uppercase tracking-wide text-slate-muted">
            <tr>
              <th className="px-3 py-2">Side</th>
              <th className="px-3 py-2">Contract</th>
              <th className="px-3 py-2">Strike</th>
              <th className="px-3 py-2">Premium</th>
              <th className="px-3 py-2">Δ Delta</th>
              <th className="px-3 py-2">Γ /100pts</th>
              <th className="px-3 py-2">{summaryForDisplay.thetaPerHour ? "Θ /h" : "Θ /d"}</th>
              <th className="px-3 py-2">ν Vega</th>
            </tr>
          </thead>
          <tbody>
            {legs.map((leg) => {
              const row = rows.find((item) => item.strike === leg.strike);
              const symbol = leg.type === "CE" ? row?.ce.symbol : row?.pe.symbol;
              const name = formatOptionContractName(symbol);
              const legGreeks = greeksById.get(leg.id);
              return (
                <tr
                  key={leg.id}
                  className={cn(
                    "border-t border-line/70",
                    leg.quoteMissing && "bg-amber-500/5",
                  )}
                >
                  <td className="px-3 py-2 capitalize">{leg.side}</td>
                  <td className="px-3 py-2">
                    <div
                      className={cn(
                        "font-medium",
                        leg.type === "CE" ? "text-teal" : "text-rose",
                      )}
                    >
                      {name ?? `${leg.type} ${leg.strike}`}
                    </div>
                    {symbol ? (
                      <div className="mt-0.5 font-mono text-[10px] text-slate-muted">{symbol}</div>
                    ) : null}
                  </td>
                  <td className="px-3 py-2 tabular-nums">{leg.strike}</td>
                  <td className="px-3 py-2">
                    <input
                      type="number"
                      min={0}
                      step={0.05}
                      value={leg.premium}
                      onChange={(e) => updateLegPremium(leg.id, Number(e.target.value))}
                      className={cn(
                        "w-24 rounded border bg-canvas px-2 py-1 tabular-nums",
                        leg.quoteMissing ? "border-amber-500/60" : "border-line",
                      )}
                      title={leg.quoteMissing ? "No chain quote at this strike" : undefined}
                    />
                  </td>
                  <td className="px-3 py-2 tabular-nums">
                    {formatNum(legGreeks?.delta, 3)}
                  </td>
                  <td className="px-3 py-2 tabular-nums">{formatGamma(legGreeks?.gamma)}</td>
                  <td className="px-3 py-2 tabular-nums">{formatNum(legGreeks?.theta, 2)}</td>
                  <td className="px-3 py-2 tabular-nums">{formatNum(legGreeks?.vega, 2)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
