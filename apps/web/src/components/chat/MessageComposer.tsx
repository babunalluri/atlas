"use client";

import { useState } from "react";

import { Button } from "@/components/ui/Button";
import { cn } from "@/lib/utils";

export function MessageComposer({
  onSend,
  onCancel,
  disabled,
  streaming,
  placeholder = "Message…",
  dark = false,
}: {
  onSend: (text: string) => void | Promise<void>;
  onCancel?: () => void;
  disabled?: boolean;
  streaming?: boolean;
  placeholder?: string;
  dark?: boolean;
}) {
  const [value, setValue] = useState("");

  async function submit() {
    const text = value.trim();
    if (!text || disabled) return;
    setValue("");
    await onSend(text);
  }

  return (
    <div
      className={cn(
        "border-t p-3",
        dark ? "border-white/10" : "border-line bg-raised/60",
      )}
    >
      <div className="flex gap-2">
        <textarea
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder={placeholder}
          rows={2}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void submit();
            }
          }}
          className={cn(
            "flex-1 resize-none rounded-xl border px-3 py-2 text-sm outline-none focus:ring-2",
            dark
              ? "border-white/15 bg-white/5 text-ink placeholder:text-slate-muted focus:ring-teal-bright/30"
              : "border-line bg-raised text-ink focus:ring-teal/25",
          )}
        />
        <div className="flex flex-col gap-2">
          <Button
            variant={dark ? "accent" : "primary"}
            disabled={disabled || !value.trim()}
            onClick={() => void submit()}
          >
            Send
          </Button>
          {streaming ? (
            <Button variant="secondary" onClick={onCancel}>
              Stop
            </Button>
          ) : null}
        </div>
      </div>
    </div>
  );
}
