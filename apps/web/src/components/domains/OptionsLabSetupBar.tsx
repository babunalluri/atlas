"use client";

import { useMemo } from "react";

import {
  buildFutOptions,
  buildPresetOptions,
  buildStrikeStepOptions,
  buildUnderlyingOptions,
} from "@/components/domains/signal-setup-options";
import { CommonInstrumentSetupBar } from "@/components/domains/CommonInstrumentSetupBar";
import type { OptionsLabAdminConfig } from "@/lib/api/admin";

export function OptionsLabSetupBar({
  config,
  presets,
  presetKey,
  presetLocked,
  onPresetChange,
  patchConfig,
  loading,
  atmHint = null,
}: {
  config: OptionsLabAdminConfig | null;
  presets: Array<{ label: string; symbol: string; strike_step: number }>;
  presetKey: string;
  presetLocked: boolean;
  onPresetChange: (value: string) => void;
  patchConfig: (patch: Partial<OptionsLabAdminConfig>) => void;
  loading: boolean;
  atmHint?: number | null;
}) {
  const presetOptions = useMemo(() => buildPresetOptions(presets), [presets]);

  const underlyingOptions = useMemo(
    () =>
      config
        ? buildUnderlyingOptions(presets, {
            underlying_symbol: config.underlying_symbol,
          })
        : [],
    [config, presets],
  );

  const strikeOptions = useMemo(
    () => buildStrikeStepOptions(presets, config?.strike_step),
    [config?.strike_step, presets],
  );

  const futOptions = useMemo(
    () => buildFutOptions(config?.fut_symbol, config?.underlying_symbol),
    [config?.fut_symbol, config?.underlying_symbol],
  );

  return (
    <CommonInstrumentSetupBar
      loading={loading}
      loadingLabel="Loading Options Lab setup…"
      config={config}
      presetKey={presetKey}
      presetLocked={presetLocked}
      onPresetChange={onPresetChange}
      onUnderlyingChange={(symbol) => {
        const preset = presets.find((p) => p.symbol === symbol);
        patchConfig({
          underlying_symbol: symbol,
          underlying_label: preset?.label ?? symbol,
        });
      }}
      onFutChange={(value) => patchConfig({ fut_symbol: value })}
      onStrikeStepChange={(value) => patchConfig({ strike_step: Number(value) })}
      presetOptions={presetOptions}
      underlyingOptions={underlyingOptions}
      futOptions={futOptions}
      strikeOptions={strikeOptions}
      idPrefix="options-lab"
      containerClassName="rounded-lg border border-line bg-canvas/40 p-3"
      layoutClassName="grid gap-3 sm:grid-cols-2 lg:grid-cols-4"
      futLabel="FUT symbol"
      atmHint={atmHint}
      footer={
        <label className="flex items-center gap-2 text-sm text-ink">
          <input
            type="checkbox"
            checked={Boolean(config?.mock)}
            onChange={(event) => patchConfig({ mock: event.target.checked })}
            className="size-4 rounded border-line"
          />
          Mock data (demo — uncheck for live Kite quotes)
        </label>
      }
    />
  );
}
