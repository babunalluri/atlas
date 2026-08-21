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
import { Label } from "@/components/ui/Field";
import { Button } from "@/components/ui/Button";
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
            `${row.strike}:${row.ce.ltp ?? ""}:${row.pe.ltp ?? ""}:${row.ce.iv ?? ""}:${row.pe.iv ?? ""}:${row.ce.delta ?? ""}:${row.pe.delta ?? ""}`,
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
    const remainingDtes = [
      maxR,
      Math.max(0, maxR * 0.66),
      Math.max(0, maxR * 0.33),
      0,
    ].map((v) => Math.round(v * 10) / 10);
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

  useEffect(() => {
    if (legs.length === 0 || missingQuotes) {
      setBrokerMargin(null);
      setBrokerFunds(null);
      setMarginSource("heuristic");
      return;
    }
    // Clear stale broker numbers while the debounced refresh runs.
    setBrokerMargin(null);
    setBrokerFunds(null);
    let cancelled = false;
    const timer = window.setTimeout(async () => {
      try {
        const token = await getAccessToken();
        if (!token || cancelled) return;
        const legPayload = legs.map((leg) => {
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
        if (legPayload.some((leg) => !leg.symbol)) {
          setMarginSource("heuristic");
          setMarginWarning("Some legs lack symbols — showing heuristic margin.");
          return;
        }
        const res = await postOptionsLabMargins(token, {
          legs: legPayload,
          lot_size: lotSize,
          product,
          underlying_symbol: snapshot?.underlying_symbol,
          heuristic: fundsMargins
            ? {
                marginNeeded: fundsMargins.marginNeeded,
                fundsNeeded: fundsMargins.fundsNeeded,
              }
            : undefined,
          mock: snapshot?.mock,
        });
        if (cancelled || !res.ok) return;
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
    }, 400);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [
    fundsMargins,
    getAccessToken,
    legs,
    lotSize,
    missingQuotes,
    product,
    rows,
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
    const confirmed = window.confirm(
      isMock
        ? `Simulate ${legs.length} leg(s) as ${product} mock orders and save a draft?`
        : `Place ${legs.length} leg(s) as ${product} LIMIT orders?\n\n` +
            `Buys are sent before sells. Paper tools are preferred when bound.\n` +
            `Quantity = lots × ${lotSize}.\n\n` +
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
          "No paper place tool available. Send LIVE place_order to the broker?",
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

      if (res.partial) {
        setOrderMessage(
          [
            `PARTIAL — ${res.submitted_count ?? "?"} submitted, ${res.failed_count ?? "?"} failed.`,
            "Do not re-Buy the full set; check broker positions first.",
            ...lines,
            ...(res.warnings || []),
          ].join("\n"),
        );
        return;
      }
      if (!res.ok) {
        setOrderMessage(
          [res.error || res.errors?.join("; ") || "Order failed.", ...lines]
            .filter(Boolean)
            .join("\n"),
        );
        return;
      }
      setOrderMessage(
        res.mock
          ? "Mock orders simulated + draft saved."
          : `Submitted via ${res.tool || "broker"} (${res.team_slug || "?"})\n${lines.join("\n")}`,
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

          <div>
            <Label htmlFor="strategy-target">Target date (days from now)</Label>
            <input
              id="strategy-target"
              type="range"
              min={0}
              max={Math.max(0, dteSliderMax)}
              step={1}
              value={Math.min(targetDayOffset, dteSliderMax)}
              onChange={(e) => setTargetDayOffset(Number(e.target.value))}
              className="mt-2 w-full"
              disabled={dteSliderMax <= 0}
            />
            <p className="mt-1 text-xs text-slate-muted tabular-nums">
              {atExpiryHorizon
                ? remainingAtTarget > 0
                  ? `Near expiry (${remainingAtTarget.toFixed(2)}d left)`
                  : "At expiry (intrinsic payoff)"
                : `T+${targetDayOffset}d · ${remainingAtTarget.toFixed(1)}d left to expiry`}
            </p>
          </div>

          <div>
            <Label htmlFor="strategy-iv-shock">IV scenario (vol pts)</Label>
            <input
              id="strategy-iv-shock"
              type="range"
              min={-10}
              max={10}
              step={1}
              value={ivShockPts}
              onChange={(e) => setIvShockPts(Number(e.target.value))}
              className="mt-2 w-full"
            />
            <p className="mt-1 text-xs text-slate-muted tabular-nums">
              {ivShockPts >= 0 ? "+" : ""}
              {ivShockPts} pts
              {scenarioIv != null ? ` → σ ${scenarioIv.toFixed(1)}%` : ""}
            </p>
          </div>

          <div className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
            {[
              ["Net premium", formatNum(summaryForDisplay.netPremium)],
              ["Max profit†", formatExtreme(summaryForDisplay.maxProfit)],
              ["Max loss†", formatExtreme(summaryForDisplay.maxLoss)],
              ["PoP‡", formatPop(summaryForDisplay.pop)],
              ["E[PnL]‡", formatNum(summaryForDisplay.expectedPnl)],
              ["P(max)‡", formatPop(summaryForDisplay.pMaxProfit)],
              ["PoP@target‡", formatPop(summaryForDisplay.targetPop)],
              ["Booked P&L ₹", formatNum(booked)],
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
            ‡ PoP / E[PnL] / P(max) are IV-implied at expiry
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
            guarantee. Booked P&L is live LTP vs builder premium × lot size. Target marks use
            per-leg IV (chain/parity/interp/LTP), not only blended σ*.
          </p>

          {missingQuotes ? (
            <p className="rounded-md border border-amber-500/40 bg-amber-500/10 px-2 py-1.5 text-xs text-amber-800 dark:text-amber-200">
              Some legs have no chain quote at that strike — premiums are 0 until you edit them or
              load matching strikes. Max profit/loss and PoP stay hidden until all legs are quoted.
            </p>
          ) : null}

          <div className="rounded-md border border-line/70 bg-raised/30 p-2">
            <div className="mb-1 flex items-center justify-between gap-2">
              <p className="text-[10px] font-semibold uppercase tracking-wide text-slate-muted">
                Funds &amp; margins
              </p>
              <div className="flex items-center gap-2">
                <select
                  value={product}
                  onChange={(e) => setProduct(e.target.value as "NRML" | "MIS")}
                  className="rounded border border-line bg-canvas px-1.5 py-0.5 text-[10px] text-ink"
                  title="F&O product"
                >
                  <option value="NRML">NRML</option>
                  <option value="MIS">MIS</option>
                </select>
                <label className="flex items-center gap-1 text-[10px] text-slate-muted">
                  Available
                  <input
                    type="number"
                    min={0}
                    step={100}
                    value={marginAvailable}
                    onChange={(e) => setMarginAvailable(Math.max(0, Number(e.target.value) || 0))}
                    className="w-24 rounded border border-line bg-canvas px-1.5 py-0.5 tabular-nums text-ink"
                  />
                </label>
              </div>
            </div>
            <div className="grid grid-cols-3 gap-2 text-xs">
              <div>
                <div className="text-[10px] text-slate-muted">Funds needed</div>
                <div className="font-semibold tabular-nums">
                  {formatNum(displayFundsNeeded, 0)}
                </div>
              </div>
              <div>
                <div className="text-[10px] text-slate-muted">Margin needed</div>
                <div className="font-semibold tabular-nums">
                  {formatNum(displayMarginNeeded, 0)}
                </div>
              </div>
              <div>
                <div className="text-[10px] text-slate-muted">Margin available</div>
                <div className="font-semibold tabular-nums">{formatNum(marginAvailable, 0)}</div>
              </div>
            </div>
            <p className="mt-1 text-[10px] text-slate-muted">
              Source: {marginSource} · lot {lotSize}
              {marginSource === "heuristic" || marginSource === "mock_heuristic"
                ? " — heuristic until broker margin tools respond."
                : " — broker per-leg sum (not basket SPAN)."}
            </p>
            {marginWarning ? (
              <p className="mt-1 text-[10px] text-amber-700 dark:text-amber-300">{marginWarning}</p>
            ) : null}
          </div>

          <div className="rounded-md border border-line/70 bg-raised/30 p-2">
            <p className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-slate-muted">
              Charges &amp; buy
            </p>
            <div className="flex flex-wrap items-end justify-between gap-2">
              <div className="text-xs">
                <div className="text-[10px] text-slate-muted">Est. charges</div>
                <div className="font-semibold tabular-nums">{formatNum(charges?.total, 2)}</div>
                <p className="mt-0.5 text-[10px] text-slate-muted">
                  Brokerage {formatNum(charges?.brokerage, 2)} · STT{" "}
                  {formatNum(charges?.stt, 2)} · exch+GST{" "}
                  {formatNum(
                    charges != null ? charges.exchangeTxn + charges.gst : null,
                    2,
                  )}
                </p>
              </div>
              <div className="flex gap-2">
                {legs.length > 0 ? (
                  <>
                    {onQueuePortfolioSave ? (
                      <Button
                        type="button"
                        size="sm"
                        variant="secondary"
                        onClick={handleBuyDraft}
                      >
                        Save draft
                      </Button>
                    ) : null}
                    <Button
                      type="button"
                      size="sm"
                      disabled={orderBusy || missingQuotes}
                      onClick={() => void handleBuyLive()}
                    >
                      {orderBusy ? "Placing…" : snapshot?.mock ? "Buy (mock)" : "Buy"}
                    </Button>
                  </>
                ) : null}
              </div>
            </div>
            {orderMessage ? (
              <p className="mt-1 whitespace-pre-wrap text-[10px] text-ink">{orderMessage}</p>
            ) : (
              <p className="mt-1 text-[10px] text-slate-muted">
                Buy prefers <code>place_paper_order</code>; live <code>place_order</code> needs
                confirm + <code>live=true</code>. LIMIT at builder premium × lot size.
              </p>
            )}
          </div>
        </div>

        <OptionsLabPayoffChart
          points={payoffPoints}
          targetPoints={atExpiryHorizon ? undefined : targetPayoffPoints}
          scenarioPoints={scenarioPayoffPoints}
          spot={spot}
          breakevens={summaryForDisplay.breakevens}
          sdBands={sdBands}
          oiBars={oiBars}
          targetLabel={`Target T+${targetDayOffset}d`}
          scenarioLabel={
            ivShockPts === 0
              ? undefined
              : `IV ${ivShockPts >= 0 ? "+" : ""}${ivShockPts}pts`
          }
        />
      </div>

      {pnlTable && pnlTable.spots.length > 0 ? (
        <div className="overflow-auto rounded-lg border border-line">
          <div className="border-b border-line bg-raised/60 px-3 py-2 text-[10px] uppercase tracking-wide text-slate-muted">
            P&amp;L table · spot × remaining DTE (model)
          </div>
          <table className="min-w-full border-collapse text-left text-[11px]">
            <thead className="bg-raised/90 text-[10px] uppercase tracking-wide text-slate-muted">
              <tr>
                <th className="px-2 py-1.5">Spot</th>
                {pnlTable.remainingDtes.map((dte) => (
                  <th key={dte} className="px-2 py-1.5 tabular-nums">
                    {dte <= 0.05 ? "Expiry" : `${dte}d`}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {pnlTable.spots.map((s, rowIdx) => (
                <tr key={s} className="border-t border-line/70">
                  <td className="px-2 py-1 tabular-nums font-medium">{Math.round(s)}</td>
                  {pnlTable.cells[rowIdx].map((cell, colIdx) => (
                    <td
                      key={`${s}-${colIdx}`}
                      className={cn(
                        "px-2 py-1 tabular-nums",
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
      ) : null}

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
