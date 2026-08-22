"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { OptionsLabPortfoliosPanel } from "@/components/domains/OptionsLabPortfoliosPanel";
import { OptionsLabBacktestPanel } from "@/components/domains/OptionsLabBacktestPanel";
import { OptionsLabFlowsPanel } from "@/components/domains/OptionsLabFlowsPanel";
import { OptionsLabIdeasPanel } from "@/components/domains/OptionsLabIdeasPanel";
import { OptionsLabHeatmapPanel } from "@/components/domains/OptionsLabHeatmapPanel";
import { OptionsLabIvChart } from "@/components/domains/OptionsLabIvChart";
import { OptionsLabOiChart } from "@/components/domains/OptionsLabOiChart";
import { OptionsLabScreenerPanel } from "@/components/domains/OptionsLabScreenerPanel";
import { OptionsLabSetupBar } from "@/components/domains/OptionsLabSetupBar";
import { OptionsLabStrategyPanel } from "@/components/domains/OptionsLabStrategyPanel";
import { OptionsLabStraddleChart } from "@/components/domains/OptionsLabStraddleChart";
import { useOptionsLabConfigAutosave } from "@/components/domains/useOptionsLabConfigAutosave";
import { suggestFutSymbol } from "@/components/domains/signal-setup-options";
import type { StrategyLeg, StrategyTemplateId } from "@/components/domains/options-lab-strategy";
import { STRATEGY_TEMPLATES } from "@/components/domains/options-lab-strategy";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import {
  BookIcon,
  BuildingIcon,
  ChartLineIcon,
  CloseIcon,
  HistoryIcon,
  LayersIcon,
  RefreshIcon,
  SearchIcon,
} from "@/components/ui/icons";
import {
  getOptionsChain,
  getOptionsLabFlows,
  getOptionsScreener,
  resetOptionsLabOiBaseline,
  resetOptionsLabScreenerBaseline,
  resetOptionsLabIvHistory,
  type OptionsChainLeg,
  type OptionsChainSnapshot,
  type OptionsLabFlowsSnapshot,
  type OptionsScreenerRow,
  type OptionsScreenerSnapshot,
} from "@/lib/api/admin";
import { streamOptionsChain } from "@/lib/api/options-lab-stream";
import { useAgentOsToken } from "@/lib/auth/token";
import { cn } from "@/lib/utils";

const SCREENER_POLL_MS = 60_000;
const WING_OPTIONS = [10, 15, 20] as const;
/** Left analysis pane — market read before building. */
type AnalysisPane = "quotes" | "oi" | "straddle" | "iv";
type DeskOverlay = null | "screener" | "books" | "heatmap" | "flows" | "backtest" | "ideas";

type PendingPortfolioSave = {
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
};

type ChainToggle = { seq: number; strike: number; type: "CE" | "PE" };

function formatNum(value: number | null | undefined, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  if (Number.isInteger(value)) return value.toLocaleString();
  return value.toFixed(digits);
}

function formatOi(value: number | null | undefined) {
  if (value === null || value === undefined) return "—";
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(2)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}K`;
  return String(Math.round(value));
}

type ChainTone = "pos" | "neg" | "neutral" | "muted";

function chainToneClass(tone: ChainTone) {
  if (tone === "pos") return "text-teal";
  if (tone === "neg") return "text-rose";
  if (tone === "muted") return "text-slate-muted";
  return "text-ink";
}

/** IV vs ATM IV: rich → rose, cheap → teal. */
function ivTone(
  iv: number | null | undefined,
  atmIv: number | null | undefined,
): ChainTone {
  if (iv == null || Number.isNaN(iv)) return "muted";
  if (atmIv == null || Number.isNaN(atmIv) || atmIv <= 0) return "neutral";
  if (iv > atmIv * 1.05) return "neg";
  if (iv < atmIv * 0.95) return "pos";
  return "neutral";
}

/** Higher CE OI vs PE → rose on CE / teal on PE (wall bias). */
function oiToneAtStrike(
  own: number | null | undefined,
  peer: number | null | undefined,
  side: "CE" | "PE",
): ChainTone {
  if (own == null) return "muted";
  if (peer == null) return "neutral";
  if (own === peer) return "neutral";
  const ownHigher = own > peer;
  if (side === "CE") return ownHigher ? "neg" : "pos";
  return ownHigher ? "pos" : "neg";
}

/** ITM premium emphasis by side. */
function ltpTone(
  strike: number,
  side: "CE" | "PE",
  spot: number | null,
): ChainTone {
  if (spot == null) return "neutral";
  if (side === "CE") return strike < spot ? "pos" : strike > spot ? "muted" : "neutral";
  return strike > spot ? "neg" : strike < spot ? "muted" : "neutral";
}

function SideChip({ side }: { side: "buy" | "sell" | null }) {
  if (!side) return null;
  return (
    <span
      className={cn(
        "ml-0.5 text-[10px] font-bold uppercase",
        side === "buy" ? "text-teal" : "text-rose",
      )}
    >
      {side === "buy" ? "B" : "S"}
    </span>
  );
}

/** Dense Sensibull-style chain cell — LTP primary, OI visible, one click to trade. */
function QuoteHit({
  leg,
  activeSide,
  onPick,
  align,
  tone = "neutral",
}: {
  leg: OptionsChainLeg;
  activeSide: "buy" | "sell" | null;
  onPick: () => void;
  align: "left" | "right";
  tone?: ChainTone;
}) {
  return (
    <button
      type="button"
      onClick={onPick}
      title="Click to cycle buy → sell → off"
      className={cn(
        "block w-full rounded px-1 py-0.5 tabular-nums transition hover:bg-fog/50",
        align === "right" ? "text-right" : "text-left",
        activeSide === "buy" && "bg-teal/15 ring-1 ring-inset ring-teal/40",
        activeSide === "sell" && "bg-rose/15 ring-1 ring-inset ring-rose/40",
      )}
    >
      <span className={cn("text-sm font-semibold", chainToneClass(tone))}>
        {formatNum(leg.ltp)}
        <SideChip side={activeSide} />
      </span>
    </button>
  );
}

export function OptionsLabPanel({ active = true }: { active?: boolean }) {
  const { getAccessToken, isLoaded, isSignedIn } = useAgentOsToken();
  const [analysisPane, setAnalysisPane] = useState<AnalysisPane>("quotes");
  const [overlay, setOverlay] = useState<DeskOverlay>(null);
  const [wings, setWings] = useState<number>(15);
  const [snapshot, setSnapshot] = useState<OptionsChainSnapshot | null>(null);
  const [screener, setScreener] = useState<OptionsScreenerSnapshot | null>(null);
  const [screenerUniverse, setScreenerUniverse] = useState<
    "indices" | "equities" | "all"
  >("indices");
  const [flows, setFlows] = useState<OptionsLabFlowsSnapshot | null>(null);
  const [flowsLoading, setFlowsLoading] = useState(false);
  const [screenerLoading, setScreenerLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [screenerError, setScreenerError] = useState<string | null>(null);
  const [flowsError, setFlowsError] = useState<string | null>(null);
  const [resettingOi, setResettingOi] = useState(false);
  const [resettingScreener, setResettingScreener] = useState(false);
  const [resettingIv, setResettingIv] = useState(false);
  const [pendingPortfolioSave, setPendingPortfolioSave] = useState<PendingPortfolioSave | null>(
    null,
  );
  const [streaming, setStreaming] = useState(false);
  const [chainToggle, setChainToggle] = useState<ChainToggle | null>(null);
  const [activeLegs, setActiveLegs] = useState<StrategyLeg[]>([]);
  const [applyTemplate, setApplyTemplate] = useState<{
    id: StrategyTemplateId;
    seq: number;
  } | null>(null);
  const [backtestHandoffHint, setBacktestHandoffHint] = useState<string | null>(null);
  const [backtestHandoffUnderlying, setBacktestHandoffUnderlying] = useState<string | null>(
    null,
  );
  const mounted = useRef(true);
  const refreshSeq = useRef(0);
  const screenerSeq = useRef(0);
  const flowsSeq = useRef(0);
  const chainToggleSeq = useRef(0);

  const {
    config,
    presets,
    presetKey,
    presetLocked,
    loading: configLoading,
    saveStatus,
    configReady,
    error: configError,
    patchConfig,
    onPresetChange,
  } = useOptionsLabConfigAutosave(getAccessToken, isLoaded && isSignedIn && active);

  const atmHint = useMemo(
    () => (snapshot?.atm != null ? Math.round(snapshot.atm) : null),
    [snapshot?.atm],
  );

  const legSideAt = useCallback(
    (strike: number, type: "CE" | "PE") => {
      const leg = activeLegs.find((item) => item.strike === strike && item.type === type);
      return leg?.side ?? null;
    },
    [activeLegs],
  );

  const onChainPick = useCallback((strike: number, type: "CE" | "PE") => {
    chainToggleSeq.current += 1;
    setChainToggle({ seq: chainToggleSeq.current, strike, type });
  }, []);

  const onLegsChange = useCallback((legs: StrategyLeg[]) => {
    setActiveLegs(legs);
  }, []);

  /** Hide prior-strategy legs until the *loaded chain* matches the Ideas handoff.
   * Key on snapshot (not config): patchConfig is optimistic and would open the gate
   * while applyTemplate still rebuilds against the previous chain. */
  const backtestLegs = useMemo(() => {
    if (!backtestHandoffUnderlying) return activeLegs;
    if ((snapshot?.underlying_symbol ?? "") !== backtestHandoffUnderlying) {
      return [];
    }
    return activeLegs;
  }, [activeLegs, backtestHandoffUnderlying, snapshot?.underlying_symbol]);

  const refresh = useCallback(async () => {
    const seq = ++refreshSeq.current;
    try {
      const token = await getAccessToken();
      if (!token || !mounted.current) return;
      const data = await getOptionsChain(token, wings);
      if (!mounted.current || seq !== refreshSeq.current) return;
      setSnapshot(data);
      setError(data.ok ? null : data.error ?? "Chain unavailable");
    } catch (err) {
      if (mounted.current && seq === refreshSeq.current) {
        setError(err instanceof Error ? err.message : "Failed to load chain");
      }
    }
  }, [getAccessToken, wings]);

  const refreshScreener = useCallback(async () => {
    const seq = ++screenerSeq.current;
    setScreenerLoading(true);
    try {
      const token = await getAccessToken();
      if (!token || !mounted.current) return;
      const data = await getOptionsScreener(token, screenerUniverse);
      if (!mounted.current || seq !== screenerSeq.current) return;
      setScreener(data);
      setScreenerError(data.ok ? null : data.error ?? "Screener unavailable");
    } catch (err) {
      if (mounted.current && seq === screenerSeq.current) {
        setScreenerError(err instanceof Error ? err.message : "Failed to load screener");
      }
    } finally {
      if (mounted.current && seq === screenerSeq.current) setScreenerLoading(false);
    }
  }, [getAccessToken, screenerUniverse]);

  const refreshFlows = useCallback(async () => {
    const seq = ++flowsSeq.current;
    setFlowsLoading(true);
    try {
      const token = await getAccessToken();
      if (!token || !mounted.current) return;
      const data = await getOptionsLabFlows(token);
      if (!mounted.current || seq !== flowsSeq.current) return;
      setFlows(data);
      setFlowsError(data.ok ? null : data.error ?? "Flows unavailable");
    } catch (err) {
      if (mounted.current && seq === flowsSeq.current) {
        setFlowsError(err instanceof Error ? err.message : "Failed to load flows");
      }
    } finally {
      if (mounted.current && seq === flowsSeq.current) setFlowsLoading(false);
    }
  }, [getAccessToken]);

  const onResetIvHistory = useCallback(async () => {
    setResettingIv(true);
    try {
      const token = await getAccessToken();
      if (!token) return;
      await resetOptionsLabIvHistory(token);
      await refresh();
      if (overlay === "screener") await refreshScreener();
    } finally {
      setResettingIv(false);
    }
  }, [getAccessToken, overlay, refresh, refreshScreener]);

  const onResetScreenerBaseline = useCallback(async () => {
    setResettingScreener(true);
    try {
      const token = await getAccessToken();
      if (!token) return;
      await resetOptionsLabScreenerBaseline(token);
      await refreshScreener();
    } finally {
      setResettingScreener(false);
    }
  }, [getAccessToken, refreshScreener]);

  const onSelectScreenerUnderlying = useCallback(
    (row: OptionsScreenerRow, opts?: { closeOverlay?: boolean }) => {
      const preset = presets.find((item) => item.symbol === row.underlying_symbol);
      patchConfig({
        underlying_symbol: row.underlying_symbol,
        underlying_label: row.underlying_label,
        strike_step: preset?.strike_step ?? config?.strike_step ?? 50,
        fut_symbol: row.fut_symbol || suggestFutSymbol(row.underlying_symbol),
      });
      if (opts?.closeOverlay !== false) {
        setOverlay(null);
      }
    },
    [config?.strike_step, patchConfig, presets],
  );

  const onApplyIdea = useCallback(
    (row: OptionsScreenerRow, templateId: StrategyTemplateId) => {
      onSelectScreenerUnderlying(row);
      setApplyTemplate((prev) => ({
        id: templateId,
        seq: (prev?.seq ?? 0) + 1,
      }));
    },
    [onSelectScreenerUnderlying],
  );

  const onSendIdeaToBacktest = useCallback(
    (row: OptionsScreenerRow, templateId: StrategyTemplateId) => {
      const tplLabel =
        STRATEGY_TEMPLATES.find((t) => t.id === templateId)?.label ?? templateId;
      // Drop stale rail legs immediately so Backtest never saves the prior strategy.
      setActiveLegs([]);
      onSelectScreenerUnderlying(row, { closeOverlay: false });
      setApplyTemplate((prev) => ({
        id: templateId,
        seq: (prev?.seq ?? 0) + 1,
      }));
      setBacktestHandoffHint(`${row.underlying_label} · ${tplLabel}`);
      setBacktestHandoffUnderlying(row.underlying_symbol);
      setOverlay("backtest");
    },
    [onSelectScreenerUnderlying],
  );

  const onResetOiBaseline = useCallback(async () => {
    setResettingOi(true);
    try {
      const token = await getAccessToken();
      if (!token) return;
      await resetOptionsLabOiBaseline(token);
      await refresh();
    } finally {
      setResettingOi(false);
    }
  }, [getAccessToken, refresh]);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  useEffect(() => {
    if (!overlay) return;
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") setOverlay(null);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [overlay]);

  useEffect(() => {
    if (overlay !== "backtest") {
      setBacktestHandoffHint(null);
      setBacktestHandoffUnderlying(null);
    }
  }, [overlay]);

  const streamGeneration = useRef(0);

  useEffect(() => {
    if (!active || !isLoaded || !isSignedIn || !configReady) return;
    if (!config?.underlying_symbol?.trim() || !config.fut_symbol?.trim()) return;

    const controller = new AbortController();
    const myGeneration = ++streamGeneration.current;
    let cancelled = false;
    setStreaming(true);

    void (async () => {
      let attempt = 0;
      while (!cancelled && myGeneration === streamGeneration.current) {
        try {
          const token = await getAccessToken();
          if (!token || !mounted.current || controller.signal.aborted) return;
          setStreaming(true);
          let gotFrame = false;
          await streamOptionsChain({
            accessToken: token,
            wings,
            signal: controller.signal,
            onState: (data) => {
              if (
                !mounted.current ||
                controller.signal.aborted ||
                myGeneration !== streamGeneration.current
              ) {
                return;
              }
              if (!gotFrame) {
                gotFrame = true;
                attempt = 0;
              }
              setSnapshot(data);
              setError(data.ok ? null : data.error ?? "Chain unavailable");
            },
          });
          if (controller.signal.aborted || cancelled) return;
          if (mounted.current && myGeneration === streamGeneration.current) {
            setStreaming(false);
          }
          attempt += 1;
        } catch (err) {
          if (
            !mounted.current ||
            controller.signal.aborted ||
            cancelled ||
            myGeneration !== streamGeneration.current
          ) {
            return;
          }
          setStreaming(false);
          setError(err instanceof Error ? err.message : "Options Lab stream failed");
          if (attempt === 0) void refresh();
          attempt += 1;
        }
        if (controller.signal.aborted || cancelled) return;
        const delayMs = Math.min(8_000, 500 * 2 ** Math.min(attempt - 1, 4));
        await new Promise((resolve) => window.setTimeout(resolve, delayMs));
      }
      if (mounted.current && myGeneration === streamGeneration.current) {
        setStreaming(false);
      }
    })();

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [
    active,
    config?.fut_symbol,
    config?.mock,
    config?.strike_step,
    config?.underlying_symbol,
    configReady,
    getAccessToken,
    isLoaded,
    isSignedIn,
    refresh,
    wings,
  ]);

  useEffect(() => {
    if (!active || !isLoaded || !isSignedIn || !configReady) return;
    if (overlay !== "screener" && overlay !== "heatmap" && overlay !== "ideas") return;
    void refreshScreener();
    const timer = window.setInterval(() => void refreshScreener(), SCREENER_POLL_MS);
    return () => window.clearInterval(timer);
  }, [
    active,
    config?.mock,
    configReady,
    isLoaded,
    isSignedIn,
    overlay,
    refreshScreener,
  ]);

  useEffect(() => {
    if (!active || !isLoaded || !isSignedIn || !configReady) return;
    if (overlay !== "flows") return;
    void refreshFlows();
  }, [active, config?.mock, configReady, isLoaded, isSignedIn, overlay, refreshFlows]);

  const rows = snapshot?.rows ?? [];
  const charts = snapshot?.charts;
  const summary = snapshot?.summary;
  const fetchedLabel =
    snapshot?.fetched_at != null
      ? new Date(snapshot.fetched_at * 1000).toLocaleTimeString()
      : null;
  const oiBaselineLabel =
    charts?.oi_baseline_at != null
      ? new Date(charts.oi_baseline_at * 1000).toLocaleTimeString()
      : null;

  const saveLabel =
    saveStatus === "pending" || saveStatus === "saving"
      ? "Saving…"
      : saveStatus === "saved"
        ? "Saved"
        : saveStatus === "error"
          ? "Save failed"
          : null;

  const spot = snapshot?.spot ?? null;
  const atm = snapshot?.atm ?? null;
  const pcr = summary?.pcr ?? null;
  const maxPain = summary?.max_pain ?? null;
  const ceOi = summary?.chain_ce_oi ?? null;
  const peOi = summary?.chain_pe_oi ?? null;
  const ivp = summary?.ivp ?? null;

  const summaryTone = (kind: "pos" | "neg" | "neutral") =>
    kind === "pos" ? "text-teal" : kind === "neg" ? "text-rose" : "text-ink";

  const spotTone: "pos" | "neg" | "neutral" =
    spot != null && atm != null
      ? spot > atm
        ? "pos"
        : spot < atm
          ? "neg"
          : "neutral"
      : "neutral";
  const pcrTone: "pos" | "neg" | "neutral" =
    pcr != null
      ? pcr >= 1.2
        ? "neg"
        : pcr <= 0.8
          ? "pos"
          : "neutral"
      : "neutral";
  const maxPainTone: "pos" | "neg" | "neutral" =
    maxPain != null && spot != null
      ? spot >= maxPain
        ? "pos"
        : "neg"
      : "neutral";
  const ivpTone: "pos" | "neg" | "neutral" =
    ivp != null ? (ivp >= 70 ? "neg" : ivp <= 30 ? "pos" : "neutral") : "neutral";
  const ceOiTone: "pos" | "neg" | "neutral" =
    ceOi != null && peOi != null
      ? ceOi > peOi
        ? "neg"
        : ceOi < peOi
          ? "pos"
          : "neutral"
      : "neutral";
  const peOiTone: "pos" | "neg" | "neutral" =
    ceOi != null && peOi != null
      ? peOi > ceOi
        ? "pos"
        : peOi < ceOi
          ? "neg"
          : "neutral"
      : "neutral";

  const summaryItems: Array<{
    label: string;
    parts: Array<{ text: string; tone: "pos" | "neg" | "neutral" }>;
  }> = [
    { label: "Spot", parts: [{ text: formatNum(spot), tone: spotTone }] },
    {
      label: "ATM",
      parts: [{ text: atm != null ? String(atm) : "—", tone: "neutral" }],
    },
    { label: "PCR", parts: [{ text: formatNum(pcr, 3), tone: pcrTone }] },
    {
      label: "Max pain",
      parts: [
        { text: maxPain != null ? String(maxPain) : "—", tone: maxPainTone },
      ],
    },
    {
      label: "ATM IV",
      parts: [{ text: formatNum(summary?.atm_iv, 1), tone: "neutral" }],
    },
    {
      label: "IVP",
      parts: [{ text: ivp != null ? `${Math.round(ivp)}` : "—", tone: ivpTone }],
    },
    {
      label: "CE / PE OI",
      parts: [
        { text: formatOi(ceOi), tone: ceOiTone },
        { text: "·", tone: "neutral" },
        { text: formatOi(peOi), tone: peOiTone },
      ],
    },
  ];

  return (
    <section className="relative flex h-full min-h-[36rem] flex-col overflow-hidden rounded-xl border border-line bg-raised/20 p-2">
      <header className="shrink-0 space-y-1.5 border-b border-line pb-1.5">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            {snapshot?.mock ? <Badge tone="info">Mock</Badge> : null}
            {snapshot?.ok && !snapshot?.mock ? (
              <Badge tone="success" live>
                Live
              </Badge>
            ) : snapshot?.ok ? null : (
              <Badge tone="warning">Offline</Badge>
            )}
            <Badge tone={streaming ? "success" : "warning"} dot={false}>
              Stream {streaming ? "connected" : "…"}
            </Badge>
            <span className="text-xs text-slate-muted tabular-nums">
              ATM ±{wings} · refreshed {fetchedLabel ?? "…"}
              {snapshot?.quote_source ? ` · ${snapshot.quote_source}` : ""}
            </span>
            {saveLabel ? (
              <span className="text-xs text-slate-muted">{saveLabel}</span>
            ) : null}
          </div>
          <div className="flex shrink-0 flex-wrap items-center justify-end gap-2">
            <Button
              variant={overlay === "screener" ? "primary" : "secondary"}
              size="sm"
              icon={<SearchIcon />}
              onClick={() => setOverlay((prev) => (prev === "screener" ? null : "screener"))}
            >
              Screener
            </Button>
            <Button
              variant={overlay === "heatmap" ? "primary" : "secondary"}
              size="sm"
              icon={<LayersIcon />}
              onClick={() => {
                setScreenerUniverse("equities");
                setOverlay((prev) => (prev === "heatmap" ? null : "heatmap"));
              }}
            >
              Heat map
            </Button>
            <Button
              variant={overlay === "flows" ? "primary" : "secondary"}
              size="sm"
              icon={<BuildingIcon />}
              onClick={() => setOverlay((prev) => (prev === "flows" ? null : "flows"))}
            >
              Flows
            </Button>
            <Button
              variant={overlay === "ideas" ? "primary" : "secondary"}
              size="sm"
              icon={<ChartLineIcon />}
              onClick={() => {
                setScreenerUniverse("all");
                setOverlay((prev) => (prev === "ideas" ? null : "ideas"));
              }}
            >
              Ideas
            </Button>
            <Button
              variant={overlay === "backtest" ? "primary" : "secondary"}
              size="sm"
              icon={<HistoryIcon />}
              onClick={() => setOverlay((prev) => (prev === "backtest" ? null : "backtest"))}
            >
              Backtest
            </Button>
            <Button
              variant={overlay === "books" ? "primary" : "secondary"}
              size="sm"
              icon={<BookIcon />}
              onClick={() => setOverlay((prev) => (prev === "books" ? null : "books"))}
            >
              Books
            </Button>
            <label
              htmlFor="options-lab-mock"
              title="Rehearsal mode — demo chain without live Kite quotes"
              className="flex cursor-pointer items-center gap-1.5 rounded-md border border-line bg-raised px-2 py-1 text-xs font-medium text-ink"
            >
              <input
                id="options-lab-mock"
                type="checkbox"
                checked={Boolean(config?.mock)}
                onChange={(e) => patchConfig({ mock: e.target.checked })}
                className="size-3.5 shrink-0 rounded border-line text-teal focus-visible:ring-2 focus-visible:ring-teal/30"
              />
              Mock
            </label>
            <label className="flex items-center gap-1.5 text-xs text-slate-muted">
              Wings
              <select
                value={wings}
                onChange={(event) => setWings(Number(event.target.value))}
                className="rounded-md border border-line bg-canvas px-2 py-1 text-xs text-ink"
              >
                {WING_OPTIONS.map((value) => (
                  <option key={value} value={value}>
                    ±{value}
                  </option>
                ))}
              </select>
            </label>
            {analysisPane === "iv" ? (
              <Button
                variant="secondary"
                size="sm"
                disabled={resettingIv}
                onClick={() => void onResetIvHistory()}
              >
                Reset IV
              </Button>
            ) : null}
            {analysisPane === "oi" ? (
              <Button
                variant="secondary"
                size="sm"
                disabled={resettingOi}
                onClick={() => void onResetOiBaseline()}
              >
                Reset Δ OI
              </Button>
            ) : null}
            <Button
              variant="secondary"
              size="sm"
              icon={<RefreshIcon />}
              onClick={() =>
                void (overlay === "screener" || overlay === "heatmap"
                  ? refreshScreener()
                  : overlay === "flows"
                    ? refreshFlows()
                    : refresh())
              }
            >
              Refresh
            </Button>
          </div>
        </div>

        <OptionsLabSetupBar
          config={config}
          presets={presets}
          presetKey={presetKey}
          presetLocked={presetLocked}
          onPresetChange={onPresetChange}
          patchConfig={patchConfig}
          loading={configLoading}
          atmHint={atmHint}
          layoutExtras={
            <div className="grid min-w-0 flex-[1.4] grid-cols-4 gap-x-3 gap-y-1 border-l border-line pl-3">
              {summaryItems.map(({ label, parts }) => (
                <div
                  key={label}
                  className={cn(
                    "flex min-w-0 items-baseline gap-1",
                    label === "CE / PE OI" && "col-span-2",
                  )}
                >
                  <span className="th-label shrink-0">{label}</span>
                  <span className="truncate text-sm font-semibold tabular-nums">
                    {parts.map((part, index) => (
                      <span
                        key={`${label}-${index}`}
                        className={cn(
                          part.tone === "neutral" && part.text === "·"
                            ? "mx-0.5 text-slate-muted"
                            : summaryTone(part.tone),
                        )}
                      >
                        {part.text}
                      </span>
                    ))}
                  </span>
                </div>
              ))}
            </div>
          }
        />

        {configError ? (
          <div className="rounded-md border border-rose/30 bg-rose/10 px-2 py-1 text-xs text-rose">
            {configError}
          </div>
        ) : null}
        {!configReady && !configLoading ? (
          <div className="rounded-md border border-line bg-canvas/60 px-2 py-1 text-xs text-slate-muted">
            Saving Options Lab setup…
          </div>
        ) : null}
        {error && configReady ? (
          <div className="rounded-md border border-rose/30 bg-rose/10 px-2 py-1 text-xs text-rose">
            {error}
          </div>
        ) : null}
        {(snapshot?.warnings?.length ?? 0) > 0 ? (
          <ul className="list-inside list-disc rounded-md border border-amber-200 bg-amber-50 px-2 py-1 text-[11px] text-amber-950">
            {snapshot?.warnings?.map((msg) => (
              <li key={msg}>{msg}</li>
            ))}
          </ul>
        ) : null}
      </header>

      <div className="mt-2 grid min-h-0 flex-1 grid-cols-1 gap-2 overflow-hidden md:grid-cols-[minmax(0,7fr)_minmax(0,13fr)]">
        {/* Left ~35% chain/OI · Right ~65% strategy (from md up) */}
        <div className="flex min-h-0 min-w-0 flex-col overflow-hidden rounded-lg border border-line bg-canvas/30">
          <div
            role="tablist"
            aria-label="Market analysis"
            className="flex shrink-0 flex-wrap items-center gap-1 border-b border-line bg-raised/40 px-2 py-1.5"
          >
            {(
              [
                ["quotes", "Quotes"],
                ["oi", "OI"],
                ["straddle", "Straddle"],
                ["iv", "IV"],
              ] as const
            ).map(([id, label]) => (
              <button
                key={id}
                type="button"
                role="tab"
                aria-selected={analysisPane === id}
                onClick={() => setAnalysisPane(id)}
                className={cn(
                  "rounded-md px-3 py-1.5 text-sm font-medium transition",
                  analysisPane === id
                    ? "bg-ink text-canvas"
                    : "text-slate-muted hover:bg-fog/50 hover:text-ink",
                )}
              >
                {label}
              </button>
            ))}
            {analysisPane === "quotes" ? (
              <span className="ml-auto hidden text-xs text-slate-muted sm:inline">
                Click CE/PE → buy → sell → off
              </span>
            ) : null}
            {analysisPane === "oi" && oiBaselineLabel ? (
              <span className="ml-auto text-xs text-slate-muted">
                Δ OI baseline {oiBaselineLabel} IST
              </span>
            ) : null}
          </div>

          {analysisPane === "quotes" ? (
            <div className="min-h-0 min-w-0 flex-1 overflow-auto">
              <table className="w-full border-collapse text-sm" style={{ tableLayout: "fixed" }}>
                <colgroup>
                  <col style={{ width: "14%" }} />
                  <col style={{ width: "12%" }} />
                  <col style={{ width: "16%" }} />
                  <col style={{ width: "16%" }} />
                  <col style={{ width: "16%" }} />
                  <col style={{ width: "12%" }} />
                  <col style={{ width: "14%" }} />
                </colgroup>
                <thead className="sticky top-0 z-10 bg-raised/95 backdrop-blur">
                  <tr className="border-b border-line/80">
                    <th
                      colSpan={3}
                      className="px-1 pb-0.5 pt-1.5 text-center text-xs font-semibold text-teal"
                    >
                      Calls
                    </th>
                    <th className="px-1" />
                    <th
                      colSpan={3}
                      className="px-1 pb-0.5 pt-1.5 text-center text-xs font-semibold text-rose"
                    >
                      Puts
                    </th>
                  </tr>
                  <tr className="border-b border-line">
                    <th className="th-label px-1.5 py-1 text-right">OI</th>
                    <th className="th-label px-1.5 py-1 text-right">IV</th>
                    <th className="th-label px-1.5 py-1 text-right">LTP</th>
                    <th className="th-label px-1.5 py-1 text-center">Strike</th>
                    <th className="th-label px-1.5 py-1 text-left">LTP</th>
                    <th className="th-label px-1.5 py-1 text-left">IV</th>
                    <th className="th-label px-1.5 py-1 text-left">OI</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => {
                    const ceSide = legSideAt(row.strike, "CE");
                    const peSide = legSideAt(row.strike, "PE");
                    const atmIv = summary?.atm_iv ?? null;
                    const ceItm = spot != null && row.strike < spot;
                    const peItm = spot != null && row.strike > spot;
                    return (
                      <tr
                        key={row.strike}
                        className={cn(
                          "border-b border-line/50",
                          row.is_atm && "bg-teal/10",
                        )}
                      >
                        <td
                          className={cn(
                            "px-1.5 py-1 text-right tabular-nums",
                            chainToneClass(
                              oiToneAtStrike(row.ce.oi, row.pe.oi, "CE"),
                            ),
                            !row.is_atm && ceItm && "bg-teal/5",
                          )}
                        >
                          {formatOi(row.ce.oi)}
                        </td>
                        <td
                          className={cn(
                            "px-1.5 py-1 text-right tabular-nums",
                            chainToneClass(ivTone(row.ce.iv, atmIv)),
                            !row.is_atm && ceItm && "bg-teal/5",
                          )}
                        >
                          {formatNum(row.ce.iv, 1)}
                        </td>
                        <td
                          className={cn(
                            "px-0.5 py-0.5",
                            !row.is_atm && ceItm && "bg-teal/5",
                          )}
                        >
                          <QuoteHit
                            leg={row.ce}
                            activeSide={ceSide}
                            onPick={() => onChainPick(row.strike, "CE")}
                            align="right"
                            tone={ltpTone(row.strike, "CE", spot)}
                          />
                        </td>
                        <td className="px-1.5 py-1 text-center font-semibold tabular-nums text-ink">
                          {row.strike}
                          {row.is_atm ? (
                            <div className="text-[10px] font-medium leading-none text-teal">
                              ATM
                            </div>
                          ) : null}
                        </td>
                        <td
                          className={cn(
                            "px-0.5 py-0.5",
                            !row.is_atm && peItm && "bg-rose/5",
                          )}
                        >
                          <QuoteHit
                            leg={row.pe}
                            activeSide={peSide}
                            onPick={() => onChainPick(row.strike, "PE")}
                            align="left"
                            tone={ltpTone(row.strike, "PE", spot)}
                          />
                        </td>
                        <td
                          className={cn(
                            "px-1.5 py-1 text-left tabular-nums",
                            chainToneClass(ivTone(row.pe.iv, atmIv)),
                            !row.is_atm && peItm && "bg-rose/5",
                          )}
                        >
                          {formatNum(row.pe.iv, 1)}
                        </td>
                        <td
                          className={cn(
                            "px-1.5 py-1 text-left tabular-nums",
                            chainToneClass(
                              oiToneAtStrike(row.pe.oi, row.ce.oi, "PE"),
                            ),
                            !row.is_atm && peItm && "bg-rose/5",
                          )}
                        >
                          {formatOi(row.pe.oi)}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : null}

          {analysisPane === "oi" ? (
            <div className="min-h-0 flex-1 overflow-hidden p-2">
              <OptionsLabOiChart
                rows={charts?.oi ?? []}
                spot={snapshot?.spot ?? null}
                fill
              />
            </div>
          ) : null}

          {analysisPane === "iv" ? (
            <div className="min-h-0 flex-1 overflow-auto p-3">
              <OptionsLabIvChart
                points={charts?.iv?.points ?? []}
                atmIv={charts?.iv?.atm_iv ?? summary?.atm_iv ?? null}
                ivp={charts?.iv?.ivp ?? summary?.ivp ?? null}
                sampleDays={charts?.iv?.sample_days}
              />
            </div>
          ) : null}

          {analysisPane === "straddle" ? (
            <div className="min-h-0 flex-1 overflow-auto p-3">
              <OptionsLabStraddleChart
                points={charts?.straddle.points ?? []}
                atm={charts?.straddle.atm ?? snapshot?.atm ?? null}
                series={charts?.straddle.series}
                strikes={charts?.straddle.strikes}
              />
            </div>
          ) : null}
        </div>

        {/* Step 3–5 — build, size, buy (always visible). */}
        <div className="flex min-h-0 min-w-0 flex-col overflow-hidden">
          <OptionsLabStrategyPanel
            snapshot={snapshot}
            strikeStep={config?.strike_step ?? snapshot?.strike_step ?? 50}
            variant="rail"
            chainToggle={chainToggle}
            applyTemplate={applyTemplate}
            onLegsChange={onLegsChange}
            onQueuePortfolioSave={(payload) => {
              setPendingPortfolioSave(payload);
              setOverlay("books");
            }}
          />
        </div>
      </div>

      {overlay ? (
        <div className="absolute inset-0 z-30 flex justify-end bg-ink/35">
          <button
            type="button"
            aria-label="Close overlay"
            className="absolute inset-0 cursor-default"
            onClick={() => setOverlay(null)}
          />
          <aside
            role="dialog"
            aria-modal="true"
            aria-label={
              overlay === "screener"
                ? "Screener"
                : overlay === "heatmap"
                  ? "Heat map"
                  : overlay === "flows"
                    ? "Flows"
                    : overlay === "ideas"
                      ? "Ideas"
                      : overlay === "backtest"
                        ? "Backtest"
                        : "Books"
            }
            className={cn(
              "relative z-10 flex h-full w-full flex-col border-l border-line bg-canvas shadow-xl",
              overlay === "screener" ||
                overlay === "heatmap" ||
                overlay === "ideas" ||
                overlay === "backtest"
                ? "max-w-5xl"
                : "max-w-3xl",
            )}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex shrink-0 items-center justify-between gap-2 border-b border-line px-3 py-2">
              <h2 className="flex items-center gap-2 font-display text-base font-semibold text-ink">
                {overlay === "screener" ? (
                  <SearchIcon className="h-4 w-4" />
                ) : overlay === "ideas" ? (
                  <ChartLineIcon className="h-4 w-4" />
                ) : overlay === "heatmap" ? (
                  <LayersIcon className="h-4 w-4" />
                ) : overlay === "flows" ? (
                  <BuildingIcon className="h-4 w-4" />
                ) : overlay === "backtest" ? (
                  <HistoryIcon className="h-4 w-4" />
                ) : (
                  <BookIcon className="h-4 w-4" />
                )}
                {overlay === "screener"
                  ? "Screener"
                  : overlay === "heatmap"
                    ? "Heat map"
                    : overlay === "flows"
                      ? "Flows"
                      : overlay === "ideas"
                        ? "Ideas"
                        : overlay === "backtest"
                          ? "Backtest"
                          : "Books"}
              </h2>
              <button
                type="button"
                onClick={() => setOverlay(null)}
                aria-label="Close"
                className="inline-flex h-7 w-7 items-center justify-center rounded-md text-slate-muted hover:bg-fog/70 hover:text-ink"
              >
                <CloseIcon className="h-4 w-4" />
              </button>
            </div>
            {(overlay === "screener" || overlay === "heatmap" || overlay === "ideas") &&
            screenerError ? (
              <div className="mx-3 mt-2 rounded-md border border-rose/30 bg-rose/10 px-2 py-1 text-xs text-rose">
                {screenerError}
              </div>
            ) : null}
            {overlay === "flows" && flowsError ? (
              <div className="mx-3 mt-2 rounded-md border border-rose/30 bg-rose/10 px-2 py-1 text-xs text-rose">
                {flowsError}
              </div>
            ) : null}
            <div className="min-h-0 flex-1 overflow-auto px-3 pb-3">
              {overlay === "screener" ? (
                <OptionsLabScreenerPanel
                  snapshot={screener}
                  loading={screenerLoading}
                  universe={screenerUniverse}
                  onUniverseChange={(next) => {
                    setScreenerUniverse(next);
                  }}
                  onRefresh={() => void refreshScreener()}
                  onResetBaseline={() => void onResetScreenerBaseline()}
                  resetting={resettingScreener}
                  onSelectUnderlying={onSelectScreenerUnderlying}
                />
              ) : overlay === "heatmap" ? (
                <OptionsLabHeatmapPanel
                  snapshot={screener}
                  loading={screenerLoading}
                  onRefresh={() => void refreshScreener()}
                  onSelectUnderlying={(row) => {
                    onSelectScreenerUnderlying(row);
                    setOverlay(null);
                  }}
                />
              ) : overlay === "ideas" ? (
                <OptionsLabIdeasPanel
                  snapshot={screener}
                  onApplyIdea={onApplyIdea}
                  onSendToBacktest={onSendIdeaToBacktest}
                />
              ) : overlay === "backtest" ? (
                <OptionsLabBacktestPanel
                  legs={backtestLegs}
                  spot={snapshot?.spot ?? null}
                  strikeStep={config?.strike_step ?? snapshot?.strike_step ?? 50}
                  ivPoints={charts?.iv?.points}
                  getAccessToken={getAccessToken}
                  underlyingSymbol={config?.underlying_symbol ?? snapshot?.underlying_symbol}
                  underlyingLabel={config?.underlying_label ?? snapshot?.underlying_label}
                  handoffHint={backtestHandoffHint}
                />
              ) : overlay === "flows" ? (
                <OptionsLabFlowsPanel
                  snapshot={flows}
                  loading={flowsLoading}
                  onRefresh={() => void refreshFlows()}
                />
              ) : (
                <OptionsLabPortfoliosPanel
                  active={active && overlay === "books"}
                  getAccessToken={getAccessToken}
                  configReady={configReady}
                  underlyingSymbol={config?.underlying_symbol ?? ""}
                  underlyingLabel={config?.underlying_label ?? ""}
                  futSymbol={config?.fut_symbol ?? ""}
                  strikeStep={config?.strike_step ?? 50}
                  mock={config?.mock ?? false}
                  pendingSave={pendingPortfolioSave}
                  onSaved={() => setPendingPortfolioSave(null)}
                />
              )}
            </div>
          </aside>
        </div>
      ) : null}
    </section>
  );
}
