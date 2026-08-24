"use client";

import { useMemo } from "react";

import {
  buildFutOptions,
  buildOptionSideOptions,
  buildPresetOptions,
  buildStrikeStepOptions,
  buildUnderlyingOptions,
  sanitizeOptionSymbol,
} from "@/components/domains/signal-setup-options";
import { CommonInstrumentSetupBar } from "@/components/domains/CommonInstrumentSetupBar";
import { SearchableSelect } from "@/components/ui/SearchableSelect";
import type { SignalEngineAdminConfig } from "@/lib/api/admin";
import { CUSTOM_PRESET } from "@/components/domains/signal-config-constants";

export function SignalSetupBar({
  config,
  presets,
  presetKey,
  presetLocked,
  onPresetChange,
  patchConfig,
  loading,
  atmHint = null,
}: {
  config: SignalEngineAdminConfig | null;
  presets: Array<{ label: string; symbol: string; strike_step: number }>;
  presetKey: string;
  presetLocked: boolean;
  onPresetChange: (value: string) => void;
  patchConfig: (patch: Partial<SignalEngineAdminConfig>) => void;
  loading: boolean;
  /** Live ATM from signal metrics — used to suggest CE/PE strikes. */
  atmHint?: number | null;
}) {
  const presetOptions = useMemo(() => buildPresetOptions(presets), [presets]);

  const underlyingOptions = useMemo(
    () => (config ? buildUnderlyingOptions(presets, config) : []),
    [config, presets],
  );

  const strikeOptions = useMemo(
    () => buildStrikeStepOptions(presets, config?.strike_step),
    [config?.strike_step, presets],
  );

  const futOptions = useMemo(
    () => buildFutOptions(config?.fut_symbol),
    [config?.fut_symbol],
  );

  const ceValue = sanitizeOptionSymbol(config?.ce_symbol);
  const peValue = sanitizeOptionSymbol(config?.pe_symbol);

  const ceOptions = useMemo(
    () =>
      buildOptionSideOptions(
        config?.fut_symbol,
        "CE",
        atmHint,
        config?.strike_step ?? 50,
        ceValue,
      ),
    [atmHint, ceValue, config?.fut_symbol, config?.strike_step],
  );

  const peOptions = useMemo(
    () =>
      buildOptionSideOptions(
        config?.fut_symbol,
        "PE",
        atmHint,
        config?.strike_step ?? 50,
        peValue,
      ),
    [atmHint, config?.fut_symbol, config?.strike_step, peValue],
  );

  if (loading || !config) {
    return <p className="text-sm text-slate-muted">Loading symbols…</p>;
  }

  function onUnderlyingChange(symbol: string) {
    const preset = presets.find((item) => item.symbol === symbol);
    if (preset) {
      onPresetChange(preset.symbol);
      return;
    }
    onPresetChange(CUSTOM_PRESET);
    patchConfig({
      underlying_symbol: symbol,
      underlying_label: symbol,
    });
  }

  function onFutChange(value: string) {
    const patch: Partial<SignalEngineAdminConfig> = { fut_symbol: value };
    // Clear CE/PE if they were wrongly set to the FUT (or any non-option).
    if (!sanitizeOptionSymbol(config?.ce_symbol)) patch.ce_symbol = "";
    if (!sanitizeOptionSymbol(config?.pe_symbol)) patch.pe_symbol = "";
    patchConfig(patch);
  }

  function onCeChange(value: string) {
    patchConfig({ ce_symbol: sanitizeOptionSymbol(value) });
  }

  function onPeChange(value: string) {
    patchConfig({ pe_symbol: sanitizeOptionSymbol(value) });
  }

  return (
    <CommonInstrumentSetupBar
      loading={loading}
      loadingLabel="Loading symbols…"
      config={config}
      presetKey={presetKey}
      presetLocked={presetLocked}
      onPresetChange={onPresetChange}
      onUnderlyingChange={onUnderlyingChange}
      onFutChange={onFutChange}
      onStrikeStepChange={(value) =>
        patchConfig({ strike_step: Number(value) || 50 })
      }
      presetOptions={presetOptions}
      underlyingOptions={underlyingOptions}
      futOptions={futOptions}
      strikeOptions={strikeOptions}
      idPrefix="signal"
      dense
      showTradingView
      containerClassName="w-full rounded-md border border-line bg-canvas/40 px-2 py-1"
      layoutClassName="flex w-full min-w-0 flex-wrap items-center gap-x-3 gap-y-1"
      underlyingAllowCustom={!presetLocked}
      underlyingPlaceholder="Search underlying…"
      underlyingEmptyMessage="Type symbol, e.g. NSE:NIFTY"
      strikeAllowCustom
      futLabel="FUT"
      atmHint={atmHint}
      layoutExtras={
        <>
          <div className="flex min-w-[10rem] flex-1 items-center gap-2">
            <label htmlFor="signal-ce" className="th-label shrink-0">
              CE
            </label>
            <div className="min-w-0 flex-1">
              <SearchableSelect
                id="signal-ce"
                className="tnum"
                value={ceValue}
                onChange={onCeChange}
                options={ceOptions}
                allowCustom
                placeholder="CE option…"
                emptyMessage={
                  atmHint != null
                    ? "Pick ATM CE (…CE), not FUT"
                    : "Set FUT and start engine for ATM CE suggestions"
                }
              />
            </div>
          </div>
          <div className="flex min-w-[10rem] flex-1 items-center gap-2">
            <label htmlFor="signal-pe" className="th-label shrink-0">
              PE
            </label>
            <div className="min-w-0 flex-1">
              <SearchableSelect
                id="signal-pe"
                className="tnum"
                value={peValue}
                onChange={onPeChange}
                options={peOptions}
                allowCustom
                placeholder="PE option…"
                emptyMessage={
                  atmHint != null
                    ? "Pick ATM PE (…PE), not FUT"
                    : "Set FUT and start engine for ATM PE suggestions"
                }
              />
            </div>
          </div>
        </>
      }
    />
  );
}
