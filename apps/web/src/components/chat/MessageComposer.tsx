"use client";

import { useEffect, useState } from "react";

import { Button } from "@/components/ui/Button";
import { SendIcon, StopIcon } from "@/components/ui/icons";
import { cn } from "@/lib/utils";

export function MessageComposer({
  onSend,
  onCancel,
  disabled,
  streaming,
  placeholder = "Message…",
  dark = false,
  compact = false,
  externalDraft = null,
  onExternalDraftApplied,
}: {
  onSend: (text: string) => void | Promise<void>;
  onCancel?: () => void;
  disabled?: boolean;
  streaming?: boolean;
  placeholder?: string;
  dark?: boolean;
  compact?: boolean;
  externalDraft?: string | null;
  onExternalDraftApplied?: () => void;
}) {
  const [value, setValue] = useState("");

  useEffect(() => {
    if (!externalDraft?.trim()) return;
    setValue(externalDraft);
    onExternalDraftApplied?.();
    // Only react when a new draft arrives from outside the composer.
    // eslint-disable-next-line react-hooks/exhaustive-deps -- callback is stable from parent
  }, [externalDraft]);

  async function submit() {
    const text = value.trim();
    if (!text || disabled) return;
    setValue("");
    await onSend(text);
  }

  return (
    <div
      className={cn(
        "shrink-0 border-t",
        compact ? "px-3 py-2" : "px-5 py-4",
        dark ? "border-white/10 bg-black/20" : "border-line bg-raised/60",
      )}
    >
      <div className={cn("mx-auto flex gap-2", compact ? "max-w-none" : "max-w-3xl gap-3")}>
        <textarea
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder={placeholder}
          rows={compact ? 1 : 2}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void submit();
            }
          }}
          className={cn(
            "flex-1 resize-none rounded-xl border px-3.5 py-2.5 text-sm outline-none focus:ring-2",
            dark
              ? "border-white/15 bg-white/5 text-white placeholder:text-white/35 focus:ring-[var(--tenant-accent)]/35"
              : "border-line bg-raised text-ink focus:ring-teal/25",
          )}
        />
        <div className="flex flex-col justify-end gap-2">
          {streaming ? (
            <Button variant="secondary" icon={<StopIcon />} onClick={onCancel}>
              Stop
            </Button>
          ) : (
            <Button
              variant={dark ? "accent" : "primary"}
              icon={<SendIcon />}
              disabled={disabled || !value.trim()}
              onClick={() => void submit()}
            >
              Send
            </Button>
          )}
        </div>
      </div>
      {!compact ? (
        <p
          className={cn(
            "mx-auto mt-2 max-w-3xl text-[11px]",
            dark ? "text-white/35" : "text-slate-muted",
          )}
        >
          Enter to send · Shift+Enter for a new line
        </p>
      ) : null}
    </div>
  );
}
