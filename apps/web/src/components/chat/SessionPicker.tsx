"use client";

import type { ConversationSession } from "@/lib/api/types";
import { cn } from "@/lib/utils";

export function SessionPicker({
  sessions,
  activeId,
  onSelect,
  onCreate,
  onDelete,
}: {
  sessions: ConversationSession[];
  activeId?: string;
  onSelect: (id: string) => void;
  onCreate: () => void;
  onDelete: (id: string) => void;
}) {
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-white/60">
          Sessions
        </p>
        <button
          type="button"
          onClick={onCreate}
          className="text-xs font-medium text-[var(--tenant-accent)] hover:underline"
        >
          New
        </button>
      </div>
      <ul className="space-y-1">
        {sessions.map((session) => (
          <li key={session.id}>
            <div
              className={cn(
                "group flex items-center rounded-lg text-sm transition",
                activeId === session.id
                  ? "bg-white/15 text-white"
                  : "text-white/70 hover:bg-white/8 hover:text-white",
              )}
            >
              <button
                type="button"
                onClick={() => onSelect(session.id)}
                className="min-w-0 flex-1 px-2.5 py-2 text-left"
              >
                <span className="block truncate font-medium">{session.title}</span>
                <span className="block text-[11px] text-white/45">
                  {session.status}
                </span>
              </button>
              <button
                type="button"
                aria-label={`Delete ${session.title}`}
                onClick={() => onDelete(session.id)}
                className="mr-2 hidden text-xs text-white/45 hover:text-white group-hover:block"
              >
                ×
              </button>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
