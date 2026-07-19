"use client";

import { useState, type CSSProperties } from "react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import {
  deleteAdminSession,
  deleteUserMemory,
} from "@/lib/api/admin";
import type { AdminSession, UserMemory } from "@/lib/api/types";
import { useAgentOsToken } from "@/lib/auth/token";
import { formatRelative } from "@/lib/utils";

export function SessionsPanel({
  initialSessions,
  initialMemories,
}: {
  initialSessions: AdminSession[];
  initialMemories: UserMemory[];
}) {
  const [sessions, setSessions] = useState(initialSessions);
  const [memories, setMemories] = useState(initialMemories);
  const [error, setError] = useState<string | null>(null);
  const { getAccessToken } = useAgentOsToken();

  async function forgetSession(id: string) {
    try {
      await deleteAdminSession(await getAccessToken(), id);
      setSessions((items) => items.filter((item) => item.id !== id));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Delete failed");
    }
  }

  async function forgetMemory(memory: UserMemory) {
    try {
      await deleteUserMemory(
        await getAccessToken(),
        memory.id,
        memory.userId,
      );
      setMemories((items) => items.filter((item) => item.id !== memory.id));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Forget failed");
    }
  }

  const activeCount = sessions.filter(
    (session) => session.status === "running" || session.status === "paused",
  ).length;

  return (
    <div className="space-y-6">
      <header>
        <p className="text-xs font-semibold uppercase tracking-[0.14em] text-teal">
          Durable state
        </p>
        <h1 className="font-display text-4xl font-semibold tracking-tight">
          Sessions & memories
        </h1>
        <p className="mt-2 text-sm text-slate-muted">
          Tenant-filtered AgentOS conversations and persistent user memories.
        </p>
        {error ? <p className="mt-2 text-sm text-rose">{error}</p> : null}
      </header>

      <div className="grid gap-3 sm:grid-cols-3">
        <div
          className="kpi-card px-4 py-3"
          style={{ "--kpi-edge": "var(--tone-accent)" } as CSSProperties}
        >
          <p className="th-label">Sessions</p>
          <p className="mono-cell mt-1 text-2xl font-semibold text-ink">
            {sessions.length}
          </p>
        </div>
        <div
          className="kpi-card px-4 py-3"
          style={{ "--kpi-edge": "var(--tone-warning)" } as CSSProperties}
        >
          <p className="th-label flex items-center gap-1.5">
            {activeCount > 0 ? (
              <span className="live-dot text-amber" aria-hidden />
            ) : null}
            In flight
          </p>
          <p className="mono-cell mt-1 text-2xl font-semibold text-ink">
            {activeCount}
          </p>
        </div>
        <div
          className="kpi-card px-4 py-3"
          style={{ "--kpi-edge": "var(--tone-info)" } as CSSProperties}
        >
          <p className="th-label">Memories</p>
          <p className="mono-cell mt-1 text-2xl font-semibold text-ink">
            {memories.length}
          </p>
        </div>
      </div>

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1.25fr)_minmax(320px,0.75fr)] xl:items-start">
        <section className="table-shell rounded-xl">
          <div className="flex items-center justify-between border-b border-line px-4 py-2.5">
            <span className="th-label">Conversations</span>
            <span className="th-label">Status</span>
          </div>
          <ul>
            {sessions.map((session) => (
              <li
                key={session.id}
                className="flex items-center justify-between gap-4 border-b border-line/60 px-4 py-2.5 last:border-0"
              >
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium">{session.title}</p>
                  <p className="mono-cell truncate text-slate-muted">
                    {session.userId} · {session.targetType} ·{" "}
                    {formatRelative(session.updatedAt)}
                  </p>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <Badge
                    dot
                    live={
                      session.status === "running" ||
                      session.status === "paused"
                    }
                    tone={
                      session.status === "error"
                        ? "danger"
                        : session.status === "paused"
                          ? "warning"
                          : session.status === "running"
                            ? "success"
                            : "neutral"
                    }
                  >
                    {session.status}
                  </Badge>
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => void forgetSession(session.id)}
                  >
                    Delete
                  </Button>
                </div>
              </li>
            ))}
            {sessions.length === 0 ? (
              <li className="px-4 py-8 text-center text-sm text-slate-muted">
                No durable sessions yet.
              </li>
            ) : null}
          </ul>
        </section>

        <section className="table-shell rounded-xl">
          <div className="border-b border-line px-4 py-2.5">
            <span className="th-label">User memories</span>
          </div>
          <ul>
            {memories.map((memory) => (
              <li
                key={memory.id}
                className="flex items-start justify-between gap-4 border-b border-line/60 px-4 py-2.5 last:border-0"
              >
                <div className="min-w-0">
                  <p className="text-sm leading-relaxed">{memory.memory}</p>
                  <p className="mono-cell mt-1 truncate text-slate-muted">
                    {memory.userId}
                  </p>
                </div>
                <Button
                  size="sm"
                  variant="danger"
                  onClick={() => void forgetMemory(memory)}
                >
                  Forget
                </Button>
              </li>
            ))}
            {memories.length === 0 ? (
              <li className="px-4 py-8 text-center text-sm text-slate-muted">
                No persistent memories yet.
              </li>
            ) : null}
          </ul>
        </section>
      </div>
    </div>
  );
}
