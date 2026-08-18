"use client";

import { cn } from "@/lib/utils";

export function deskChatEmptyCopy(customer: boolean) {
  return customer
    ? "No desk chats are assigned to you yet. Ask your administrator to assign a team."
    : "No published teams to chat with yet. Assign a team, or provision the Stock Broker domain.";
}

export function DeskChatPills({
  targets,
  selectedId,
  onSelect,
  compact = false,
}: {
  targets: Array<{ id: string; name: string }>;
  selectedId?: string;
  onSelect: (id: string) => void;
  compact?: boolean;
}) {
  if (targets.length === 0) {
    return (
      <p className={cn("text-xs text-slate-muted", compact ? "" : "mt-2")}>
        No desk chats assigned.
      </p>
    );
  }
  return (
    <div
      className={cn(
        "flex flex-wrap gap-1",
        compact ? "" : "mt-2 gap-1.5",
      )}
    >
      {targets.map((target) => (
        <button
          key={target.id}
          type="button"
          onClick={() => onSelect(target.id)}
          className={cn(
            "rounded-full border font-medium transition",
            compact ? "px-2 py-0.5 text-[11px]" : "px-2.5 py-1 text-xs",
            target.id === selectedId
              ? "border-teal/40 bg-teal/10 text-teal"
              : "border-line text-slate-muted hover:border-teal/30 hover:text-ink",
          )}
        >
          {target.name}
        </button>
      ))}
    </div>
  );
}

export function DeskChatStarters({
  prompts,
  onSelect,
}: {
  prompts: string[];
  onSelect: (text: string) => void;
}) {
  if (!prompts.length) return null;
  return (
    <div className="shrink-0 border-t border-line bg-raised/40 px-3 py-2">
      <div className="flex gap-1.5 overflow-x-auto pb-0.5 [-ms-overflow-style:none] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
        {prompts.map((prompt) => (
          <button
            key={prompt}
            type="button"
            onClick={() => onSelect(prompt)}
            className="shrink-0 rounded-full border border-line bg-raised px-2.5 py-1 text-[11px] text-ink-soft transition hover:border-teal/35 hover:bg-teal/[0.06] hover:text-ink"
          >
            {prompt}
          </button>
        ))}
      </div>
    </div>
  );
}
