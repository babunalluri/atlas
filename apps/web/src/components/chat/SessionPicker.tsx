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
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center justify-between px-1">
        <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-slate-muted">
          Sessions
        </p>
        <button
          type="button"
          onClick={onCreate}
          className="rounded-md px-1.5 py-0.5 text-xs font-medium text-[var(--tenant-accent)] transition hover:bg-fog/60"
        >
          + New
        </button>
      </div>
      <ul className="mt-3 min-h-0 flex-1 space-y-1 overflow-y-auto pr-0.5">
        {sessions.map((session) => (
          <li key={session.id}>
            <div
              className={cn(
                "group flex items-center rounded-xl text-sm transition",
                activeId === session.id
                  ? "bg-fog text-ink ring-1 ring-line"
                  : "text-slate-muted hover:bg-fog/70 hover:text-ink",
              )}
            >
              <button
                type="button"
                onClick={() => onSelect(session.id)}
                className="min-w-0 flex-1 px-3 py-2.5 text-left"
              >
                <span className="block truncate font-medium">
                  {session.title}
                </span>
                <span className="mt-0.5 block text-[10px] uppercase tracking-[0.1em] text-slate-muted">
                  {session.status}
                </span>
              </button>
              <button
                type="button"
                aria-label={`Delete ${session.title}`}
                onClick={() => onDelete(session.id)}
                className="mr-2 hidden size-6 items-center justify-center rounded-md text-slate-muted hover:bg-raised hover:text-ink group-hover:inline-flex"
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
