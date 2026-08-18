"use client";

import { Input, Label, Select } from "@/components/ui/Field";
import type { SignalEngineAdminConfig } from "@/lib/api/admin";
import { CUSTOM_PRESET } from "@/components/domains/useSignalConfigAutosave";

export function SignalSetupBar({
  config,
  presets,
  presetKey,
  presetLocked,
  onPresetChange,
  patchConfig,
  loading,
}: {
  config: SignalEngineAdminConfig | null;
  presets: Array<{ label: string; symbol: string; strike_step: number }>;
  presetKey: string;
  presetLocked: boolean;
  onPresetChange: (value: string) => void;
  patchConfig: (patch: Partial<SignalEngineAdminConfig>) => void;
  loading: boolean;
}) {
  if (loading || !config) {
    return <p className="text-sm text-slate-muted">Loading symbols…</p>;
  }

  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-4">
      <div>
        <Label htmlFor="signal-preset">Preset</Label>
        <Select
          id="signal-preset"
          value={presetKey}
          onChange={(e) => onPresetChange(e.target.value)}
        >
          {presets.map((p) => (
            <option key={p.symbol} value={p.symbol}>
              {p.label}
            </option>
          ))}
          <option value={CUSTOM_PRESET}>Custom…</option>
        </Select>
      </div>
      <div className="sm:col-span-2">
        <Label htmlFor="signal-underlying">Underlying</Label>
        <Input
          id="signal-underlying"
          className="tnum"
          value={config.underlying_symbol}
          placeholder="NSE:NIFTY 50"
          readOnly={presetLocked}
          disabled={presetLocked}
          onChange={(e) =>
            patchConfig({
              underlying_symbol: e.target.value,
              underlying_label: config.underlying_label || e.target.value,
            })
          }
        />
      </div>
      <div>
        <Label htmlFor="signal-strike">Strike step</Label>
        <Input
          id="signal-strike"
          type="number"
          className="tnum"
          value={config.strike_step ?? 50}
          onChange={(e) =>
            patchConfig({ strike_step: Number(e.target.value) || 50 })
          }
        />
      </div>
      <div className="sm:col-span-2">
        <Label htmlFor="signal-fut">FUT (current expiry)</Label>
        <Input
          id="signal-fut"
          className="tnum"
          value={config.fut_symbol ?? ""}
          placeholder="NFO:NIFTY26AUGFUT"
          onChange={(e) => patchConfig({ fut_symbol: e.target.value })}
        />
      </div>
      <div>
        <Label htmlFor="signal-ce">CE symbol</Label>
        <Input
          id="signal-ce"
          className="tnum"
          value={config.ce_symbol ?? ""}
          placeholder="NFO:…CE…"
          onChange={(e) => patchConfig({ ce_symbol: e.target.value })}
        />
      </div>
      <div>
        <Label htmlFor="signal-pe">PE symbol</Label>
        <Input
          id="signal-pe"
          className="tnum"
          value={config.pe_symbol ?? ""}
          placeholder="NFO:…PE…"
          onChange={(e) => patchConfig({ pe_symbol: e.target.value })}
        />
      </div>
    </div>
  );
}
