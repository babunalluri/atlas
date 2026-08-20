import type { SearchableSelectOption } from "@/components/ui/SearchableSelect";
import type {
  SignalEngineAdminConfig,
  SignalUnderlyingPreset,
} from "@/lib/api/admin";

import { CUSTOM_PRESET } from "@/components/domains/signal-config-constants";

const EXTRA_STRIKE_STEPS = [25, 50, 75, 100, 200];
const MONTH_CODES = [
  "JAN",
  "FEB",
  "MAR",
  "APR",
  "MAY",
  "JUN",
  "JUL",
  "AUG",
  "SEP",
  "OCT",
  "NOV",
  "DEC",
] as const;

const FUT_ROOT_BY_UNDERLYING: Record<string, { exchange: string; root: string }> =
  {
    "NSE:NIFTY 50": { exchange: "NFO", root: "NIFTY" },
    "NSE:NIFTY": { exchange: "NFO", root: "NIFTY" },
    "NSE:BANKNIFTY": { exchange: "NFO", root: "BANKNIFTY" },
    "NSE:FINNIFTY": { exchange: "NFO", root: "FINNIFTY" },
    "NSE:MIDCPNIFTY": { exchange: "NFO", root: "MIDCPNIFTY" },
    "BSE:SENSEX": { exchange: "BFO", root: "SENSEX" },
  };

function istCalendarParts(when: Date): { year: number; month: number; day: number } {
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Kolkata",
    year: "numeric",
    month: "numeric",
    day: "numeric",
  }).formatToParts(when);
  const pick = (type: string) =>
    Number(parts.find((part) => part.type === type)?.value ?? 0);
  return { year: pick("year"), month: pick("month"), day: pick("day") };
}

function lastThursdayOfMonth(year: number, month: number): number {
  for (let day = 31; day >= 1; day -= 1) {
    const probe = new Date(Date.UTC(year, month - 1, day, 12, 0, 0));
    const parts = istCalendarParts(probe);
    if (parts.year !== year || parts.month !== month) continue;
    const weekday = new Intl.DateTimeFormat("en-GB", {
      timeZone: "Asia/Kolkata",
      weekday: "short",
    }).format(probe);
    if (weekday === "Thu") return day;
  }
  return 1;
}

function activeFutMonth(when: Date): { year: number; month: number } {
  const { year, month, day } = istCalendarParts(when);
  if (day > lastThursdayOfMonth(year, month)) {
    let nextMonth = month + 1;
    let nextYear = year;
    if (nextMonth > 12) {
      nextMonth = 1;
      nextYear += 1;
    }
    return { year: nextYear, month: nextMonth };
  }
  return { year, month };
}

/** Monthly FUT symbol for NSE/BFO index underlyings (e.g. NFO:NIFTY26AUGFUT). */
export function suggestFutSymbol(
  underlyingSymbol: string,
  when: Date = new Date(),
): string {
  const meta = FUT_ROOT_BY_UNDERLYING[underlyingSymbol.trim()];
  if (!meta) return "";
  const { year, month } = activeFutMonth(when);
  const yy = String(year).slice(-2);
  const mon = MONTH_CODES[month - 1];
  return `${meta.exchange}:${meta.root}${yy}${mon}FUT`;
}

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
  config: Pick<SignalEngineAdminConfig, "underlying_symbol">,
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

export function buildFutOptions(
  futSymbol: string | null | undefined,
  underlyingSymbol?: string | null,
) {
  const values: string[] = [];
  const current = futSymbol?.trim();
  if (current) values.push(current);
  if (underlyingSymbol?.trim()) {
    const suggested = suggestFutSymbol(underlyingSymbol);
    if (suggested && suggested !== current) values.push(suggested);
  }
  return uniqueOptions(values);
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
