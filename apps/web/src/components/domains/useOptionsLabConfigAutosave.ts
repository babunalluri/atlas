"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { CUSTOM_PRESET } from "@/components/domains/signal-config-constants";
import { suggestFutSymbol } from "@/components/domains/signal-setup-options";
import {
  getOptionsLabConfig,
  patchOptionsLabConfig,
  type OptionsLabAdminConfig,
  type SignalUnderlyingPreset,
} from "@/lib/api/admin";

const SAVE_DEBOUNCE_MS = 650;

export type OptionsLabSaveStatus = "idle" | "pending" | "saving" | "saved" | "error";

function configSnapshot(config: OptionsLabAdminConfig): string {
  return JSON.stringify(config);
}

function withSuggestedFut(config: OptionsLabAdminConfig): OptionsLabAdminConfig {
  if (config.fut_symbol?.trim() || !config.underlying_symbol?.trim()) {
    return config;
  }
  const suggested = suggestFutSymbol(config.underlying_symbol);
  if (!suggested) return config;
  return { ...config, fut_symbol: suggested };
}

export function useOptionsLabConfigAutosave(
  getAccessToken: () => Promise<string | null>,
  enabled: boolean,
) {
  const [presets, setPresets] = useState<SignalUnderlyingPreset[]>([]);
  const [presetKey, setPresetKey] = useState(CUSTOM_PRESET);
  const [config, setConfig] = useState<OptionsLabAdminConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [saveStatus, setSaveStatus] = useState<OptionsLabSaveStatus>("idle");
  const [error, setError] = useState<string | null>(null);

  const lastSavedRef = useRef("");
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const saveSeqRef = useRef(0);

  const persist = useCallback(
    async (next: OptionsLabAdminConfig) => {
      const snapshot = configSnapshot(next);
      if (snapshot === lastSavedRef.current) return;

      const seq = ++saveSeqRef.current;
      setSaveStatus("saving");
      setError(null);
      try {
        const token = await getAccessToken();
        if (!token) return;
        await patchOptionsLabConfig(token, next);
        if (seq !== saveSeqRef.current) return;
        lastSavedRef.current = snapshot;
        setSaveStatus("saved");
      } catch (err) {
        if (seq !== saveSeqRef.current) return;
        setSaveStatus("error");
        setError(err instanceof Error ? err.message : "Save failed");
      }
    },
    [getAccessToken],
  );

  const scheduleSave = useCallback(
    (next: OptionsLabAdminConfig) => {
      if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
      const snapshot = configSnapshot(next);
      if (snapshot === lastSavedRef.current) {
        setSaveStatus("idle");
        return;
      }
      setSaveStatus("pending");
      saveTimerRef.current = setTimeout(() => {
        void persist(next);
      }, SAVE_DEBOUNCE_MS);
    },
    [persist],
  );

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const token = await getAccessToken();
      if (!token) return;
      const data = await getOptionsLabConfig(token);
      if (!data.ok) {
        setError(data.error ?? "Failed to load Options Lab config");
        return;
      }
      const bootstrapped = withSuggestedFut(data.config);
      setPresets(data.presets);
      setConfig(bootstrapped);
      const match = data.presets.find(
        (p) => p.symbol === bootstrapped.underlying_symbol,
      );
      setPresetKey(match ? match.symbol : CUSTOM_PRESET);
      setError(null);
      if (configSnapshot(bootstrapped) !== configSnapshot(data.config)) {
        lastSavedRef.current = "";
        await persist(bootstrapped);
      } else {
        lastSavedRef.current = configSnapshot(data.config);
        setSaveStatus("idle");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load config");
    } finally {
      setLoading(false);
    }
  }, [getAccessToken, persist]);

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

  const patchConfig = useCallback(
    (patch: Partial<OptionsLabAdminConfig>) => {
      setConfig((prev) => {
        if (!prev) return prev;
        const next = { ...prev, ...patch };
        // Underlying change must refresh FUT — keep stale NIFTY FUT off RELIANCE etc.
        if (patch.underlying_symbol && patch.fut_symbol === undefined) {
          next.fut_symbol = suggestFutSymbol(next.underlying_symbol) || "";
        }
        return next;
      });
      if (patch.underlying_symbol !== undefined) {
        const match = presets.find((p) => p.symbol === patch.underlying_symbol);
        setPresetKey(match ? match.symbol : CUSTOM_PRESET);
      }
    },
    [presets],
  );

  const onPresetChange = useCallback(
    (value: string) => {
      setPresetKey(value);
      if (value === CUSTOM_PRESET) return;
      const preset = presets.find((p) => p.symbol === value);
      if (!preset) return;
      patchConfig({
        underlying_symbol: preset.symbol,
        underlying_label: preset.label,
        strike_step: preset.strike_step,
        fut_symbol: suggestFutSymbol(preset.symbol),
      });
    },
    [patchConfig, presets],
  );

  const configReady = useMemo(
    () =>
      !loading &&
      config !== null &&
      saveStatus !== "pending" &&
      saveStatus !== "saving",
    [config, loading, saveStatus],
  );

  return {
    config,
    presets,
    presetKey,
    presetLocked: presetKey !== CUSTOM_PRESET,
    loading,
    saveStatus,
    configReady,
    error,
    patchConfig,
    onPresetChange,
    reload: load,
  };
}
