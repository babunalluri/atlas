/**
 * Shared desk instrument handoff across Signal Engine, Options Lab, and Param Chart.
 *
 * Same-tab: ``sessionStorage`` + ``CustomEvent``. Backend persists under
 * ``desk_instrument`` in signal tool settings on each desk config PATCH.
 */

export type DeskInstrumentSource =
  | "signal"
  | "options-lab"
  | "param-chart";

export type DeskInstrumentSelection = {
  underlying_symbol: string;
  underlying_label: string;
  fut_symbol?: string;
  strike_step?: number;
  /** ATM CE/PE when known from chain or Signal board. */
  ce_symbol?: string;
  pe_symbol?: string;
  /** Epoch ms — newest write wins when a desk applies. */
  updated_at_ms: number;
  source: DeskInstrumentSource;
};

const STORAGE_KEY = "atlas-desk-instrument";
const EVENT_NAME = "atlas-desk-instrument";

function canUseDom(): boolean {
  if (typeof window === "undefined") return false;
  // Some privacy modes throw when *reading* sessionStorage, not only on
  // getItem/setItem — probe inside try/catch.
  try {
    return typeof sessionStorage !== "undefined";
  } catch {
    return false;
  }
}

export function readDeskInstrument(): DeskInstrumentSelection | null {
  if (!canUseDom()) return null;
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as DeskInstrumentSelection;
    if (!parsed?.underlying_symbol?.trim()) return null;
    return parsed;
  } catch {
    return null;
  }
}

export function publishDeskInstrument(
  partial: Omit<DeskInstrumentSelection, "updated_at_ms" | "source"> & {
    source?: DeskInstrumentSelection["source"];
  },
): DeskInstrumentSelection | null {
  if (!canUseDom()) return null;
  const underlying = partial.underlying_symbol?.trim();
  if (!underlying) return null;

  const next: DeskInstrumentSelection = {
    underlying_symbol: underlying,
    underlying_label:
      partial.underlying_label?.trim() || underlying,
    fut_symbol: partial.fut_symbol?.trim() || undefined,
    strike_step:
      partial.strike_step && partial.strike_step > 0
        ? partial.strike_step
        : undefined,
    ce_symbol: partial.ce_symbol?.trim() || undefined,
    pe_symbol: partial.pe_symbol?.trim() || undefined,
    updated_at_ms: Date.now(),
    source: partial.source ?? "options-lab",
  };

  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  } catch {
    // private mode / quota
  }
  window.dispatchEvent(
    new CustomEvent<DeskInstrumentSelection>(EVENT_NAME, { detail: next }),
  );
  return next;
}

export function subscribeDeskInstrument(
  listener: (selection: DeskInstrumentSelection) => void,
): () => void {
  if (!canUseDom()) return () => undefined;

  const onEvent = (event: Event) => {
    const detail = (event as CustomEvent<DeskInstrumentSelection>).detail;
    if (detail?.underlying_symbol) listener(detail);
  };

  window.addEventListener(EVENT_NAME, onEvent);
  return () => {
    window.removeEventListener(EVENT_NAME, onEvent);
  };
}

/** Fingerprint used to skip no-op Signal applies. */
export function deskInstrumentKey(selection: DeskInstrumentSelection): string {
  return [
    selection.underlying_symbol,
    selection.fut_symbol ?? "",
    selection.strike_step ?? "",
    selection.ce_symbol ?? "",
    selection.pe_symbol ?? "",
  ].join("|");
}

export type DeskInstrumentIdentity = Pick<
  DeskInstrumentSelection,
  "underlying_symbol" | "fut_symbol" | "strike_step"
>;

/** Identity-only fingerprint — Param Chart must not react to live chain CE/PE rolls. */
export function deskInstrumentIdentityKey(
  selection: DeskInstrumentIdentity & {
    ce_symbol?: string;
    pe_symbol?: string;
  },
): string {
  return [
    selection.underlying_symbol,
    selection.fut_symbol ?? "",
    selection.strike_step ?? "",
  ].join("|");
}

/** Test helper. */
export function resetDeskInstrumentForTests(): void {
  try {
    if (typeof sessionStorage === "undefined") return;
    sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    // ignore
  }
}
