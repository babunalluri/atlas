"use client";

import Link from "next/link";
import { useMemo, useState, type CSSProperties } from "react";

import {
  activityChannelLabel,
  activityStatusTone,
  activityTargetTypeLabel,
  formatActivityTime,
} from "@/lib/activities";
import type { ActivityChannel, ActivityRow } from "@/lib/api/types";
import { Badge } from "@/components/ui/Badge";
import { Input, Select } from "@/components/ui/Field";

type TabKey = "live_chat" | "scheduled" | "api" | "email" | "all";

const TABS: Array<{ key: TabKey; label: string; empty: string }> = [
  {
    key: "live_chat",
    label: "Chat",
    empty:
      "No chat sessions yet. Conversations from admin and public chat appear here.",
  },
  {
    key: "scheduled",
    label: "Schedules",
    empty: "No schedule runs yet. Trigger a schedule to see run history here.",
  },
  {
    key: "api",
    label: "Public API",
    empty:
      "No Public API or service-account runs yet. Calls with PAT tokens show up here.",
  },
  {
    key: "email",
    label: "Email",
    empty: "Email channel is not connected in Atlas yet.",
  },
  {
    key: "all",
    label: "All",
    empty: "No traces recorded for this tenant yet.",
  },
];

function matchesChannel(row: ActivityRow, tab: TabKey) {
  if (tab === "all") return true;
  return row.channel === (tab as ActivityChannel);
}

function shortUser(userId: string) {
  if (userId.startsWith("sa:")) return `API ${userId.slice(3, 11)}`;
  if (userId.length > 18) return `${userId.slice(0, 16)}…`;
  return userId;
}

export function TracesList({
  initialActivities,
}: {
  initialActivities: ActivityRow[];
}) {
  const [tab, setTab] = useState<TabKey>("live_chat");
  const [user, setUser] = useState("all");
  const [target, setTarget] = useState("all");
  const [slug, setSlug] = useState("all");
  const [status, setStatus] = useState("all");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");

  const users = useMemo(
    () =>
      [...new Set(initialActivities.map((row) => row.userId))].sort((a, b) =>
        a.localeCompare(b),
      ),
    [initialActivities],
  );
  const targets = useMemo(
    () =>
      [...new Set(initialActivities.map((row) => row.personaName))].sort((a, b) =>
        a.localeCompare(b),
      ),
    [initialActivities],
  );
  const slugs = useMemo(
    () =>
      [...new Set(initialActivities.map((row) => row.taskName))].sort((a, b) =>
        a.localeCompare(b),
      ),
    [initialActivities],
  );

  const filtered = useMemo(() => {
    const startMs = startDate ? new Date(`${startDate}T00:00:00`).getTime() : null;
    const endMs = endDate ? new Date(`${endDate}T23:59:59.999`).getTime() : null;
    return initialActivities.filter((row) => {
      if (!matchesChannel(row, tab)) return false;
      if (user !== "all" && row.userId !== user) return false;
      if (target !== "all" && row.personaName !== target) return false;
      if (slug !== "all" && row.taskName !== slug) return false;
      if (status !== "all" && row.status !== status) return false;
      const when = new Date(row.updatedAt).getTime();
      if (startMs != null && when < startMs) return false;
      if (endMs != null && when > endMs) return false;
      return true;
    });
  }, [initialActivities, tab, user, target, slug, status, startDate, endDate]);

  const counts = useMemo(() => {
    const total = filtered.length;
    const completed = filtered.filter((row) => row.status === "completed").length;
    const running = filtered.filter(
      (row) =>
        row.status === "running" ||
        row.status === "paused" ||
        row.status === "active",
    ).length;
    const error = filtered.filter(
      (row) => row.status === "error" || row.status === "cancelled",
    ).length;
    return { total, completed, running, error };
  }, [filtered]);

  const activeTab = TABS.find((item) => item.key === tab) ?? TABS[0];
  const timeZoneHint = formatActivityTime(new Date().toISOString()).zone;

  return (
    <div className="space-y-6">
      <header>
        <p className="text-xs font-semibold uppercase tracking-[0.14em] text-teal">
          Monitor
        </p>
        <h1 className="font-display text-4xl font-semibold tracking-tight">
          Traces
        </h1>
        <p className="mt-2 max-w-2xl text-sm text-slate-muted">
          Session history across chat, schedules, and the Public API — open a
          row for the conversation, status, and clear error details.
        </p>
      </header>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {(
          [
            ["Total", counts.total, "var(--tone-accent)"],
            ["Completed", counts.completed, "var(--tone-accent-dim)"],
            ["Running", counts.running, "var(--tone-warning)"],
            ["Error", counts.error, "var(--tone-danger)"],
          ] as const
        ).map(([label, value, edge]) => (
          <div
            key={label}
            className="kpi-card px-4 py-3"
            style={{ "--kpi-edge": edge } as CSSProperties}
          >
            <p className="th-label flex items-center gap-1.5">
              {label === "Running" && counts.running > 0 ? (
                <span className="live-dot text-amber" aria-hidden />
              ) : null}
              {label}
            </p>
            <p className="mono-cell mt-1 text-2xl font-semibold text-ink">
              {value}
            </p>
          </div>
        ))}
      </div>

      <div className="flex flex-wrap gap-1 border-b border-line pb-px">
        {TABS.map((item) => {
          const count = initialActivities.filter((row) =>
            matchesChannel(row, item.key),
          ).length;
          const selected = tab === item.key;
          return (
            <button
              key={item.key}
              type="button"
              onClick={() => setTab(item.key)}
              className={
                selected
                  ? "-mb-px border-b-2 border-ink px-3 py-2 text-sm font-semibold text-ink"
                  : "-mb-px border-b-2 border-transparent px-3 py-2 text-sm text-slate-muted hover:text-ink"
              }
            >
              {item.label}
              <span className="ml-1.5 font-mono text-xs text-slate-muted">
                {count}
              </span>
            </button>
          );
        })}
      </div>

      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-6">
        <Select
          aria-label="Filter by user"
          value={user}
          onChange={(event) => setUser(event.target.value)}
        >
          <option value="all">All users</option>
          {users.map((id) => (
            <option key={id} value={id}>
              {shortUser(id)}
            </option>
          ))}
        </Select>
        <Select
          aria-label="Filter by target"
          value={target}
          onChange={(event) => setTarget(event.target.value)}
        >
          <option value="all">All targets</option>
          {targets.map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </Select>
        <Select
          aria-label="Filter by slug"
          value={slug}
          onChange={(event) => setSlug(event.target.value)}
        >
          <option value="all">All slugs</option>
          {slugs.map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </Select>
        <Select
          aria-label="Filter by status"
          value={status}
          onChange={(event) => setStatus(event.target.value)}
        >
          <option value="all">All statuses</option>
          <option value="completed">Completed</option>
          <option value="running">Running</option>
          <option value="active">Active</option>
          <option value="paused">Paused</option>
          <option value="error">Error</option>
          <option value="cancelled">Cancelled</option>
        </Select>
        <Input
          type="date"
          aria-label="Start date"
          value={startDate}
          onChange={(event) => setStartDate(event.target.value)}
        />
        <Input
          type="date"
          aria-label="End date"
          value={endDate}
          onChange={(event) => setEndDate(event.target.value)}
        />
      </div>

      <section className="table-shell overflow-x-auto rounded-xl">
        <div className="min-w-[900px]">
          <div className="grid grid-cols-[minmax(120px,0.9fr)_minmax(140px,0.9fr)_minmax(180px,1.4fr)_minmax(120px,0.9fr)_minmax(100px,0.7fr)_90px] gap-3 border-b border-line px-4 py-2.5">
            <span className="th-label">User</span>
            <span className="th-label">Time ({timeZoneHint})</span>
            <span className="th-label">Session</span>
            <span className="th-label">Target</span>
            <span className="th-label">Slug</span>
            <span className="th-label text-right">Status</span>
          </div>
          {filtered.map((row) => {
            const time = formatActivityTime(row.updatedAt);
            return (
              <Link
                key={row.id}
                href={`/admin/traces/${encodeURIComponent(row.id)}`}
                className="grid grid-cols-[minmax(120px,0.9fr)_minmax(140px,0.9fr)_minmax(180px,1.4fr)_minmax(120px,0.9fr)_minmax(100px,0.7fr)_90px] items-center gap-3 border-b border-line/60 px-4 py-3 last:border-0 hover:bg-fog/40"
              >
                <p className="mono-cell truncate text-sm" title={row.userId}>
                  {shortUser(row.userId)}
                </p>
                <p
                  className="mono-cell text-xs text-slate-muted"
                  title={time.absolute}
                >
                  {time.absolute}
                </p>
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium">{row.title}</p>
                  <p className="mono-cell truncate text-[11px] text-slate-muted">
                    {activityChannelLabel(row.channel)}
                  </p>
                </div>
                <div className="min-w-0">
                  <p className="truncate text-sm">{row.personaName}</p>
                  <p className="mono-cell text-[11px] text-slate-muted">
                    {activityTargetTypeLabel(row.personaType)}
                  </p>
                </div>
                <p className="truncate text-sm text-slate-muted">{row.taskName}</p>
                <div className="flex justify-end">
                  <Badge
                    tone={activityStatusTone(row.status)}
                    live={row.status === "running" || row.status === "paused"}
                    dot={row.status !== "running" && row.status !== "paused"}
                  >
                    {row.status}
                  </Badge>
                </div>
              </Link>
            );
          })}
          {filtered.length === 0 ? (
            <p className="px-4 py-12 text-center text-sm text-slate-muted">
              {activeTab.empty}
            </p>
          ) : null}
        </div>
      </section>
    </div>
  );
}
