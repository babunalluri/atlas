"use client";

import { useMemo } from "react";

import {
  buildFutOptions,
  buildOptionSideOptions,
  buildPresetOptions,
  buildStrikeStepOptions,
  buildUnderlyingOptions,
} from "@/components/domains/signal-setup-options";
import { CommonInstrumentSetupBar } from "@/components/domains/CommonInstrumentSetupBar";
import { Label } from "@/components/ui/Field";
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

  const ceOptions = useMemo(
    () =>
      buildOptionSideOptions(
        config?.fut_symbol,
        "CE",
        atmHint,
        config?.strike_step ?? 50,
        config?.ce_symbol,
      ),
    [atmHint, config?.ce_symbol, config?.fut_symbol, config?.strike_step],
  );

  const peOptions = useMemo(
    () =>
      buildOptionSideOptions(
        config?.fut_symbol,
        "PE",
        atmHint,
        config?.strike_step ?? 50,
        config?.pe_symbol,
      ),
    [atmHint, config?.fut_symbol, config?.pe_symbol, config?.strike_step],
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

  return (
    <CommonInstrumentSetupBar
      loading={loading}
      loadingLabel="Loading symbols…"
      config={config}
      presetKey={presetKey}
      presetLocked={presetLocked}
      onPresetChange={onPresetChange}
      onUnderlyingChange={onUnderlyingChange}
      onFutChange={(value) => patchConfig({ fut_symbol: value })}
      onStrikeStepChange={(value) =>
        patchConfig({ strike_step: Number(value) || 50 })
      }
      presetOptions={presetOptions}
      underlyingOptions={underlyingOptions}
      futOptions={futOptions}
      strikeOptions={strikeOptions}
      idPrefix="signal"
      containerClassName="rounded-lg border border-line bg-canvas/40 p-3"
      layoutClassName="grid gap-3 sm:grid-cols-2 lg:grid-cols-4"
      underlyingAllowCustom={!presetLocked}
      underlyingPlaceholder="Search underlying…"
      underlyingEmptyMessage="Type symbol, e.g. NSE:NIFTY"
      strikeAllowCustom
      futLabel="FUT (current expiry)"
      atmHint={atmHint}
      layoutExtras={
        <>
          <div>
            <Label htmlFor="signal-ce">CE symbol</Label>
            <SearchableSelect
              id="signal-ce"
              className="tnum"
              value={config?.ce_symbol ?? ""}
              onChange={(value) => patchConfig({ ce_symbol: value })}
              options={ceOptions}
              allowCustom
              placeholder="Search or type CE…"
              emptyMessage={
                atmHint != null
                  ? "Type CE symbol or pick ATM strike"
                  : "Set FUT and start engine for ATM suggestions"
              }
            />
          </div>
          <div>
            <Label htmlFor="signal-pe">PE symbol</Label>
            <SearchableSelect
              id="signal-pe"
              className="tnum"
              value={config?.pe_symbol ?? ""}
              onChange={(value) => patchConfig({ pe_symbol: value })}
              options={peOptions}
              allowCustom
              placeholder="Search or type PE…"
              emptyMessage={
                atmHint != null
                  ? "Type PE symbol or pick ATM strike"
                  : "Set FUT and start engine for ATM suggestions"
              }
            />
          </div>
        </>
      }
    />
  );
}
