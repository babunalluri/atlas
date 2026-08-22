"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { OptionsLabPayoffChart } from "@/components/domains/OptionsLabPayoffChart";
import {
  STRATEGY_TEMPLATES,
  blendStrategyIv,
  bookedStrategyPnlRupees,
  buildLegIvMap,
  buildPayoffCurve,
  buildPnlTable,
  buildStrategyFromTemplate,
  chainLegPremium,
  cycleChainLeg,
  estimateFundsAndMargins,
  estimateLotSize,
  estimatePayoffDistributionStats,
  estimateProbabilityOfProfit,
  estimateStrategyCharges,
  estimateStrategyGreeks,
  estimateTargetDateProbabilityOfProfit,
  formatOptionContractName,
  isExpiryHorizon,
  istSessionHourKey,
  MIN_DTE_DAYS,
  resolveDaysToExpiry,
  summarizeStrategy,
  syntheticForwardFromChain,
  volatilitySpotBands,
  type StrategyLeg,
  type StrategyTemplateId,
} from "@/components/domains/options-lab-strategy";
import { Label, Select } from "@/components/ui/Field";
import { Button } from "@/components/ui/Button";
import {
  CheckIcon,
  CloseIcon,
  SaveIcon,
  TableIcon,
} from "@/components/ui/icons";
import {
  postOptionsLabMargins,
  postOptionsLabOrders,
  type OptionsChainSnapshot,
} from "@/lib/api/admin";
import { useAgentOsToken } from "@/lib/auth/token";
import { cn } from "@/lib/utils";

const BUILDER_STATE_KEY = "atlas-options-lab-builder";
const EMPTY_CHAIN_ROWS: NonNullable<OptionsChainSnapshot["rows"]> = [];
/** Mock desk funds until Live margins are wired into Options Lab. */
const DEMO_MARGIN_AVAILABLE = 100_000;

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
    const tpl = STRATEGY_TEMPLATES.find((t) => t.id === parsed.templateId);
    if (!tpl || tpl.gated) throw new Error("bad");
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

type MetricTone = "pos" | "neg" | "neutral";

function toneFromSigned(value: number | null | undefined): MetricTone {
  if (value === null || value === undefined || Number.isNaN(value) || value === 0) {
    return "neutral";
  }
  return value > 0 ? "pos" : "neg";
}

/** Max profit Unlimited = favorable; max loss Unlimited = adverse. */
function toneMaxProfit(value: number | null | undefined): MetricTone {
  if (value === null || value === undefined) return "pos";
  return toneFromSigned(value);
}

function toneMaxLoss(value: number | null | undefined): MetricTone {
  if (value === null || value === undefined) return "neg";
  return toneFromSigned(value);
}

/** Probability metrics (0–1): ≥50% favorable, below adverse. */
function toneFromPop(value: number | null | undefined): MetricTone {
  if (value === null || value === undefined || Number.isNaN(value)) return "neutral";
  return value >= 0.5 ? "pos" : "neg";
}

function metricToneClass(tone: MetricTone) {
  if (tone === "pos") return "text-teal";
  if (tone === "neg") return "text-rose";
  return "text-ink";
}

type MetricItem = { label: string; value: string; tone?: MetricTone };

/** Dense label + range + value (rail) or stacked Label + full-width range (page). */
function InlineSlider({
  id,
  label,
  value,
  display,
  min,
  max,
  step = 1,
  disabled,
  onChange,
  valueClassName,
  stacked = false,
}: {
  id: string;
  label: string;
  value: number;
  display: string;
  min: number;
  max: number;
  step?: number;
  disabled?: boolean;
  onChange: (next: number) => void;
  /** Wider readout for labels like “T+0d · 5.6d left”. */
  valueClassName?: string;
  /** Page layout: label above, full-width track, caption below via display. */
  stacked?: boolean;
}) {
  const pct = max === min ? 0 : ((value - min) / (max - min)) * 100;
  const range = (
    <input
      id={id}
      type="range"
      min={min}
      max={max}
      step={step}
      value={value}
      disabled={disabled}
      onChange={(e) => onChange(Number(e.target.value))}
      style={{ ["--slider-pct" as string]: `${pct}%` }}
      className={cn("slider-sleek w-full", stacked ? "mt-2" : "min-w-0 flex-1")}
    />
  );
  if (stacked) {
    return (
      <div>
        <Label htmlFor={id}>{label}</Label>
        {range}
        <p className="mt-1 text-xs tabular-nums text-slate-muted">{display}</p>
      </div>
    );
  }
  return (
    <div className="flex min-w-[7.5rem] flex-1 items-center gap-2">
      <label htmlFor={id} className="th-label shrink-0">
        {label}
      </label>
      {range}
      <output
        htmlFor={id}
        className={cn(
          "shrink-0 text-right text-sm tabular-nums text-ink",
          valueClassName ?? "min-w-[2.75rem]",
        )}
      >
        {display}
      </output>
    </div>
  );
}

function MetricCell({
  label,
  value,
  tone = "neutral",
}: {
  label: string;
  value: string;
  tone?: MetricTone;
}) {
  return (
    <div className="rounded-md border border-line/70 bg-raised/40 px-2.5 py-2">
      <p className="th-label">{label}</p>
      <p
        className={cn(
          "mt-1 font-display text-sm font-semibold tabular-nums",
          metricToneClass(tone),
        )}
      >
        {value}
      </p>
    </div>
  );
}

/** Vertical metrics rail beside the payoff graph. */
function MetricColumn({ items }: { items: MetricItem[] }) {
  return (
    <div className="flex flex-col gap-1.5">
      {items.map(({ label, value, tone = "neutral" }) => (
        <div
          key={label}
          className="rounded-md border border-line/70 bg-raised/40 px-1.5 py-1.5"
        >
          <p className="th-label truncate" title={label}>
            {label}
          </p>
          <p
            className={cn(
              "mt-0.5 truncate text-sm font-semibold tabular-nums",
              metricToneClass(tone),
            )}
            title={value}
          >
            {value}
          </p>
        </div>
      ))}
    </div>
  );
}

export function OptionsLabStrategyPanel({
  snapshot,
  strikeStep,
  onQueuePortfolioSave,
  variant = "page",
  chainToggle = null,
  applyTemplate = null,
  onLegsChange,
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
  /** Dense rail for single-desk layout. */
  variant?: "page" | "rail";
  /** Parent increments seq when chain CE/PE is clicked. */
  chainToggle?: { seq: number; strike: number; type: "CE" | "PE" } | null;
  /** Parent increments seq to force a template from Ideas overlay. */
  applyTemplate?: { id: StrategyTemplateId; seq: number } | null;
  onLegsChange?: (legs: StrategyLeg[]) => void;
}) {
  const { getAccessToken } = useAgentOsToken();
  const [templateId, setTemplateId] = useState<StrategyTemplateId>("long_straddle");
  const [shiftSteps, setShiftSteps] = useState(0);
  const [widthSteps, setWidthSteps] = useState(1);
  /** Days from now toward expiry (0 = now/mark, max ≈ DTE = expiry). */
  const [targetDayOffset, setTargetDayOffset] = useState(0);
  /** IV shock in vol points for scenario curve. */
  const [ivShockPts, setIvShockPts] = useState(0);
  const [marginAvailable, setMarginAvailable] = useState(DEMO_MARGIN_AVAILABLE);
  const [brokerFunds, setBrokerFunds] = useState<number | null>(null);
  const [brokerMargin, setBrokerMargin] = useState<number | null>(null);
  const [marginSource, setMarginSource] = useState<string>("heuristic");
  const [marginWarning, setMarginWarning] = useState<string | null>(null);
  const [orderBusy, setOrderBusy] = useState(false);
  const [orderMessage, setOrderMessage] = useState<string | null>(null);
  const [product, setProduct] = useState<"NRML" | "MIS">("NRML");
  const [legs, setLegs] = useState<StrategyLeg[]>([]);
  const [pnlOpen, setPnlOpen] = useState(false);
  const [editingLegs, setEditingLegs] = useState(false);
  const [stopLossPct, setStopLossPct] = useState("");
  const [targetPct, setTargetPct] = useState("");
  const [orderMode, setOrderMode] = useState<"legs" | "basket">("legs");
  const touchedPremiums = useRef(new Set<string>());
  const lastLayoutKey = useRef("");
  const lastMarketKey = useRef("");
  const manualEdit = useRef(false);
  const lastChainToggleSeq = useRef(0);
  const lastApplyTemplateSeq = useRef(0);
  const rail = variant === "rail";

  // Restore builder prefs first; Ideas applyTemplate must run after so it can win.
  useEffect(() => {
    const saved = loadBuilderState();
    setTemplateId(saved.templateId);
    setShiftSteps(saved.shiftSteps);
    setWidthSteps(saved.widthSteps);
  }, []);

  useEffect(() => {
    if (!applyTemplate || applyTemplate.seq === lastApplyTemplateSeq.current) return;
    lastApplyTemplateSeq.current = applyTemplate.seq;
    const tpl = STRATEGY_TEMPLATES.find((t) => t.id === applyTemplate.id);
    if (!tpl || tpl.gated) return;
    manualEdit.current = false;
    touchedPremiums.current.clear();
    // Force rebuild even when templateId is unchanged (re-click same Idea).
    lastLayoutKey.current = "";
    lastMarketKey.current = "";
    setTemplateId(applyTemplate.id);
    if (atm == null) return;
    setLegs(
      buildStrategyFromTemplate(applyTemplate.id, {
        atm,
        strikeStep,
        rows,
        shiftSteps,
        widthSteps,
      }),
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps -- applyTemplate.seq gates; market/layout read at click
  }, [applyTemplate]);

  useEffect(() => {
    if (!pnlOpen) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setPnlOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [pnlOpen]);

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
            `${row.strike}:${row.ce.ltp ?? ""}:${row.pe.ltp ?? ""}:${row.ce.iv ?? ""}:${row.pe.iv ?? ""}:${row.ce.delta ?? ""}:${row.pe.delta ?? ""}`,
        )
        .join("|"),
    [rows],
  );

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
        manualEdit.current = false;
      }
      return;
    }
    const marketKey = `${layoutKey}:${atm}`;
    if (lastMarketKey.current === marketKey) return;

    const layoutChanged = lastLayoutKey.current !== layoutKey;
    lastLayoutKey.current = layoutKey;
    lastMarketKey.current = marketKey;

    // Template/shift/width change always rebuilds; ATM-only refresh keeps manual legs.
    if (!layoutChanged && manualEdit.current) return;

    if (layoutChanged) {
      touchedPremiums.current.clear();
      manualEdit.current = false;
    }

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
    // Rebuild on ATM/layout only — `rows` is read inside for premiums; quote ticks use rowsQuoteKey below.
    // eslint-disable-next-line react-hooks/exhaustive-deps -- intentional; avoid SSE row identity churn
  }, [atm, layoutKey, shiftSteps, strikeStep, templateId, widthSteps]);

  useEffect(() => {
    if (!chainToggle || chainToggle.seq === lastChainToggleSeq.current) return;
    lastChainToggleSeq.current = chainToggle.seq;
    manualEdit.current = true;
    setLegs((prev) => cycleChainLeg(prev, rows, chainToggle.strike, chainToggle.type));
    // eslint-disable-next-line react-hooks/exhaustive-deps -- chainToggle.seq gates; rows read for quotes at click
  }, [chainToggle]);

  useEffect(() => {
    onLegsChange?.(legs);
  }, [legs, onLegsChange]);

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
    // rowsQuoteKey tracks quote content without depending on `rows` array identity.
    // eslint-disable-next-line react-hooks/exhaustive-deps -- rows via rowsQuoteKey
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

  const dteSliderMax = daysToExpiry != null ? Math.max(0, Math.ceil(daysToExpiry)) : 0;
  useEffect(() => {
    setTargetDayOffset((prev) => Math.min(prev, dteSliderMax));
  }, [dteSliderMax]);

  const remainingAtTarget =
    daysToExpiry == null ? 0 : Math.max(0, daysToExpiry - targetDayOffset);
  // Use remaining life only — do not treat floor(DTE) as expiry while a fraction remains.
  const atExpiryHorizon = isExpiryHorizon(remainingAtTarget);

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
  // Prefer rowsQuoteKey over `rows` (same as greeks).
  // eslint-disable-next-line react-hooks/exhaustive-deps -- rows via rowsQuoteKey
  }, [atmIv, daysToExpiry, forward, legs, rowsQuoteKey]);

  const effectiveIv = blendedIv?.ivPct ?? null;
  const legIvById = useMemo(() => {
    if (legs.length === 0 || daysToExpiry == null) return null;
    return buildLegIvMap(legs, rows, {
      atmIv,
      forward,
      daysToExpiry,
      ivShockPts: 0,
    });
  // eslint-disable-next-line react-hooks/exhaustive-deps -- rows via rowsQuoteKey
  }, [atmIv, daysToExpiry, forward, legs, rowsQuoteKey]);

  const scenarioLegIvById = useMemo(() => {
    if (legs.length === 0 || daysToExpiry == null || ivShockPts === 0) return null;
    return buildLegIvMap(legs, rows, {
      atmIv,
      forward,
      daysToExpiry,
      ivShockPts,
    });
  // eslint-disable-next-line react-hooks/exhaustive-deps -- rows via rowsQuoteKey
  }, [atmIv, daysToExpiry, forward, ivShockPts, legs, rowsQuoteKey]);

  const scenarioIv =
    effectiveIv != null ? Math.max(0.5, effectiveIv + ivShockPts) : null;

  const targetPayoffPoints = useMemo(() => {
    if (legs.length === 0 || atExpiryHorizon || legIvById == null) return [];
    return buildPayoffCurve(legs, {
      strikeStep,
      remainingDaysToExpiry: remainingAtTarget,
      ivPct: effectiveIv,
      legIvById,
    });
  }, [atExpiryHorizon, effectiveIv, legIvById, legs, remainingAtTarget, strikeStep]);

  const scenarioPayoffPoints = useMemo(() => {
    // Expiry intrinsic is IV-insensitive — don't overlay a duplicate dashed line.
    if (
      legs.length === 0 ||
      scenarioLegIvById == null ||
      ivShockPts === 0 ||
      atExpiryHorizon
    ) {
      return [];
    }
    return buildPayoffCurve(legs, {
      strikeStep,
      remainingDaysToExpiry: remainingAtTarget,
      ivPct: scenarioIv,
      legIvById: scenarioLegIvById,
    });
  }, [
    atExpiryHorizon,
    ivShockPts,
    legs,
    remainingAtTarget,
    scenarioIv,
    scenarioLegIvById,
    strikeStep,
  ]);

  const sdBands = useMemo(() => {
    if (forward == null || effectiveIv == null || daysToExpiry == null) return null;
    // Expiry view: move to expiry. Target view: move to the target date.
    const horizon = atExpiryHorizon
      ? daysToExpiry
      : Math.max(targetDayOffset, MIN_DTE_DAYS);
    return volatilitySpotBands(forward, effectiveIv, horizon);
  }, [
    atExpiryHorizon,
    daysToExpiry,
    effectiveIv,
    forward,
    targetDayOffset,
  ]);

  const oiBars = useMemo(() => {
    const chartOi = snapshot?.charts?.oi;
    if (chartOi && chartOi.length > 0) {
      return chartOi.map((row) => ({
        strike: row.strike,
        ceOi: Math.abs(row.ce_oi ?? 0),
        peOi: Math.abs(row.pe_oi ?? 0),
      }));
    }
    return rows.map((row) => ({
      strike: row.strike,
      ceOi: Math.abs(row.ce.oi ?? 0),
      peOi: Math.abs(row.pe.oi ?? 0),
    }));
  }, [rows, snapshot?.charts?.oi]);

  const pnlTable = useMemo(() => {
    if (legs.length === 0 || legIvById == null || daysToExpiry == null) {
      return null;
    }
    const maxR = Math.max(0, daysToExpiry);
    // 2dp + dedupe: on expiry day 1dp rounds collapse (0.1/0.1/0/0 → duplicate React keys).
    const remainingDtes: number[] = [];
    for (const v of [maxR, maxR * 0.66, maxR * 0.33, 0]) {
      const rounded = Math.round(Math.max(0, v) * 100) / 100;
      if (!remainingDtes.includes(rounded)) remainingDtes.push(rounded);
    }
    return buildPnlTable(legs, {
      strikeStep,
      remainingDtes,
      ivPct: effectiveIv,
      legIvById,
      wings: 8,
      spotStepMultiplier: 2,
    });
  }, [daysToExpiry, effectiveIv, legIvById, legs, strikeStep]);

  const lotSize = estimateLotSize(
    snapshot?.underlying_label ?? snapshot?.underlying_symbol,
  );
  const fundsMargins = useMemo(() => {
    if (legs.length === 0 || spot == null) return null;
    return estimateFundsAndMargins(legs, { spot, lotSize });
  }, [legs, lotSize, spot]);
  const charges = useMemo(
    () => estimateStrategyCharges(legs, lotSize),
    [legs, lotSize],
  );
  const booked = useMemo(
    () => bookedStrategyPnlRupees(legs, rows, lotSize),
    [legs, lotSize, rows],
  );
  const missingQuotes = legs.some((leg) => leg.quoteMissing);

  const displayMarginNeeded = brokerMargin ?? fundsMargins?.marginNeeded ?? null;
  const displayFundsNeeded = brokerFunds ?? fundsMargins?.fundsNeeded ?? displayMarginNeeded;

  /**
   * Stable key for broker margin. Quote ticks update leg.premium continuously; round to
   * nearest rupee so the 400ms debounce is not reset every SSE frame (broker quote TTL ~500ms).
   * Live premium is still read from refs when the timer fires.
   */
  const marginLegsSignature = useMemo(
    () =>
      legs
        .map((leg) => {
          const row = rows.find((item) => item.strike === leg.strike);
          const symbol = (leg.type === "CE" ? row?.ce : row?.pe)?.symbol ?? "";
          return [
            leg.id,
            leg.side,
            leg.type,
            leg.strike,
            leg.qty,
            Math.round(leg.premium),
            symbol,
          ].join(":");
        })
        .join("|"),
    [legs, rows],
  );
  const legsRef = useRef(legs);
  const rowsRef = useRef(rows);
  const spotRef = useRef(spot);
  useEffect(() => {
    legsRef.current = legs;
    rowsRef.current = rows;
    spotRef.current = spot;
  }, [legs, rows, spot]);

  useEffect(() => {
    if (legs.length === 0 || missingQuotes) {
      setBrokerMargin(null);
      setBrokerFunds(null);
      setMarginSource("heuristic");
      setMarginWarning(null);
      return;
    }
    let cancelled = false;
    const timer = window.setTimeout(async () => {
      const currentLegs = legsRef.current;
      const currentRows = rowsRef.current;
      const currentSpot = spotRef.current;
      try {
        const token = await getAccessToken();
        if (!token || cancelled) return;
        const legPayload = currentLegs.map((leg) => {
          const row = currentRows.find((item) => item.strike === leg.strike);
          const chainLeg = leg.type === "CE" ? row?.ce : row?.pe;
          return {
            id: leg.id,
            side: leg.side,
            type: leg.type,
            strike: leg.strike,
            qty: leg.qty,
            entry_premium: leg.premium,
            premium: leg.premium,
            symbol: chainLeg?.symbol || undefined,
          };
        });
        if (legPayload.some((leg) => !leg.symbol)) {
          setBrokerMargin(null);
          setBrokerFunds(null);
          setMarginSource("heuristic");
          setMarginWarning("Some legs lack symbols — showing heuristic margin.");
          return;
        }
        const heuristic =
          currentSpot != null
            ? estimateFundsAndMargins(currentLegs, {
                spot: currentSpot,
                lotSize,
              })
            : null;
        const res = await postOptionsLabMargins(token, {
          legs: legPayload,
          lot_size: lotSize,
          product,
          underlying_symbol: snapshot?.underlying_symbol,
          heuristic: heuristic
            ? {
                marginNeeded: heuristic.marginNeeded,
                fundsNeeded: heuristic.fundsNeeded,
              }
            : undefined,
          mock: snapshot?.mock,
          // Prefer SPAN basket whenever multi-leg; single leg still works.
          basket: orderMode === "basket" || currentLegs.length >= 2,
        });
        if (cancelled) return;
        if (!res.ok) {
          setBrokerMargin(null);
          setBrokerFunds(null);
          setMarginSource("heuristic");
          setMarginWarning(
            res.error || "Broker margin refresh failed — showing heuristic.",
          );
          return;
        }
        setMarginSource(res.source || "heuristic");
        if (res.margin_needed != null) setBrokerMargin(res.margin_needed);
        if (res.funds_needed != null) setBrokerFunds(res.funds_needed);
        if (res.margin_available != null) {
          setMarginAvailable(res.margin_available);
        }
        setMarginWarning(res.warnings?.length ? res.warnings.join(" ") : null);
      } catch {
        if (!cancelled) {
          setBrokerMargin(null);
          setBrokerFunds(null);
          setMarginSource("heuristic");
          setMarginWarning("Broker margin refresh failed — showing heuristic.");
        }
      }
    }, 600);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [
    marginLegsSignature,
    getAccessToken,
    legs.length,
    lotSize,
    missingQuotes,
    product,
    orderMode,
    snapshot?.mock,
    snapshot?.underlying_symbol,
  ]);

  function legOrderPayload() {
    return legs.map((leg) => {
      const row = rows.find((item) => item.strike === leg.strike);
      const chainLeg = leg.type === "CE" ? row?.ce : row?.pe;
      return {
        id: leg.id,
        side: leg.side,
        type: leg.type,
        strike: leg.strike,
        qty: leg.qty,
        entry_premium: leg.premium,
        premium: leg.premium,
        symbol: chainLeg?.symbol || undefined,
      };
    });
  }

  function handleBuyDraft() {
    if (!onQueuePortfolioSave || legs.length === 0) return;
    onQueuePortfolioSave({
      name: `${template.label} @ ${atm ?? "ATM"}`,
      legs: legOrderPayload(),
    });
  }

  async function handleBuyLive() {
    if (legs.length === 0 || missingQuotes) return;
    const payload = legOrderPayload();
    if (payload.some((leg) => !leg.symbol)) {
      setOrderMessage("Every leg needs a chain symbol before Buy.");
      return;
    }
    if (payload.some((leg) => !(leg.qty > 0))) {
      setOrderMessage("Every leg needs qty > 0.");
      return;
    }
    if (payload.some((leg) => !(Number(leg.premium) > 0))) {
      setOrderMessage("LIMIT Buy needs a premium > 0 on every leg.");
      return;
    }
    const isMock = Boolean(snapshot?.mock);
    const sl = stopLossPct.trim();
    const tgt = targetPct.trim();
    const slNum = sl ? Number(sl) : NaN;
    const tgtNum = tgt ? Number(tgt) : NaN;
    const wantGtt =
      (Number.isFinite(slNum) && slNum > 0) || (Number.isFinite(tgtNum) && tgtNum > 0);
    const extras: string[] = [];
    if (orderMode === "basket") {
      extras.push(
        "Basket: concurrent buy wave then sell wave (Kite has no atomic multi-leg place). " +
          "Margin uses SPAN basket API when bound.",
      );
    }
    if (wantGtt) {
      extras.push(
        `SL/Tgt (${Number.isFinite(slNum) && slNum > 0 ? slNum : "—"}% / ${
          Number.isFinite(tgtNum) && tgtNum > 0 ? tgtNum : "—"
        }%): auto per-leg GTT only runs for MARKET entries (NRML). ` +
          `This Buy is LIMIT — GTT will be skipped; place exits manually after fill or switch to MARKET.`,
      );
    }
    const confirmed = window.confirm(
      isMock
        ? `Simulate ${legs.length} leg(s) as ${product} mock orders and save a draft?` +
            (extras.length ? `\n\n${extras.join("\n")}` : "")
        : `Place ${legs.length} leg(s) as ${product} LIMIT orders?\n\n` +
            `Buys are sent before sells. Paper tools are preferred when bound.\n` +
            `Quantity = lots × ${lotSize}.\n\n` +
            (extras.length ? `${extras.join("\n")}\n\n` : "") +
            `If any leg fails, do NOT re-Buy the full set — check positions first.`,
    );
    if (!confirmed) return;
    setOrderBusy(true);
    setOrderMessage(null);
    try {
      const token = await getAccessToken();
      if (!token) {
        setOrderMessage("Not signed in.");
        return;
      }
      const submit = (live: boolean) =>
        postOptionsLabOrders(token, {
          legs: payload,
          confirm: true,
          live,
          lot_size: lotSize,
          product,
          order_type: "LIMIT",
          name: `${template.label} @ ${atm ?? "ATM"}`,
          underlying_symbol: snapshot?.underlying_symbol,
          save_draft: true,
          mock: isMock,
          basket: orderMode === "basket",
          ...(Number.isFinite(slNum) && slNum > 0
            ? { stop_loss_pct: Math.min(90, Math.max(0.5, slNum)) }
            : {}),
          ...(Number.isFinite(tgtNum) && tgtNum > 0
            ? { target_pct: Math.min(200, Math.max(0.5, tgtNum)) }
            : {}),
        });

      // Default live=false so paper_order can win; escalate only if backend requires it.
      let res = await submit(false);
      if (
        !res.ok &&
        !res.partial &&
        typeof res.error === "string" &&
        res.error.includes("live=true")
      ) {
        const liveOk = window.confirm(
          "No paper place tool available. Send LIVE place_order to the broker?" +
            (wantGtt
              ? "\n\nSL/Tgt GTT exits will be created after live order accept (NRML only)."
              : ""),
        );
        if (!liveOk) {
          setOrderMessage("Cancelled — live place_order not confirmed.");
          return;
        }
        res = await submit(true);
      }

      const lines = (res.orders || []).map((o) => {
        const sym = o.symbol || "?";
        if (o.status === "submitted") return `${sym}: ok ${o.order_id ?? ""}`.trim();
        return `${sym}: ${o.status || "failed"}${o.error ? ` (${o.error})` : ""}`;
      });
      const gttLines = (res.gtts || []).map((g) => {
        const sym = g.symbol || "?";
        if (g.status === "submitted" || g.status === "simulated") {
          const levels = (g.trigger_values || []).join("/");
          return `GTT ${sym}: ${g.status} ${g.trigger_id ?? ""} ${levels ? `@ ${levels}` : ""}`.trim();
        }
        return `GTT ${sym}: ${g.status || "failed"}${g.error ? ` (${g.error})` : ""}`;
      });

      if (res.partial) {
        setOrderMessage(
          [
            `PARTIAL — ${res.submitted_count ?? "?"} submitted, ${res.failed_count ?? "?"} failed.`,
            "Do not re-Buy the full set; check broker positions first.",
            ...lines,
            ...gttLines,
            ...(res.warnings || []),
          ].join("\n"),
        );
        return;
      }
      if (!res.ok) {
        setOrderMessage(
          [res.error || res.errors?.join("; ") || "Order failed.", ...lines, ...gttLines]
            .filter(Boolean)
            .join("\n"),
        );
        return;
      }
      setOrderMessage(
        [
          res.mock
            ? "Mock orders simulated + draft saved."
            : `Submitted via ${res.tool || "broker"} (${res.team_slug || "?"})`,
          ...lines,
          ...gttLines,
          ...(res.warnings || []),
        ]
          .filter(Boolean)
          .join("\n"),
      );
    } catch (err) {
      setOrderMessage(err instanceof Error ? err.message : "Order failed.");
    } finally {
      setOrderBusy(false);
    }
  }

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

  const distStats = useMemo(() => {
    if (forward == null || blendedIv == null || daysToExpiry == null || legs.length === 0) {
      return null;
    }
    return estimatePayoffDistributionStats(legs, {
      forward,
      ivPct: blendedIv.ivPct,
      daysToExpiry,
      strikeStep,
      maxProfit: summary.maxProfit,
    });
  }, [blendedIv, daysToExpiry, forward, legs, strikeStep, summary.maxProfit]);

  const targetPop = useMemo(() => {
    if (
      forward == null ||
      blendedIv == null ||
      daysToExpiry == null ||
      legs.length === 0
    ) {
      return null;
    }
    // T+0 point-mass 0%/100% is misleading next to expiry PoP — hide until we move forward.
    if (targetDayOffset <= 0) return null;
    // Slider max is ceil(DTE); clamp so density never exceeds true life.
    const daysToTarget = Math.min(targetDayOffset, daysToExpiry);
    return estimateTargetDateProbabilityOfProfit(legs, {
      forward,
      ivPct: blendedIv.ivPct,
      daysToTarget,
      remainingDaysToExpiry: Math.max(0, daysToExpiry - daysToTarget),
      strikeStep,
      legIvById,
    });
  }, [
    blendedIv,
    daysToExpiry,
    forward,
    legIvById,
    legs,
    strikeStep,
    targetDayOffset,
  ]);

  const greeks = useMemo(
    () =>
      estimateStrategyGreeks(legs, rows, {
        forward,
        daysToExpiry,
        atmIv,
      }),
    // Prefer rowsQuoteKey over `rows` so identity churn without quote/IV changes
    // does not recompute greeks every SSE frame.
    // eslint-disable-next-line react-hooks/exhaustive-deps -- rows captured via rowsQuoteKey
    [atmIv, daysToExpiry, forward, legs, rowsQuoteKey],
  );
  const greeksById = useMemo(() => {
    const map = new Map(greeks.legs.map((leg) => [leg.id, leg]));
    return map;
  }, [greeks.legs]);

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
        expectedPnl: null as number | null,
        pMaxProfit: null as number | null,
        targetPop: null as number | null,
      }
    : {
        ...summary,
        netDelta: greeks.netDelta,
        netGamma: greeks.netGamma,
        netTheta: greeks.netTheta,
        netVega: greeks.netVega,
        thetaPerHour: greeks.thetaPerHour,
        pop,
        expectedPnl: distStats?.expectedPnl ?? null,
        pMaxProfit: distStats?.pMaxProfit ?? null,
        targetPop,
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

  function updateLegField(
    id: string,
    patch: Partial<Pick<StrategyLeg, "side" | "type" | "strike" | "qty" | "premium">>,
  ) {
    setLegs((prev) =>
      prev.map((leg) => {
        if (leg.id !== id) return leg;
        const next = { ...leg, ...patch };
        if (patch.strike != null || patch.type != null) {
          const { premium, delta } = chainLegPremium(rows, next.strike, next.type);
          next.premium = premium ?? 0;
          next.delta = delta;
          next.quoteMissing = premium == null;
          touchedPremiums.current.delete(id);
        }
        if (patch.premium != null) touchedPremiums.current.add(id);
        return next;
      }),
    );
  }

  function removeLeg(id: string) {
    setLegs((prev) => prev.filter((leg) => leg.id !== id));
  }

  function addBlankLeg() {
    if (legs.length >= 8) return;
    const strike = atm ?? 0;
    const { premium, delta } = chainLegPremium(rows, strike, "CE");
    setLegs((prev) => [
      ...prev,
      {
        id: `custom-${Date.now()}`,
        side: "buy",
        type: "CE",
        strike,
        qty: 1,
        premium: premium ?? 0,
        delta,
        quoteMissing: premium == null,
      },
    ]);
  }

  if (!snapshot?.ok || atm == null) {
    return (
      <p className={cn("text-center text-sm text-slate-muted", rail ? "py-6" : "mt-6 py-10")}>
        Load a live or mock chain first — builder uses ATM strikes and premiums from the chain.
      </p>
    );
  }

  const metricItems: MetricItem[] = [
    {
      label: "Net premium",
      value: formatNum(summaryForDisplay.netPremium),
      tone: toneFromSigned(summaryForDisplay.netPremium),
    },
    {
      label: "Max profit†",
      value: formatExtreme(summaryForDisplay.maxProfit),
      tone: toneMaxProfit(summaryForDisplay.maxProfit),
    },
    {
      label: "Max loss†",
      value: formatExtreme(summaryForDisplay.maxLoss),
      tone: toneMaxLoss(summaryForDisplay.maxLoss),
    },
    {
      label: "PoP‡",
      value: formatPop(summaryForDisplay.pop),
      tone: toneFromPop(summaryForDisplay.pop),
    },
    {
      label: "E[PnL]‡",
      value: formatNum(summaryForDisplay.expectedPnl),
      tone: toneFromSigned(summaryForDisplay.expectedPnl),
    },
    {
      label: "P(max)‡",
      value: formatPop(summaryForDisplay.pMaxProfit),
      tone: toneFromPop(summaryForDisplay.pMaxProfit),
    },
    {
      label: "PoP@target‡",
      value: formatPop(summaryForDisplay.targetPop),
      tone: toneFromPop(summaryForDisplay.targetPop),
    },
    {
      label: "Booked P&L ₹",
      value: formatNum(booked),
      tone: toneFromSigned(booked),
    },
    {
      label: "Δ Delta",
      value: formatNum(summaryForDisplay.netDelta, 3),
      tone: toneFromSigned(summaryForDisplay.netDelta),
    },
    {
      label: "Γ Gamma /100pts",
      value: formatGamma(summaryForDisplay.netGamma),
      tone: toneFromSigned(summaryForDisplay.netGamma),
    },
    {
      label: thetaLabel,
      value: formatNum(summaryForDisplay.netTheta, 2),
      tone: toneFromSigned(summaryForDisplay.netTheta),
    },
    {
      label: "ν Vega /1%",
      value: formatNum(summaryForDisplay.netVega, 2),
      tone: toneFromSigned(summaryForDisplay.netVega),
    },
  ];

  const railHeadlineMetrics = metricItems.slice(0, 4);
  const railColumnMetrics = metricItems.slice(4);

  const footnotes = (
    <>
      <p className="text-xs text-slate-muted">
        † Max profit/loss at expiry; unlimited risk or reward shown as Unlimited.
      </p>
      <p className="text-xs text-slate-muted">
        Greeks are Black-76 model estimates (r=0) from leg IV — not exchange prints. Γ is per
        100 pts of forward; Θ is per{" "}
        {summaryForDisplay.thetaPerHour ? "hour when under 1 day to expiry" : "calendar day"}; ν
        is per 1 vol point.
      </p>
      <p className="text-xs text-slate-muted">
        ‡ PoP / E[PnL] / P(max) are IV-implied at expiry
        {blendedIv
          ? ` (σ* ${blendedIv.ivPct.toFixed(1)}% from ${blendedIv.chainLegs}/${blendedIv.legs} leg IV`
          : ""}
        {blendedIv && blendedIv.parityLegs > 0 ? `, ${blendedIv.parityLegs} parity` : ""}
        {blendedIv && blendedIv.interpLegs > 0 ? `, ${blendedIv.interpLegs} interp` : ""}
        {blendedIv && blendedIv.ltpLegs > 0 ? `, ${blendedIv.ltpLegs} from LTP` : ""}
        {blendedIv && blendedIv.atmFallbackLegs > 0
          ? `, ${blendedIv.atmFallbackLegs} ATM fallback`
          : ""}
        {blendedIv ? ")" : ""}
        {`, ~${daysToExpiry != null ? Math.round(daysToExpiry) : "—"}d`} — not a guarantee.
        Booked P&L is live LTP vs builder premium × lot size. Click chain CE/PE to cycle
        buy → sell → off.
      </p>
    </>
  );

  const legsTable = (
    <div className={cn("overflow-auto rounded-lg border border-line", rail && "min-h-0 flex-1")}>
      <div className="flex items-center justify-between gap-2 border-b border-line bg-raised/80 px-2 py-1">
        <p className="th-label">Legs</p>
        <div className="flex items-center gap-1">
          {editingLegs ? (
            <Button type="button" size="sm" variant="secondary" onClick={addBlankLeg}>
              Add leg
            </Button>
          ) : null}
          <Button
            type="button"
            size="sm"
            variant={editingLegs ? "primary" : "secondary"}
            onClick={() => setEditingLegs((v) => !v)}
          >
            {editingLegs ? "Done" : "Edit legs"}
          </Button>
        </div>
      </div>
      <table className="min-w-full border-collapse text-left text-sm">
        <thead className="sticky top-0 z-10 bg-raised/95 backdrop-blur">
          <tr>
            <th className="th-label px-2 py-1.5">Side</th>
            <th className="th-label px-2 py-1.5">Type</th>
            <th className="th-label px-2 py-1.5 text-right">Strike</th>
            {editingLegs ? <th className="th-label px-2 py-1.5 text-right">Qty</th> : null}
            <th className="th-label px-2 py-1.5 text-right">OI</th>
            <th className="th-label px-2 py-1.5 text-right">Premium</th>
            {!editingLegs ? (
              <>
                <th className="th-label px-2 py-1.5 text-right">Δ Delta</th>
                <th className="th-label px-2 py-1.5 text-right">Γ Gamma</th>
                <th className="th-label px-2 py-1.5 text-right">
                  {summaryForDisplay.thetaPerHour ? "Θ Theta /h" : "Θ Theta /d"}
                </th>
                <th className="th-label px-2 py-1.5 text-right">ν Vega</th>
              </>
            ) : (
              <th className="th-label px-2 py-1.5" />
            )}
          </tr>
        </thead>
        <tbody>
          {legs.map((leg) => {
            const row = rows.find((item) => item.strike === leg.strike);
            const chainLeg = leg.type === "CE" ? row?.ce : row?.pe;
            const symbol = chainLeg?.symbol;
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
                <td className="px-2 py-1.5 capitalize">
                  {editingLegs ? (
                    <select
                      value={leg.side}
                      onChange={(e) =>
                        updateLegField(leg.id, { side: e.target.value as "buy" | "sell" })
                      }
                      className="rounded border border-line bg-canvas px-1 py-0.5 text-sm"
                    >
                      <option value="buy">buy</option>
                      <option value="sell">sell</option>
                    </select>
                  ) : (
                    leg.side
                  )}
                </td>
                <td
                  className={cn(
                    "px-2 py-1.5 font-medium",
                    leg.type === "CE" ? "text-teal" : "text-rose",
                  )}
                >
                  {editingLegs ? (
                    <select
                      value={leg.type}
                      onChange={(e) =>
                        updateLegField(leg.id, { type: e.target.value as "CE" | "PE" })
                      }
                      className="rounded border border-line bg-canvas px-1 py-0.5 text-sm"
                    >
                      <option value="CE">CE</option>
                      <option value="PE">PE</option>
                    </select>
                  ) : (
                    (name ?? `${leg.type} ${leg.strike}`)
                  )}
                </td>
                <td className="px-2 py-1.5 text-right tabular-nums">
                  {editingLegs ? (
                    <input
                      type="number"
                      step={strikeStep}
                      value={leg.strike}
                      onChange={(e) =>
                        updateLegField(leg.id, { strike: Number(e.target.value) || leg.strike })
                      }
                      className="ml-auto w-20 rounded border border-line bg-canvas px-1 py-0.5 text-right text-sm"
                    />
                  ) : (
                    leg.strike
                  )}
                </td>
                {editingLegs ? (
                  <td className="px-2 py-1.5 text-right">
                    <input
                      type="number"
                      min={1}
                      step={1}
                      value={leg.qty}
                      onChange={(e) =>
                        updateLegField(leg.id, {
                          qty: Math.max(1, Math.round(Number(e.target.value) || 1)),
                        })
                      }
                      className="ml-auto w-14 rounded border border-line bg-canvas px-1 py-0.5 text-right text-sm"
                    />
                  </td>
                ) : null}
                <td className="px-2 py-1.5 text-right tabular-nums text-ink">
                  {formatNum(chainLeg?.oi != null ? Math.round(chainLeg.oi) : null, 0)}
                </td>
                <td className="px-2 py-1.5 text-right">
                  <input
                    type="number"
                    min={0}
                    step={0.05}
                    value={leg.premium}
                    onChange={(e) => updateLegPremium(leg.id, Number(e.target.value))}
                    className={cn(
                      "ml-auto w-20 rounded-md border bg-canvas px-1.5 py-1 text-right text-sm tabular-nums",
                      leg.quoteMissing ? "border-amber-500/60" : "border-line",
                    )}
                    title={leg.quoteMissing ? "No chain quote at this strike" : undefined}
                  />
                </td>
                {!editingLegs ? (
                  <>
                    <td className="px-2 py-1.5 text-right tabular-nums">
                      <span className={metricToneClass(toneFromSigned(legGreeks?.delta))}>
                        {formatNum(legGreeks?.delta, 3)}
                      </span>
                    </td>
                    <td className="px-2 py-1.5 text-right tabular-nums">
                      <span className={metricToneClass(toneFromSigned(legGreeks?.gamma))}>
                        {formatGamma(legGreeks?.gamma)}
                      </span>
                    </td>
                    <td className="px-2 py-1.5 text-right tabular-nums">
                      <span className={metricToneClass(toneFromSigned(legGreeks?.theta))}>
                        {formatNum(legGreeks?.theta, 2)}
                      </span>
                    </td>
                    <td className="px-2 py-1.5 text-right tabular-nums">
                      <span className={metricToneClass(toneFromSigned(legGreeks?.vega))}>
                        {formatNum(legGreeks?.vega, 2)}
                      </span>
                    </td>
                  </>
                ) : (
                  <td className="px-2 py-1.5 text-right">
                    <button
                      type="button"
                      className="text-xs text-rose hover:underline"
                      onClick={() => removeLeg(leg.id)}
                    >
                      Remove
                    </button>
                  </td>
                )}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );

  /** Sensibull-style action strip: one compact row — funds through Buy. */
  const tradeFooter = (
    <div className="rounded-lg border border-line bg-canvas/50 px-2.5 py-1.5">
      <div className="flex items-center gap-x-3 text-sm">
        <div className="flex min-w-0 flex-1 flex-nowrap items-center gap-x-3 overflow-x-auto pb-0.5">
          <span className="shrink-0 text-slate-muted">
            Funds{" "}
            <span className="font-semibold tabular-nums text-ink">
              {formatNum(displayFundsNeeded, 0)}
            </span>
          </span>
          <span className="shrink-0 text-slate-muted">
            Margin{" "}
            <span className="font-semibold tabular-nums text-ink">
              {formatNum(displayMarginNeeded, 0)}
            </span>
            <span className="ml-1 text-[10px] uppercase tracking-wide opacity-70">
              {marginSource === "heuristic" || marginSource === "mock_heuristic"
                ? "est."
                : "broker"}
            </span>
          </span>
          <label className="flex shrink-0 items-center gap-1.5 text-slate-muted">
            Available
            <input
              type="number"
              min={0}
              step={100}
              value={marginAvailable}
              onChange={(e) => setMarginAvailable(Math.max(0, Number(e.target.value) || 0))}
              className="w-[7.5rem] shrink-0 rounded-md border border-line bg-canvas px-2 py-1 text-sm tabular-nums text-ink"
            />
          </label>
          <select
            value={product}
            onChange={(e) => setProduct(e.target.value as "NRML" | "MIS")}
            title="F&O product"
            className="w-[4.75rem] shrink-0 rounded-md border border-line bg-raised px-2 py-1 text-sm text-ink outline-none focus:border-teal focus:ring-2 focus:ring-teal/20"
          >
            <option value="NRML">NRML</option>
            <option value="MIS">MIS</option>
          </select>
          <select
            value={orderMode}
            onChange={(e) => setOrderMode(e.target.value as "legs" | "basket")}
            title={
              orderMode === "basket"
                ? "Basket: buy wave then sell wave in parallel — SPAN margins via get_basket_margins (no atomic place API)"
                : "Place each leg sequentially (buys before sells)"
            }
            className="w-[5.5rem] shrink-0 rounded-md border border-line bg-raised px-2 py-1 text-sm text-ink"
          >
            <option value="legs">Legs</option>
            <option value="basket">Basket</option>
          </select>
          <label
            className="flex shrink-0 items-center gap-1 text-slate-muted"
            title="Per-leg premium stop — Kite GTT after LIVE NRML accept (not MIS/paper)"
          >
            SL%
            <input
              type="number"
              min={0}
              step={1}
              value={stopLossPct}
              onChange={(e) => setStopLossPct(e.target.value)}
              placeholder="—"
              className="w-12 rounded border border-line bg-canvas px-1 py-1 text-sm tabular-nums text-ink"
            />
          </label>
          <label
            className="flex shrink-0 items-center gap-1 text-slate-muted"
            title="Per-leg premium target — Kite GTT after LIVE NRML accept (not MIS/paper)"
          >
            Tgt%
            <input
              type="number"
              min={0}
              step={1}
              value={targetPct}
              onChange={(e) => setTargetPct(e.target.value)}
              placeholder="—"
              className="w-12 rounded border border-line bg-canvas px-1 py-1 text-sm tabular-nums text-ink"
            />
          </label>
          <span className="shrink-0 text-slate-muted">
            Charges{" "}
            <span className="font-semibold tabular-nums text-ink">
              {formatNum(charges?.total, 2)}
            </span>
          </span>
        </div>
        <span className="flex shrink-0 items-center gap-2 border-l border-line pl-3">
          {pnlTable && pnlTable.spots.length > 0 ? (
            <Button
              type="button"
              size="sm"
              variant="secondary"
              icon={<TableIcon />}
              onClick={() => setPnlOpen(true)}
            >
              P&amp;L table
            </Button>
          ) : null}
          {legs.length > 0 && onQueuePortfolioSave ? (
            <Button
              type="button"
              size="sm"
              variant="secondary"
              icon={<SaveIcon />}
              onClick={handleBuyDraft}
            >
              Save draft
            </Button>
          ) : null}
          {legs.length > 0 ? (
            <Button
              type="button"
              size="sm"
              icon={<CheckIcon />}
              disabled={orderBusy || missingQuotes}
              onClick={() => void handleBuyLive()}
            >
              {orderBusy ? "Placing…" : snapshot?.mock ? "Buy (mock)" : "Buy"}
            </Button>
          ) : null}
        </span>
      </div>
      {marginWarning ? (
        <p className="mt-1.5 text-sm text-amber-700 dark:text-amber-300">{marginWarning}</p>
      ) : null}
      {orderMessage ? (
        <p className="mt-1.5 whitespace-pre-wrap text-sm text-ink">{orderMessage}</p>
      ) : null}
    </div>
  );

  const payoffFooter = (
    <div className="flex w-full flex-wrap items-center gap-x-4 gap-y-2">
      <InlineSlider
        id="strategy-target"
        label="Target date"
        value={Math.min(targetDayOffset, dteSliderMax)}
        display={
          atExpiryHorizon
            ? remainingAtTarget > 0
              ? `${remainingAtTarget.toFixed(1)}d left`
              : "At expiry"
            : `T+${targetDayOffset}d · ${remainingAtTarget.toFixed(1)}d left`
        }
        min={0}
        max={Math.max(0, dteSliderMax)}
        disabled={dteSliderMax <= 0}
        onChange={setTargetDayOffset}
        valueClassName="min-w-[7.5rem]"
      />
      <InlineSlider
        id="strategy-iv-shock"
        label="IV scenario"
        value={ivShockPts}
        display={`${ivShockPts >= 0 ? "+" : ""}${ivShockPts} pts${scenarioIv != null ? ` → σ ${scenarioIv.toFixed(1)}%` : ""}`}
        min={-10}
        max={10}
        onChange={setIvShockPts}
        valueClassName="min-w-[7.5rem]"
      />
    </div>
  );

  const payoff = (
    <OptionsLabPayoffChart
      points={payoffPoints}
      targetPoints={atExpiryHorizon ? undefined : targetPayoffPoints}
      scenarioPoints={scenarioPayoffPoints}
      spot={spot}
      breakevens={summaryForDisplay.breakevens}
      sdBands={sdBands}
      oiBars={oiBars}
      fill={rail}
      footer={rail ? payoffFooter : undefined}
      targetLabel={`Target T+${targetDayOffset}d`}
      scenarioLabel={
        ivShockPts === 0 ? undefined : `IV ${ivShockPts >= 0 ? "+" : ""}${ivShockPts}pts`
      }
    />
  );

  const pnlModal =
    pnlOpen && pnlTable && pnlTable.spots.length > 0 ? (
      <div
        className="fixed inset-0 z-50 flex items-center justify-center bg-ink/40 p-4"
        onClick={() => setPnlOpen(false)}
      >
        <div
          role="dialog"
          aria-modal="true"
          aria-label="P&L matrix"
          className="max-h-[80vh] w-full max-w-3xl overflow-auto rounded-xl border border-line bg-canvas p-4 shadow-lg"
          onClick={(e) => e.stopPropagation()}
        >
          <div className="mb-3 flex items-center justify-between gap-2">
            <p className="th-label">P&amp;L table · spot × remaining DTE</p>
            <Button
              type="button"
              size="icon"
              variant="secondary"
              icon={<CloseIcon />}
              aria-label="Close"
              title="Close"
              onClick={() => setPnlOpen(false)}
            />
          </div>
          <table className="min-w-full border-collapse text-left text-sm">
            <thead>
              <tr>
                <th className="th-label px-2 py-1.5">Spot</th>
                {pnlTable.remainingDtes.map((dte, colIdx) => (
                  <th key={colIdx} className="th-label px-2 py-1.5 tabular-nums">
                    {dte <= 0 ? "Expiry" : dte < 1 ? `${dte.toFixed(2)}d` : `${dte}d`}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {pnlTable.spots.map((s, rowIdx) => (
                <tr key={s} className="border-t border-line/70">
                  <td className="px-2 py-1.5 tabular-nums font-medium">{Math.round(s)}</td>
                  {pnlTable.cells[rowIdx].map((cell, colIdx) => (
                    <td
                      key={`${s}-${colIdx}`}
                      className={cn(
                        "px-2 py-1.5 tabular-nums",
                        cell > 0 ? "text-teal" : cell < 0 ? "text-rose" : "text-ink",
                      )}
                    >
                      {formatNum(cell, 1)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    ) : null;

  if (rail) {
    return (
      <div className="flex h-full min-h-0 flex-1 flex-col gap-2 overflow-hidden">
        {missingQuotes ? (
          <p className="shrink-0 rounded-md border border-amber-500/40 bg-amber-500/10 px-2 py-1.5 text-sm text-amber-800 dark:text-amber-200">
            Some legs have no chain quote — edit premiums or load matching strikes.
          </p>
        ) : null}

        {/* Net premium · Max P/L · PoP · Template — one lane; boxes match column size */}
        <div className="flex shrink-0 flex-nowrap items-center gap-2 overflow-x-auto">
          {railHeadlineMetrics.map(({ label, value, tone = "neutral" }) => (
            <div
              key={label}
              className="w-[7rem] shrink-0 rounded-md border border-line/70 bg-raised/40 px-1.5 py-1.5"
            >
              <p className="th-label truncate" title={label}>
                {label}
              </p>
              <p
                className={cn(
                  "mt-0.5 truncate text-sm font-semibold tabular-nums",
                  metricToneClass(tone),
                )}
                title={value}
              >
                {value}
              </p>
            </div>
          ))}
          <div className="flex min-w-0 flex-1 flex-wrap items-center gap-x-3 gap-y-1 rounded-md border border-line bg-canvas/40 px-2.5 py-1.5">
            <label htmlFor="strategy-template" className="th-label shrink-0">
              Template
            </label>
            <Select
              id="strategy-template"
              value={templateId}
              onChange={(e) => {
                const next = e.target.value as StrategyTemplateId;
                const tpl = STRATEGY_TEMPLATES.find((t) => t.id === next);
                if (!tpl || tpl.gated) return;
                setTemplateId(next);
              }}
              className="!w-[11rem] max-w-full shrink-0 !py-1.5"
            >
              {STRATEGY_TEMPLATES.map((item) => (
                <option key={item.id} value={item.id} disabled={Boolean(item.gated)}>
                  {item.label} · {item.hint}
                  {item.gated ? " (soon)" : ""}
                </option>
              ))}
            </Select>
            {template.gated ? (
              <p className="basis-full text-xs text-amber-800 dark:text-amber-200">
                {template.gateHint || "Template unavailable until dual-expiry is supported."}
              </p>
            ) : null}
            <InlineSlider
              id="strategy-shift"
              label="Shift"
              value={shiftSteps}
              display={`${shiftSteps >= 0 ? "+" : ""}${shiftSteps}`}
              min={-5}
              max={5}
              onChange={setShiftSteps}
            />
            {template.usesWidth ? (
              <InlineSlider
                id="strategy-width"
                label="Width"
                value={widthSteps}
                display={`${widthSteps * strikeStep}`}
                min={1}
                max={6}
                onChange={setWidthSteps}
              />
            ) : null}
          </div>
        </div>

        <div className="grid min-h-0 flex-1 grid-cols-1 gap-2 overflow-hidden md:grid-cols-[7rem_minmax(0,1fr)]">
          <aside className="min-h-0 overflow-auto md:pr-0.5">
            <MetricColumn items={railColumnMetrics} />
            {summaryForDisplay.breakevens.length > 0 ? (
              <p className="mt-2 px-0.5 text-xs text-slate-muted">
                BEs:{" "}
                <span className="font-semibold tabular-nums text-ink">
                  {summaryForDisplay.breakevens.join(", ")}
                </span>
              </p>
            ) : null}
          </aside>

          <div className="min-h-[14rem] min-w-0 overflow-hidden">{payoff}</div>
        </div>

        <div className="max-h-[28%] min-h-0 shrink-0 overflow-auto">{legsTable}</div>
        <div className="shrink-0">{tradeFooter}</div>
        <div className="max-h-[4.5rem] shrink-0 space-y-0.5 overflow-y-auto">{footnotes}</div>
        {pnlModal}
      </div>
    );
  }

  return (
    <div className="mt-3 flex min-h-0 flex-1 flex-col gap-4">
      <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]">
        <div className="space-y-3 rounded-lg border border-line bg-canvas/40 p-3">
          <div>
            <Label htmlFor="strategy-template-page">Template</Label>
            <Select
              id="strategy-template-page"
              value={templateId}
              onChange={(e) => {
                const next = e.target.value as StrategyTemplateId;
                const tpl = STRATEGY_TEMPLATES.find((t) => t.id === next);
                if (!tpl || tpl.gated) return;
                setTemplateId(next);
              }}
            >
              {STRATEGY_TEMPLATES.map((item) => (
                <option key={item.id} value={item.id} disabled={Boolean(item.gated)}>
                  {item.label} · {item.hint}
                  {item.gated ? " (soon)" : ""}
                </option>
              ))}
            </Select>
            {template.gated ? (
              <p className="mt-1 text-xs text-amber-800 dark:text-amber-200">
                {template.gateHint || "Template unavailable until dual-expiry is supported."}
              </p>
            ) : null}
          </div>
          <InlineSlider
            stacked
            id="strategy-shift-page"
            label="Strike shift (steps)"
            value={shiftSteps}
            display={`${shiftSteps >= 0 ? "+" : ""}${shiftSteps} × ${strikeStep} → center ${atm + shiftSteps * strikeStep}`}
            min={-5}
            max={5}
            onChange={setShiftSteps}
          />
          {template.usesWidth ? (
            <InlineSlider
              stacked
              id="strategy-width-page"
              label="Spread width (steps)"
              value={widthSteps}
              display={`${widthSteps} × ${strikeStep} = ${widthSteps * strikeStep} pts`}
              min={1}
              max={6}
              onChange={setWidthSteps}
            />
          ) : null}
          <InlineSlider
            stacked
            id="strategy-target-page"
            label="Target date (days from now)"
            value={Math.min(targetDayOffset, dteSliderMax)}
            display={
              atExpiryHorizon
                ? remainingAtTarget > 0
                  ? `Near expiry (${remainingAtTarget.toFixed(2)}d left)`
                  : "At expiry (intrinsic payoff)"
                : `T+${targetDayOffset}d · ${remainingAtTarget.toFixed(1)}d left to expiry`
            }
            min={0}
            max={Math.max(0, dteSliderMax)}
            disabled={dteSliderMax <= 0}
            onChange={setTargetDayOffset}
          />
          <InlineSlider
            stacked
            id="strategy-iv-page"
            label="IV scenario (vol pts)"
            value={ivShockPts}
            display={`${ivShockPts >= 0 ? "+" : ""}${ivShockPts} pts${scenarioIv != null ? ` → σ ${scenarioIv.toFixed(1)}%` : ""}`}
            min={-10}
            max={10}
            onChange={setIvShockPts}
          />
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            {metricItems.map(({ label, value, tone }) => (
              <MetricCell key={label} label={label} value={value} tone={tone} />
            ))}
          </div>
          {summaryForDisplay.breakevens.length > 0 ? (
            <p className="text-sm text-slate-muted">
              Breakevens:{" "}
              <span className="font-semibold tabular-nums text-ink">
                {summaryForDisplay.breakevens.join(", ")}
              </span>
            </p>
          ) : (
            <p className="text-sm text-slate-muted">No breakeven in scanned range.</p>
          )}
          {footnotes}
          {missingQuotes ? (
            <p className="rounded-md border border-amber-500/40 bg-amber-500/10 px-2 py-1.5 text-sm text-amber-800 dark:text-amber-200">
              Some legs have no chain quote at that strike — premiums are 0 until you edit them or
              load matching strikes. Max profit/loss and PoP stay hidden until all legs are quoted.
            </p>
          ) : null}
          {tradeFooter}
        </div>
        {payoff}
      </div>
      {legsTable}
      {pnlModal}
    </div>
  );
}
