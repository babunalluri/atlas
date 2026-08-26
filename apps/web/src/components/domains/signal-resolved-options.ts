import type { SignalEngineAdminConfig } from "@/lib/api/admin";

/** Serialize config for dirty / last-saved comparisons. */
export function signalConfigSnapshot(config: SignalEngineAdminConfig): string {
  return JSON.stringify(config);
}

/**
 * Merge live SSE CE/PE into the setup-bar config.
 *
 * Caller must already ensure the SSE frame's underlying matches config
 * (stale NIFTY frames must not refill a SENSEX setup).
 */
export function mergeResolvedOptionSymbols(
  prev: SignalEngineAdminConfig,
  resolved: { ce_symbol?: string | null; pe_symbol?: string | null },
  lastSavedJson: string,
): SignalEngineAdminConfig | null {
  const ce = (resolved.ce_symbol || "").trim();
  const pe = (resolved.pe_symbol || "").trim();
  if (!ce && !pe) return null;

  const localCe = (prev.ce_symbol || "").trim();
  const localPe = (prev.pe_symbol || "").trim();
  const dirty = signalConfigSnapshot(prev) !== lastSavedJson;
  const next = { ...prev };
  let changed = false;

  // Fill empty slots from live ATM.
  if (ce && !localCe) {
    next.ce_symbol = ce;
    changed = true;
  }
  if (pe && !localPe) {
    next.pe_symbol = pe;
    changed = true;
  }
  // ATM roll: both sides already set, server moved, and local is clean.
  if (
    !dirty &&
    ce &&
    pe &&
    localCe &&
    localPe &&
    (ce !== localCe || pe !== localPe)
  ) {
    next.ce_symbol = ce;
    next.pe_symbol = pe;
    changed = true;
  }
  return changed ? next : null;
}
