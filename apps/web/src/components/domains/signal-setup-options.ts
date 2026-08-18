import type { SearchableSelectOption } from "@/components/ui/SearchableSelect";
import type {
  SignalEngineAdminConfig,
  SignalUnderlyingPreset,
} from "@/lib/api/admin";

import { CUSTOM_PRESET } from "@/components/domains/signal-config-constants";

const EXTRA_STRIKE_STEPS = [25, 50, 75, 100, 200];

export function deriveOptionSymbol(
  futSymbol: string,
  strike: number,
  side: "CE" | "PE",
): string | null {
  if (strike <= 0) return null;
  const raw = futSymbol.trim();
  if (!raw) return null;
  const [exchange, symRaw] = raw.includes(":")
    ? raw.split(":", 2)
    : ["NFO", raw];
  const sym = symRaw.trim().toUpperCase();
  if (!sym.endsWith("FUT")) return null;
  const prefix = sym.slice(0, -3);
  if (!prefix) return null;
  return `${exchange.trim().toUpperCase()}:${prefix}${strike}${side}`;
}

function uniqueOptions(values: Iterable<string>): SearchableSelectOption[] {
  const seen = new Set<string>();
  const options: SearchableSelectOption[] = [];
  for (const value of values) {
    const trimmed = value.trim();
    if (!trimmed || seen.has(trimmed)) continue;
    seen.add(trimmed);
    options.push({ value: trimmed, label: trimmed });
  }
  return options;
}

export function buildPresetOptions(
  presets: SignalUnderlyingPreset[],
): SearchableSelectOption[] {
  return [
    ...presets.map((preset) => ({
      value: preset.symbol,
      label: preset.label,
    })),
    { value: CUSTOM_PRESET, label: "Custom…" },
  ];
}

export function buildUnderlyingOptions(
  presets: SignalUnderlyingPreset[],
  config: SignalEngineAdminConfig,
): SearchableSelectOption[] {
  const values = new Set<string>();
  const current = config.underlying_symbol.trim();
  if (current) values.add(current);
  for (const preset of presets) values.add(preset.symbol);
  return [...values].map((symbol) => {
    const preset = presets.find((item) => item.symbol === symbol);
    return {
      value: symbol,
      label: preset ? `${preset.label} — ${symbol}` : symbol,
    };
  });
}

export function buildStrikeStepOptions(
  presets: SignalUnderlyingPreset[],
  strikeStep: number | null | undefined,
): SearchableSelectOption[] {
  const values = [
    ...presets.map((preset) => String(preset.strike_step)),
    ...EXTRA_STRIKE_STEPS.map(String),
    strikeStep != null ? String(strikeStep) : "",
  ];
  return uniqueOptions(values);
}

export function buildFutOptions(futSymbol: string | null | undefined) {
  return uniqueOptions([futSymbol ?? ""]);
}

export function buildOptionSideOptions(
  futSymbol: string | null | undefined,
  side: "CE" | "PE",
  atm: number | null,
  strikeStep: number,
  current: string | null | undefined,
): SearchableSelectOption[] {
  const values = new Set<string>();
  if (current?.trim()) values.add(current.trim());
  const fut = futSymbol?.trim() ?? "";
  if (fut && atm != null && strikeStep > 0) {
    for (let offset = -4; offset <= 4; offset += 1) {
      const strike = atm + offset * strikeStep;
      const symbol = deriveOptionSymbol(fut, strike, side);
      if (symbol) values.add(symbol);
    }
  }
  return uniqueOptions(values);
}
