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
}: {
  targets: Array<{ id: string; name: string }>;
  selectedId?: string;
  onSelect: (id: string) => void;
}) {
  if (targets.length === 0) {
    return (
      <p className="mt-2 text-xs text-slate-muted">
        No desk chats assigned.
      </p>
    );
  }
  return (
    <div className="mt-2 flex flex-wrap gap-1.5">
      {targets.map((target) => (
        <button
          key={target.id}
          type="button"
          onClick={() => onSelect(target.id)}
          className={cn(
            "rounded-full border px-2.5 py-1 text-xs font-medium transition",
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
