"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { OptionsLabPortfoliosPanel } from "@/components/domains/OptionsLabPortfoliosPanel";
import { OptionsLabIvChart } from "@/components/domains/OptionsLabIvChart";
import { OptionsLabOiChart } from "@/components/domains/OptionsLabOiChart";
import { OptionsLabScreenerPanel } from "@/components/domains/OptionsLabScreenerPanel";
import { OptionsLabSetupBar } from "@/components/domains/OptionsLabSetupBar";
import { OptionsLabStrategyPanel } from "@/components/domains/OptionsLabStrategyPanel";
import { OptionsLabStraddleChart } from "@/components/domains/OptionsLabStraddleChart";
import { useOptionsLabConfigAutosave } from "@/components/domains/useOptionsLabConfigAutosave";
import { suggestFutSymbol } from "@/components/domains/signal-setup-options";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { RefreshIcon } from "@/components/ui/icons";
import {
  getOptionsChain,
  getOptionsScreener,
  resetOptionsLabOiBaseline,
  resetOptionsLabScreenerBaseline,
  resetOptionsLabIvHistory,
  type OptionsChainLeg,
  type OptionsChainSnapshot,
  type OptionsScreenerRow,
  type OptionsScreenerSnapshot,
} from "@/lib/api/admin";
import { useAgentOsToken } from "@/lib/auth/token";
import { cn } from "@/lib/utils";

const POLL_MS = 2_000;
const SCREENER_POLL_MS = 60_000;
const WING_OPTIONS = [10, 15, 20] as const;
type LabView = "chain" | "oi" | "straddle" | "builder" | "screener" | "iv" | "portfolios";

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

function LegCell({ leg, tone }: { leg: OptionsChainLeg; tone: "ce" | "pe" }) {
  return (
    <div className="space-y-0.5 text-[11px] leading-tight">
      <div className="font-semibold tabular-nums text-ink">{formatNum(leg.ltp)}</div>
      <div className="flex flex-wrap gap-x-2 text-slate-muted">
        <span className="tabular-nums">OI {formatOi(leg.oi)}</span>
        <span className="tabular-nums">IV {formatNum(leg.iv, 1)}</span>
        {leg.delta != null ? (
          <span className={cn("tabular-nums", tone === "ce" ? "text-teal" : "text-rose")}>
            Δ {formatNum(leg.delta, 3)}
          </span>
        ) : null}
      </div>
    </div>
  );
}

export function OptionsLabPanel({ active = true }: { active?: boolean }) {
  const { getAccessToken, isLoaded, isSignedIn } = useAgentOsToken();
  const [view, setView] = useState<LabView>("chain");
  const [wings, setWings] = useState<number>(15);
  const [snapshot, setSnapshot] = useState<OptionsChainSnapshot | null>(null);
  const [screener, setScreener] = useState<OptionsScreenerSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [screenerError, setScreenerError] = useState<string | null>(null);
  const [resettingOi, setResettingOi] = useState(false);
  const [resettingScreener, setResettingScreener] = useState(false);
  const [resettingIv, setResettingIv] = useState(false);
  const [pendingPortfolioSave, setPendingPortfolioSave] = useState<PendingPortfolioSave | null>(
    null,
  );
  const mounted = useRef(true);
  const refreshSeq = useRef(0);
  const screenerSeq = useRef(0);

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
    try {
      const token = await getAccessToken();
      if (!token || !mounted.current) return;
      const data = await getOptionsScreener(token);
      if (!mounted.current || seq !== screenerSeq.current) return;
      setScreener(data);
      setScreenerError(data.ok ? null : data.error ?? "Screener unavailable");
    } catch (err) {
      if (mounted.current && seq === screenerSeq.current) {
        setScreenerError(err instanceof Error ? err.message : "Failed to load screener");
      }
    }
  }, [getAccessToken]);

  const onResetIvHistory = useCallback(async () => {
    setResettingIv(true);
    try {
      const token = await getAccessToken();
      if (!token) return;
      await resetOptionsLabIvHistory(token);
      await refresh();
      if (view === "screener") await refreshScreener();
    } finally {
      setResettingIv(false);
    }
  }, [getAccessToken, refresh, refreshScreener, view]);

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
    (row: OptionsScreenerRow) => {
      const preset = presets.find((item) => item.symbol === row.underlying_symbol);
      patchConfig({
        underlying_symbol: row.underlying_symbol,
        underlying_label: row.underlying_label,
        strike_step: preset?.strike_step ?? config?.strike_step ?? 50,
        fut_symbol: row.fut_symbol || suggestFutSymbol(row.underlying_symbol),
      });
      setView("chain");
    },
    [config?.strike_step, patchConfig, presets],
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
    if (!active || !isLoaded || !isSignedIn || !configReady) return;
    if (!config?.underlying_symbol?.trim() || !config.fut_symbol?.trim()) return;
    if (view === "screener" || view === "portfolios") return;
    void refresh();
    const timer = window.setInterval(() => void refresh(), POLL_MS);
    return () => window.clearInterval(timer);
  }, [
    active,
    config?.fut_symbol,
    config?.mock,
    config?.strike_step,
    config?.underlying_symbol,
    configReady,
    isLoaded,
    isSignedIn,
    refresh,
    view,
    wings,
  ]);

  useEffect(() => {
    if (!active || !isLoaded || !isSignedIn || !configReady) return;
    if (view !== "screener") return;
    void refreshScreener();
    const timer = window.setInterval(() => void refreshScreener(), SCREENER_POLL_MS);
    return () => window.clearInterval(timer);
  }, [
    active,
    config?.mock,
    configReady,
    isLoaded,
    isSignedIn,
    refreshScreener,
    view,
  ]);

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

  return (
    <section className="mt-5 flex min-h-[min(68vh,52rem)] flex-col rounded-xl border border-line bg-raised/20 p-4">
      <header className="border-b border-line pb-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              {snapshot?.mock ? <Badge tone="info">Mock</Badge> : null}
              {snapshot?.ok && !snapshot?.mock ? (
                <Badge tone="success" live>
                  Live
                </Badge>
              ) : snapshot?.ok ? null : (
                <Badge tone="warning">Offline</Badge>
              )}
              {saveLabel ? (
                <span className="text-xs text-slate-muted">{saveLabel}</span>
              ) : null}
            </div>
            <p className="mt-1 text-sm text-slate-muted">
              Chain, OI, straddle, IV, builder, screener, portfolios · ATM ±{wings} · refreshed{" "}
              {fetchedLabel ?? "…"}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <label className="flex items-center gap-2 text-xs text-slate-muted">
              Wings
              <select
                value={wings}
                onChange={(event) => setWings(Number(event.target.value))}
                className="rounded-md border border-line bg-canvas px-2 py-1 text-xs text-ink"
                disabled={view === "screener" || view === "iv" || view === "portfolios"}
              >
                {WING_OPTIONS.map((value) => (
                  <option key={value} value={value}>
                    ±{value}
                  </option>
                ))}
              </select>
            </label>
            {view === "iv" ? (
              <Button
                variant="secondary"
                size="sm"
                disabled={resettingIv}
                onClick={() => void onResetIvHistory()}
              >
                Reset IV history
              </Button>
            ) : null}
            {view === "oi" ? (
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
                void (view === "screener" ? refreshScreener() : refresh())
              }
            >
              Refresh
            </Button>
          </div>
        </div>

        <div
          role="tablist"
          aria-label="Options Lab views"
          className="mt-3 inline-flex rounded-lg border border-line bg-canvas/60 p-1"
        >
          {(
            [
              ["chain", "Chain"],
              ["oi", "OI"],
              ["straddle", "Straddle"],
              ["builder", "Builder"],
              ["screener", "Screener"],
              ["iv", "IV"],
              ["portfolios", "Portfolios"],
            ] as const
          ).map(([id, label]) => (
            <button
              key={id}
              type="button"
              role="tab"
              aria-selected={view === id}
              onClick={() => setView(id)}
              className={cn(
                "rounded-md px-3 py-1.5 text-sm font-medium transition",
                view === id
                  ? "bg-raised text-ink shadow-sm"
                  : "text-slate-muted hover:text-ink",
              )}
            >
              {label}
            </button>
          ))}
        </div>
      </header>

      <div className="mt-3">
        <OptionsLabSetupBar
          config={config}
          presets={presets}
          presetKey={presetKey}
          presetLocked={presetLocked}
          onPresetChange={onPresetChange}
          patchConfig={patchConfig}
          loading={configLoading}
          atmHint={atmHint}
        />
      </div>

      {configError ? (
        <div className="mt-3 rounded-lg border border-rose/30 bg-rose/10 px-3 py-2 text-sm text-rose">
          {configError}
        </div>
      ) : null}

      {!configReady && !configLoading ? (
        <div className="mt-3 rounded-lg border border-line bg-canvas/60 px-3 py-2 text-sm text-slate-muted">
          Saving Options Lab setup…
        </div>
      ) : null}

      {error && configReady ? (
        <div className="mt-3 rounded-lg border border-rose/30 bg-rose/10 px-3 py-2 text-sm text-rose">
          {error}
        </div>
      ) : null}

      {screenerError && view === "screener" ? (
        <div className="mt-3 rounded-lg border border-rose/30 bg-rose/10 px-3 py-2 text-sm text-rose">
          {screenerError}
        </div>
      ) : null}

      {(snapshot?.warnings?.length ?? 0) > 0 && view !== "screener" ? (
        <ul className="mt-2 list-inside list-disc rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-950">
          {snapshot?.warnings?.map((msg) => (
            <li key={msg}>{msg}</li>
          ))}
        </ul>
      ) : null}

      {view === "chain" ? (
        <>
          <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-8">
            {[
              ["Spot", formatNum(snapshot?.spot)],
              ["ATM", snapshot?.atm != null ? String(snapshot.atm) : "—"],
              ["PCR", formatNum(summary?.pcr, 3)],
              ["Max pain", summary?.max_pain != null ? String(summary.max_pain) : "—"],
              ["ATM IV", formatNum(summary?.atm_iv, 1)],
              ["IVP", summary?.ivp != null ? `${Math.round(summary.ivp)}` : "—"],
              ["CE OI", formatOi(summary?.chain_ce_oi)],
              ["PE OI", formatOi(summary?.chain_pe_oi)],
            ].map(([label, value]) => (
              <div
                key={label}
                className="rounded-lg border border-line bg-canvas/60 px-3 py-2"
              >
                <div className="text-[10px] font-semibold uppercase tracking-wide text-slate-muted">
                  {label}
                </div>
                <div className="font-display text-base font-semibold tabular-nums text-ink">
                  {value}
                </div>
              </div>
            ))}
          </div>

          <div className="mt-3 min-h-0 flex-1 overflow-auto rounded-lg border border-line">
            <table className="min-w-full border-collapse text-left text-xs">
              <thead className="sticky top-0 z-10 bg-raised/95 backdrop-blur">
                <tr className="border-b border-line text-[10px] uppercase tracking-wide text-slate-muted">
                  <th className="px-3 py-2">Strike</th>
                  <th className="px-3 py-2">Call (CE)</th>
                  <th className="px-3 py-2">Put (PE)</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr
                    key={row.strike}
                    className={cn(
                      "border-b border-line/70",
                      row.is_atm && "bg-teal/10",
                    )}
                  >
                    <td className="px-3 py-2 font-semibold tabular-nums">
                      {row.strike}
                      {row.is_atm ? (
                        <span className="ml-1 text-[10px] font-medium text-teal">ATM</span>
                      ) : null}
                    </td>
                    <td className="px-3 py-2">
                      <LegCell leg={row.ce} tone="ce" />
                    </td>
                    <td className="px-3 py-2">
                      <LegCell leg={row.pe} tone="pe" />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : null}

      {view === "oi" ? (
        <div className="mt-3 min-h-0 flex-1">
          {oiBaselineLabel ? (
            <p className="mb-2 text-[11px] text-slate-muted">
              Δ OI baseline set at {oiBaselineLabel} IST session
            </p>
          ) : null}
          <OptionsLabOiChart rows={charts?.oi ?? []} spot={snapshot?.spot ?? null} />
        </div>
      ) : null}

      {view === "iv" ? (
        <div className="mt-3 min-h-0 flex-1">
          <OptionsLabIvChart
            points={charts?.iv?.points ?? []}
            atmIv={charts?.iv?.atm_iv ?? summary?.atm_iv ?? null}
            ivp={charts?.iv?.ivp ?? summary?.ivp ?? null}
            sampleDays={charts?.iv?.sample_days}
          />
        </div>
      ) : null}

      {view === "screener" ? (
        <OptionsLabScreenerPanel
          snapshot={screener}
          onRefresh={() => void refreshScreener()}
          onResetBaseline={() => void onResetScreenerBaseline()}
          resetting={resettingScreener}
          onSelectUnderlying={onSelectScreenerUnderlying}
        />
      ) : null}

      {view === "builder" ? (
        <OptionsLabStrategyPanel
          snapshot={snapshot}
          strikeStep={config?.strike_step ?? snapshot?.strike_step ?? 50}
          onQueuePortfolioSave={(payload) => {
            setPendingPortfolioSave(payload);
            setView("portfolios");
          }}
        />
      ) : null}

      {view === "portfolios" ? (
        <OptionsLabPortfoliosPanel
          active={active && view === "portfolios"}
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
      ) : null}

      {view === "straddle" ? (
        <div className="mt-3 min-h-0 flex-1">
          <OptionsLabStraddleChart
            points={charts?.straddle.points ?? []}
            atm={charts?.straddle.atm ?? snapshot?.atm ?? null}
          />
        </div>
      ) : null}
    </section>
  );
}
