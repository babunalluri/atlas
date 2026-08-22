"use client";

import type { ReactNode } from "react";

import { openTradingViewChartWindow } from "@/components/domains/TradingViewChartWidget";
import { CUSTOM_PRESET } from "@/components/domains/signal-config-constants";
import { Button } from "@/components/ui/Button";
import { Label } from "@/components/ui/Field";
import { ChartLineIcon } from "@/components/ui/icons";
import {
  SearchableSelect,
  type SearchableSelectOption,
} from "@/components/ui/SearchableSelect";
import { cn } from "@/lib/utils";

type CommonInstrumentConfig = {
  underlying_symbol: string;
  fut_symbol?: string | null;
  strike_step?: number | null;
};

function FieldShell({
  dense,
  htmlFor,
  label,
  children,
  className,
}: {
  dense: boolean;
  htmlFor: string;
  label: string;
  children: ReactNode;
  className?: string;
}) {
  if (dense) {
    return (
      <div className={cn("flex min-w-0 flex-1 items-center gap-2", className)}>
        <label htmlFor={htmlFor} className="th-label shrink-0">
          {label}
        </label>
        <div className="min-w-0 flex-1">{children}</div>
      </div>
    );
  }
  return (
    <div className={className}>
      <Label htmlFor={htmlFor}>{label}</Label>
      {children}
    </div>
  );
}

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
  dense = false,
  showTradingView = false,
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
  /** Inline labels + single-row chrome for Options Lab desk. */
  dense?: boolean;
  /** Chart button between Preset and Underlying. */
  showTradingView?: boolean;
}) {
  if (loading || !config) {
    return <p className="text-sm text-slate-muted">{loadingLabel}</p>;
  }

  const presetOption =
    presetKey && presetKey !== CUSTOM_PRESET
      ? presetOptions.find((o) => o.value === presetKey)
      : undefined;
  // Prefer locked Preset ticker (NSE:HCLTECH); Custom falls back to underlying.
  const tvSymbol =
    (presetKey && presetKey !== CUSTOM_PRESET ? presetKey : "") ||
    config.underlying_symbol?.trim() ||
    "";

  return (
    <div className={containerClassName}>
      <div className={layoutClassName}>
        <FieldShell dense={dense} htmlFor={`${idPrefix}-preset`} label="Preset">
          <SearchableSelect
            id={`${idPrefix}-preset`}
            value={presetKey}
            onChange={onPresetChange}
            options={presetOptions}
            placeholder="Search preset…"
          />
        </FieldShell>
        {showTradingView ? (
          <Button
            type="button"
            size="sm"
            variant="secondary"
            icon={<ChartLineIcon />}
            disabled={!tvSymbol}
            title={
              tvSymbol
                ? `Chart ${presetOption?.label ?? tvSymbol} on TradingView`
                : "Select a preset first"
            }
            aria-label="Open TradingView chart"
            onClick={() => {
              if (!tvSymbol) return;
              openTradingViewChartWindow(tvSymbol);
            }}
            className="shrink-0"
          >
            TV
          </Button>
        ) : null}
        <FieldShell
          dense={dense}
          htmlFor={`${idPrefix}-underlying`}
          label="Underlying"
        >
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
        </FieldShell>
        <FieldShell dense={dense} htmlFor={`${idPrefix}-fut`} label={futLabel}>
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
        </FieldShell>
        <FieldShell
          dense={dense}
          htmlFor={`${idPrefix}-strike-step`}
          label="Strike step"
          className={dense ? "max-w-[8.5rem] shrink-0 flex-none" : undefined}
        >
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
        </FieldShell>
        {layoutExtras}
        {dense && atmHint != null ? (
          <span className="shrink-0 text-xs tabular-nums text-slate-muted">
            ATM {atmHint}
          </span>
        ) : null}
      </div>

      {extras}

      {!dense && (footer ?? atmHint != null) ? (
        <div className="mt-2 flex items-end gap-3">
          {footer}
          {atmHint != null ? (
            <span className="text-xs text-slate-muted">Live ATM hint: {atmHint}</span>
          ) : null}
        </div>
      ) : null}
      {dense && footer ? <div className="mt-1">{footer}</div> : null}
    </div>
  );
}
