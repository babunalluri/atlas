"use client";

import type { ReactNode } from "react";

import { Label } from "@/components/ui/Field";
import {
  SearchableSelect,
  type SearchableSelectOption,
} from "@/components/ui/SearchableSelect";

type CommonInstrumentConfig = {
  underlying_symbol: string;
  fut_symbol?: string | null;
  strike_step?: number | null;
};

export function CommonInstrumentSetupBar({
  loading,
  loadingLabel,
  config,
  presetKey,
  presetLocked,
  onPresetChange,
  onUnderlyingChange,
  onFutChange,
  onStrikeStepChange,
  presetOptions,
  underlyingOptions,
  futOptions,
  strikeOptions,
  idPrefix,
  containerClassName,
  layoutClassName,
  underlyingAllowCustom = false,
  underlyingPlaceholder,
  underlyingEmptyMessage,
  strikeAllowCustom = false,
  futLabel = "FUT symbol",
  futPlaceholder = "Search or type FUT…",
  futEmptyMessage = "Type FUT symbol, e.g. NFO:NIFTY26AUGFUT",
  atmHint = null,
  footer = null,
  extras = null,
  layoutExtras = null,
}: {
  loading: boolean;
  loadingLabel: string;
  config: CommonInstrumentConfig | null;
  presetKey: string;
  presetLocked: boolean;
  onPresetChange: (value: string) => void;
  onUnderlyingChange: (symbol: string) => void;
  onFutChange: (value: string) => void;
  onStrikeStepChange: (value: string) => void;
  presetOptions: SearchableSelectOption[];
  underlyingOptions: SearchableSelectOption[];
  futOptions: SearchableSelectOption[];
  strikeOptions: SearchableSelectOption[];
  idPrefix: string;
  containerClassName?: string;
  layoutClassName: string;
  underlyingAllowCustom?: boolean;
  underlyingPlaceholder?: string;
  underlyingEmptyMessage?: string;
  strikeAllowCustom?: boolean;
  futLabel?: string;
  futPlaceholder?: string;
  futEmptyMessage?: string;
  atmHint?: number | null;
  footer?: ReactNode;
  extras?: ReactNode;
  layoutExtras?: ReactNode;
}) {
  if (loading || !config) {
    return <p className="text-sm text-slate-muted">{loadingLabel}</p>;
  }

  return (
    <div className={containerClassName}>
      <div className={layoutClassName}>
        <div>
          <Label htmlFor={`${idPrefix}-preset`}>Preset</Label>
          <SearchableSelect
            id={`${idPrefix}-preset`}
            value={presetKey}
            onChange={onPresetChange}
            options={presetOptions}
            placeholder="Search preset…"
          />
        </div>
        <div>
          <Label htmlFor={`${idPrefix}-underlying`}>Underlying</Label>
          <SearchableSelect
            id={`${idPrefix}-underlying`}
            className="tnum"
            value={config.underlying_symbol || ""}
            onChange={onUnderlyingChange}
            options={underlyingOptions}
            disabled={presetLocked}
            allowCustom={underlyingAllowCustom}
            placeholder={underlyingPlaceholder}
            emptyMessage={underlyingEmptyMessage}
          />
        </div>
        <div>
          <Label htmlFor={`${idPrefix}-fut`}>{futLabel}</Label>
          <SearchableSelect
            id={`${idPrefix}-fut`}
            className="tnum"
            value={config.fut_symbol || ""}
            onChange={onFutChange}
            options={futOptions}
            allowCustom
            placeholder={futPlaceholder}
            emptyMessage={futEmptyMessage}
          />
        </div>
        <div>
          <Label htmlFor={`${idPrefix}-strike-step`}>Strike step</Label>
          <SearchableSelect
            id={`${idPrefix}-strike-step`}
            className="tnum"
            value={String(config.strike_step ?? 50)}
            disabled={presetLocked}
            onChange={onStrikeStepChange}
            options={strikeOptions}
            allowCustom={strikeAllowCustom}
            placeholder={strikeAllowCustom ? "Search step…" : undefined}
            emptyMessage={strikeAllowCustom ? "Type strike step" : undefined}
          />
        </div>
        {layoutExtras}
      </div>

      {extras}

      {footer ?? atmHint != null ? (
        <div className="mt-2 flex items-end gap-3">
          {footer}
          {atmHint != null ? (
            <span className="text-xs text-slate-muted">Live ATM hint: {atmHint}</span>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
