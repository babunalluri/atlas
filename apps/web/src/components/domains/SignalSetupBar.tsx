"use client";

import { useMemo } from "react";

import {
  buildFutOptions,
  buildOptionSideOptions,
  buildPresetOptions,
  buildStrikeStepOptions,
  buildUnderlyingOptions,
} from "@/components/domains/signal-setup-options";
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
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-4">
      <div>
        <Label htmlFor="signal-preset">Preset</Label>
        <SearchableSelect
          id="signal-preset"
          value={presetKey}
          onChange={onPresetChange}
          options={presetOptions}
          placeholder="Search preset…"
        />
      </div>
      <div className="sm:col-span-2">
        <Label htmlFor="signal-underlying">Underlying</Label>
        <SearchableSelect
          id="signal-underlying"
          className="tnum"
          value={config.underlying_symbol}
          onChange={onUnderlyingChange}
          options={underlyingOptions}
          disabled={presetLocked}
          allowCustom={!presetLocked}
          placeholder="Search underlying…"
          emptyMessage="Type symbol, e.g. NSE:NIFTY"
        />
      </div>
      <div>
        <Label htmlFor="signal-strike">Strike step</Label>
        <SearchableSelect
          id="signal-strike"
          className="tnum"
          value={String(config.strike_step ?? 50)}
          onChange={(value) =>
            patchConfig({ strike_step: Number(value) || 50 })
          }
          options={strikeOptions}
          allowCustom
          placeholder="Search step…"
          emptyMessage="Type strike step"
        />
      </div>
      <div className="sm:col-span-2">
        <Label htmlFor="signal-fut">FUT (current expiry)</Label>
        <SearchableSelect
          id="signal-fut"
          className="tnum"
          value={config.fut_symbol ?? ""}
          onChange={(value) => patchConfig({ fut_symbol: value })}
          options={futOptions}
          allowCustom
          placeholder="Search or type FUT…"
          emptyMessage="Type FUT symbol, e.g. NFO:NIFTY26AUGFUT"
        />
      </div>
      <div>
        <Label htmlFor="signal-ce">CE symbol</Label>
        <SearchableSelect
          id="signal-ce"
          className="tnum"
          value={config.ce_symbol ?? ""}
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
          value={config.pe_symbol ?? ""}
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
    </div>
  );
}
