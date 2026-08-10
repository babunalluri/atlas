"use client";

import { useEffect, useId, useState } from "react";

import { Button } from "@/components/ui/Button";
import { Input, Label } from "@/components/ui/Field";

const DEFAULT_PRESETS = [1_000, 5_000, 10_000, 50_000];

export function GrantCreditsForm({
  label,
  hint,
  balanceLabel,
  availableCredits,
  balanceLoading = false,
  defaultCredits = "5000",
  defaultDescription = "",
  presets = DEFAULT_PRESETS,
  busy = false,
  disabled = false,
  onGrant,
}: {
  label: string;
  hint?: string;
  balanceLabel?: string;
  availableCredits?: number | null;
  balanceLoading?: boolean;
  defaultCredits?: string;
  defaultDescription?: string;
  presets?: number[];
  busy?: boolean;
  disabled?: boolean;
  onGrant: (credits: number, description: string) => Promise<void>;
}) {
  const formId = useId().replace(/:/g, "");
  const [credits, setCredits] = useState(defaultCredits);
  const [description, setDescription] = useState(defaultDescription);
  const [error, setError] = useState<string | null>(null);
  const [justGranted, setJustGranted] = useState<number | null>(null);

  useEffect(() => {
    setDescription(defaultDescription);
  }, [defaultDescription]);

  async function submit() {
    if (disabled) {
      setError("Complete the selection above first");
      return;
    }
    const amount = Number.parseInt(credits, 10);
    if (!Number.isFinite(amount) || amount <= 0) {
      setError("Enter a positive credit amount");
      return;
    }
    setError(null);
    setJustGranted(null);
    try {
      await onGrant(amount, description.trim() || "Free credit grant");
      setJustGranted(amount);
      setCredits(defaultCredits);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Grant failed");
    }
  }

  return (
    <div className="space-y-3">
      {hint ? <p className="text-sm text-slate-muted">{hint}</p> : null}
      {balanceLabel ? (
        <p className="rounded-md border border-line bg-raised/60 px-3 py-2 text-sm">
          <span className="text-slate-muted">{balanceLabel}: </span>
          <span className="font-semibold tabular-nums text-ink">
            {balanceLoading
              ? "Loading…"
              : availableCredits != null
                ? availableCredits.toLocaleString()
                : "—"}
          </span>
          <span className="ml-1 text-xs text-slate-muted">credits available</span>
        </p>
      ) : null}
      {justGranted != null ? (
        <p className="rounded-md border border-teal/30 bg-teal/10 px-3 py-2 text-sm text-teal">
          Added {justGranted.toLocaleString()} credits successfully.
        </p>
      ) : null}
      {presets.length > 0 ? (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs font-medium text-slate-muted">Quick amounts:</span>
          {presets.map((preset) => (
            <button
              key={preset}
              type="button"
              disabled={busy || disabled}
              onClick={() => setCredits(String(preset))}
              className={`rounded-md border px-2.5 py-1 text-xs font-medium tabular-nums transition ${
                credits === String(preset)
                  ? "border-ink bg-ink text-canvas"
                  : "border-line bg-raised text-slate-muted hover:border-teal hover:text-ink"
              }`}
            >
              {preset.toLocaleString()}
            </button>
          ))}
        </div>
      ) : null}
      <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)_auto] sm:items-end">
        <div>
          <Label htmlFor={`${formId}-amount`}>Credits to add</Label>
          <Input
            id={`${formId}-amount`}
            value={credits}
            onChange={(event) => setCredits(event.target.value)}
            inputMode="numeric"
            disabled={busy || disabled}
            placeholder="e.g. 5000"
          />
        </div>
        <div>
          <Label htmlFor={`${formId}-note`} hint="shown in ledger">
            Note
          </Label>
          <Input
            id={`${formId}-note`}
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            disabled={busy || disabled}
            placeholder="e.g. Welcome bonus, support adjustment"
          />
        </div>
        <Button disabled={busy || disabled} onClick={() => void submit()}>
          {busy ? "Granting…" : label}
        </Button>
      </div>
      {error ? (
        <p className="text-sm text-rose">{error}</p>
      ) : null}
    </div>
  );
}
