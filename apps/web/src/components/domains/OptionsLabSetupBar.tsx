"use client";

import { useMemo, type ReactNode } from "react";

import {
  buildFutOptions,
  buildPresetOptions,
  buildStrikeStepOptions,
  buildUnderlyingOptions,
  suggestFutSymbol,
} from "@/components/domains/signal-setup-options";
import { CommonInstrumentSetupBar } from "@/components/domains/CommonInstrumentSetupBar";
import { publishDeskInstrument } from "@/components/domains/desk-instrument";
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
  layoutExtras = null,
  readOnly = false,
}: {
  config: OptionsLabAdminConfig | null;
      presets: Array<{
        label: string;
        symbol: string;
        strike_step: number;
        fut_symbol?: string;
      }>;
  presetKey: string;
  presetLocked: boolean;
  onPresetChange: (value: string) => void;
  patchConfig: (patch: Partial<OptionsLabAdminConfig>) => void;
  loading: boolean;
  atmHint?: number | null;
  /** Inline market strip (Spot / PCR / …) on the same dense row. */
  layoutExtras?: ReactNode;
  readOnly?: boolean;
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
        const fut =
          preset?.fut_symbol?.trim() || suggestFutSymbol(symbol) || "";
        patchConfig({
          underlying_symbol: symbol,
          underlying_label: preset?.label ?? symbol,
          ...(preset?.strike_step != null ? { strike_step: preset.strike_step } : {}),
          ...(fut ? { fut_symbol: fut } : { fut_symbol: "" }),
        });
        publishDeskInstrument({
          underlying_symbol: symbol,
          underlying_label: preset?.label ?? symbol,
          fut_symbol: fut || undefined,
          strike_step: preset?.strike_step,
          source: "options-lab",
        });
      }}
      onFutChange={(value) => patchConfig({ fut_symbol: value })}
      onStrikeStepChange={(value) => patchConfig({ strike_step: Number(value) })}
      presetOptions={presetOptions}
      underlyingOptions={underlyingOptions}
      futOptions={futOptions}
      strikeOptions={strikeOptions}
      idPrefix="options-lab"
      dense
      showTradingView
      readOnly={readOnly}
      allowPresetChange={false}
      containerClassName="w-full rounded-md border border-line bg-canvas/40 px-2 py-1"
      layoutClassName="flex w-full min-w-0 flex-wrap items-center gap-x-3 gap-y-1"
      futLabel="FUT"
      atmHint={layoutExtras ? null : atmHint}
      layoutExtras={layoutExtras}
    />
  );
}
