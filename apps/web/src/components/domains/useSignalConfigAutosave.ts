"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { CUSTOM_PRESET } from "@/components/domains/signal-config-constants";
import {
  mergeResolvedOptionSymbols,
  signalConfigSnapshot,
} from "@/components/domains/signal-resolved-options";
import { suggestFutSymbol } from "@/components/domains/signal-setup-options";
import {
  getSignalConfig,
  patchSignalConfig,
  type SignalEngineAdminConfig,
  type SignalUnderlyingPreset,
} from "@/lib/api/admin";

const SAVE_DEBOUNCE_MS = 650;

export type SignalConfigSaveStatus = "idle" | "pending" | "saving" | "saved" | "error";

/** Fields autosave may PATCH. Never include engine_enabled — Start/Stop only. */
function diffConfigPatch(
  previousJson: string,
  next: SignalEngineAdminConfig,
): Partial<SignalEngineAdminConfig> {
  let previous: Partial<SignalEngineAdminConfig> = {};
  try {
    previous = JSON.parse(previousJson || "{}") as Partial<SignalEngineAdminConfig>;
  } catch {
    previous = {};
  }
  const patch: Partial<SignalEngineAdminConfig> = {};
  const keys = Object.keys(next) as (keyof SignalEngineAdminConfig)[];
  for (const key of keys) {
    if (key === "engine_enabled") continue;
    const a = JSON.stringify(previous[key] ?? null);
    const b = JSON.stringify(next[key] ?? null);
    if (a !== b) {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      (patch as any)[key] = next[key];
    }
  }
  return patch;
}

export function useSignalConfigAutosave(
  getAccessToken: () => Promise<string | null>,
  enabled: boolean,
) {
  const [presets, setPresets] = useState<SignalUnderlyingPreset[]>([]);
  const [presetKey, setPresetKey] = useState(CUSTOM_PRESET);
  const [config, setConfig] = useState<SignalEngineAdminConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [saveStatus, setSaveStatus] = useState<SignalConfigSaveStatus>("idle");
  const [error, setError] = useState<string | null>(null);

  const lastSavedRef = useRef("");
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const saveSeqRef = useRef(0);
  /** Bumps when an immediate Start/Stop fires so in-flight autosaves abort. */
  const immediateEpochRef = useRef(0);

  const persist = useCallback(
    async (next: SignalEngineAdminConfig, epoch: number) => {
      const snapshot = signalConfigSnapshot(next);
      if (snapshot === lastSavedRef.current) return;

      const patch = diffConfigPatch(lastSavedRef.current, next);
      if (Object.keys(patch).length === 0) {
        lastSavedRef.current = snapshot;
        return;
      }

      const seq = ++saveSeqRef.current;
      setSaveStatus("saving");
      setError(null);
      try {
        const token = await getAccessToken();
        if (!token) return;
        if (epoch !== immediateEpochRef.current) return;
        const result = await patchSignalConfig(token, patch);
        if (seq !== saveSeqRef.current) return;
        if (epoch !== immediateEpochRef.current) return;
        // Server may sanitize (drop mismatched CE/PE, mirror FUT). Prefer that.
        if (result?.config) {
          setConfig(result.config);
          lastSavedRef.current = signalConfigSnapshot(result.config);
          const match = presets.find(
            (p) => p.symbol === result.config.underlying_symbol,
          );
          setPresetKey(match ? match.symbol : CUSTOM_PRESET);
        } else {
          lastSavedRef.current = snapshot;
        }
        setSaveStatus("saved");
      } catch (err) {
        if (seq !== saveSeqRef.current) return;
        setSaveStatus("error");
        setError(err instanceof Error ? err.message : "Save failed");
      }
    },
    [getAccessToken, presets],
  );

  const scheduleSave = useCallback(
    (next: SignalEngineAdminConfig) => {
      if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
      const snapshot = signalConfigSnapshot(next);
      if (snapshot === lastSavedRef.current) {
        setSaveStatus("idle");
        return;
      }
      const epoch = immediateEpochRef.current;
      setSaveStatus("pending");
      saveTimerRef.current = setTimeout(() => {
        void persist(next, epoch);
      }, SAVE_DEBOUNCE_MS);
    },
    [persist],
  );

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const token = await getAccessToken();
      if (!token) return;
      const data = await getSignalConfig(token);
      setPresets(data.presets);
      setConfig(data.config);
      lastSavedRef.current = signalConfigSnapshot(data.config);
      const match = data.presets.find(
        (p) => p.symbol === data.config.underlying_symbol,
      );
      setPresetKey(match ? match.symbol : CUSTOM_PRESET);
      setSaveStatus("idle");
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load config");
    } finally {
      setLoading(false);
    }
  }, [getAccessToken]);

  useEffect(() => {
    if (!enabled) return;
    void load();
  }, [enabled, load]);

  useEffect(() => {
    if (!enabled || loading || config === null) return;
    scheduleSave(config);
  }, [config, enabled, loading, scheduleSave]);

  useEffect(() => {
    return () => {
      if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    };
  }, []);

  useEffect(() => {
    if (saveStatus !== "saved") return;
    const timer = setTimeout(() => setSaveStatus("idle"), 2000);
    return () => clearTimeout(timer);
  }, [saveStatus]);

  const patchConfig = useCallback((patch: Partial<SignalEngineAdminConfig>) => {
    setConfig((prev) => (prev ? { ...prev, ...patch } : prev));
  }, []);

  const patchConfigImmediate = useCallback(
    async (patch: Partial<SignalEngineAdminConfig>) => {
      if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
      immediateEpochRef.current += 1;
      const epoch = immediateEpochRef.current;
      let next: SignalEngineAdminConfig | null = null;
      setConfig((prev) => {
        if (!prev) return prev;
        next = { ...prev, ...patch };
        return next;
      });
      if (!next) return;
      const snapshot = signalConfigSnapshot(next);
      setSaveStatus("saving");
      setError(null);
      try {
        const token = await getAccessToken();
        if (!token) return;
        const result = await patchSignalConfig(token, patch);
        if (epoch !== immediateEpochRef.current) return;
        if (result?.config) {
          setConfig(result.config);
          lastSavedRef.current = signalConfigSnapshot(result.config);
          const match = presets.find(
            (p) => p.symbol === result.config.underlying_symbol,
          );
          setPresetKey(match ? match.symbol : CUSTOM_PRESET);
        } else {
          lastSavedRef.current = snapshot;
        }
        setSaveStatus("saved");
      } catch (err) {
        setSaveStatus("error");
        setError(err instanceof Error ? err.message : "Save failed");
        throw err;
      }
    },
    [getAccessToken, presets],
  );

  /**
   * Pull resolved CE/PE from the live SSE snapshot into the setup bar.
   * Caller must gate on matching underlying (stale NIFTY frames must not
   * refill a SENSEX setup). Auto-ATM persists on the server but the form
   * never reloads without this.
   */
  const syncResolvedOptions = useCallback(
    (resolved: { ce_symbol?: string | null; pe_symbol?: string | null }) => {
      if (saveStatus === "pending" || saveStatus === "saving") return;
      const ce = (resolved.ce_symbol || "").trim();
      const pe = (resolved.pe_symbol || "").trim();
      if (!ce && !pe) return;

      let merged: SignalEngineAdminConfig | null = null;
      setConfig((prev) => {
        if (!prev) return prev;
        merged = mergeResolvedOptionSymbols(prev, resolved, lastSavedRef.current);
        return merged ?? prev;
      });
      // Keep lastSaved outside the updater (React may invoke updaters twice).
      if (merged) {
        lastSavedRef.current = signalConfigSnapshot(merged);
      }
    },
    [saveStatus],
  );

  const onPresetChange = useCallback(
    (value: string) => {
      setPresetKey(value);
      if (value === CUSTOM_PRESET) return;
      const preset = presets.find((p) => p.symbol === value);
      if (!preset) return;
      const fut =
        preset.fut_symbol?.trim() || suggestFutSymbol(preset.symbol) || "";
      // Backend mirrors fut→nifty_fut and clears CE/PE when underlying/FUT changes.
      // Do not send nifty_fut_symbol / empty ce_symbol (breaks tsc + clearing guard).
      patchConfig({
        underlying_symbol: preset.symbol,
        underlying_label: preset.label,
        strike_step: preset.strike_step,
        fut_symbol: fut,
      });
    },
    [patchConfig, presets],
  );

  /** Options Lab–style: apply screener (or chain) pick into Signal setup fields. */
  const applyInstrumentSelection = useCallback(
    (selection: {
      underlying_symbol: string;
      underlying_label: string;
      fut_symbol?: string;
      strike_step?: number;
      ce_symbol?: string;
      pe_symbol?: string;
      clearOptions?: boolean;
    }) => {
      const match = presets.find((p) => p.symbol === selection.underlying_symbol);
      const strike =
        selection.strike_step ?? match?.strike_step ?? 50;
      // Equity screener picks are often absent from the initial index-only list —
      // upsert so PRESET shows "ASIANPAINT" instead of "Custom…".
      setPresets((prev) => {
        if (prev.some((p) => p.symbol === selection.underlying_symbol)) {
          return prev;
        }
        return [
          ...prev,
          {
            label: selection.underlying_label || selection.underlying_symbol,
            symbol: selection.underlying_symbol,
            strike_step: strike,
            fut_symbol: selection.fut_symbol,
            universe: "equities",
          },
        ];
      });
      setPresetKey(selection.underlying_symbol);
      const fut =
        selection.fut_symbol?.trim() ||
        match?.fut_symbol?.trim() ||
        suggestFutSymbol(selection.underlying_symbol) ||
        "";
      const clearOptions = selection.clearOptions !== false;
      const ce = selection.ce_symbol?.trim() || "";
      const pe = selection.pe_symbol?.trim() || "";
      patchConfig({
        underlying_symbol: selection.underlying_symbol,
        underlying_label:
          selection.underlying_label ||
          match?.label ||
          selection.underlying_symbol,
        strike_step: strike,
        ...(fut ? { fut_symbol: fut } : { fut_symbol: "" }),
        ...(clearOptions && !ce && !pe
          ? { ce_symbol: "", pe_symbol: "" }
          : {
              ...(ce ? { ce_symbol: ce } : clearOptions ? { ce_symbol: "" } : {}),
              ...(pe ? { pe_symbol: pe } : clearOptions ? { pe_symbol: "" } : {}),
            }),
      });
    },
    [patchConfig, presets],
  );

  return {
    config,
    presets,
    presetKey,
    presetLocked: presetKey !== CUSTOM_PRESET,
    loading,
    saveStatus,
    error,
    patchConfig,
    patchConfigImmediate,
    onPresetChange,
    applyInstrumentSelection,
    syncResolvedOptions,
  };
}
