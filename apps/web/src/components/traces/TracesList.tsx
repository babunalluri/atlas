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

function targetFilterKey(row: ActivityRow) {
  return `${row.personaType}:${row.personaName}`;
}

function targetTypeBadgeTone(
  type: ActivityRow["personaType"],
): "info" | "success" | "neutral" {
  if (type === "team") return "info";
  if (type === "workflow") return "success";
  return "neutral";
}

export function TracesList({
  initialActivities,
}: {
  initialActivities: ActivityRow[];
}) {
  const [tab, setTab] = useState<TabKey>("live_chat");
  const [user, setUser] = useState("all");
  const [target, setTarget] = useState("all");
  const [status, setStatus] = useState("all");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");

  const users = useMemo(() => {
    const byId = new Map<string, string>();
    for (const row of initialActivities) {
      byId.set(row.userId, row.userLabel || shortUser(row.userId));
    }
    return [...byId.entries()].sort((a, b) => a[1].localeCompare(b[1]));
  }, [initialActivities]);
  const targets = useMemo(() => {
    const byKey = new Map<string, ActivityRow>();
    for (const row of initialActivities) {
      byKey.set(targetFilterKey(row), row);
    }
    return [...byKey.values()].sort((a, b) => {
      const byName = a.personaName.localeCompare(b.personaName);
      if (byName !== 0) return byName;
      return a.personaType.localeCompare(b.personaType);
    });
  }, [initialActivities]);

  const tabCounts = useMemo(() => {
    const counts = Object.fromEntries(
      TABS.map(({ key }) => [key, 0]),
    ) as Record<TabKey, number>;
    for (const row of initialActivities) {
      counts.all += 1;
      if (row.channel in counts) counts[row.channel] += 1;
    }
    return counts;
  }, [initialActivities]);

  const { filtered, counts } = useMemo(() => {
    const startMs = startDate ? new Date(`${startDate}T00:00:00`).getTime() : null;
    const endMs = endDate ? new Date(`${endDate}T23:59:59.999`).getTime() : null;
    const rows: ActivityRow[] = [];
    const nextCounts = { total: 0, completed: 0, running: 0, error: 0 };
    for (const row of initialActivities) {
      if (!matchesChannel(row, tab)) continue;
      if (user !== "all" && row.userId !== user) continue;
      if (target !== "all" && targetFilterKey(row) !== target) continue;
      if (status !== "all" && row.status !== status) continue;
      const when = new Date(row.updatedAt).getTime();
      if (startMs != null && when < startMs) continue;
      if (endMs != null && when > endMs) continue;
      rows.push(row);
      nextCounts.total += 1;
      if (row.status === "completed") nextCounts.completed += 1;
      if (
        row.status === "running" ||
        row.status === "paused" ||
        row.status === "active"
      ) {
        nextCounts.running += 1;
      }
      if (row.status === "error" || row.status === "cancelled") {
        nextCounts.error += 1;
      }
    }
    return { filtered: rows, counts: nextCounts };
  }, [initialActivities, tab, user, target, status, startDate, endDate]);

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
          const count = tabCounts[item.key];
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

      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-5">
        <Select
          aria-label="Filter by user"
          value={user}
          onChange={(event) => setUser(event.target.value)}
        >
          <option value="all">All users</option>
          {users.map(([id, label]) => (
            <option key={id} value={id}>
              {label}
            </option>
          ))}
        </Select>
        <Select
          aria-label="Filter by target"
          value={target}
          onChange={(event) => setTarget(event.target.value)}
        >
          <option value="all">All teams / workflows</option>
          {targets.map((row) => (
            <option key={targetFilterKey(row)} value={targetFilterKey(row)}>
              {row.personaName} ({activityTargetTypeLabel(row.personaType)})
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
        <div className="min-w-[820px]">
          <div className="grid grid-cols-[minmax(120px,0.9fr)_minmax(140px,0.9fr)_minmax(180px,1.4fr)_minmax(140px,1fr)_90px] gap-3 border-b border-line px-4 py-2.5">
            <span className="th-label">User</span>
            <span className="th-label">Time ({timeZoneHint})</span>
            <span className="th-label">Session</span>
            <span className="th-label">Target</span>
            <span className="th-label text-right">Status</span>
          </div>
          {filtered.map((row) => {
            const time = formatActivityTime(row.updatedAt);
            return (
              <Link
                key={row.id}
                href={`/admin/traces/${encodeURIComponent(row.id)}`}
                className="grid grid-cols-[minmax(120px,0.9fr)_minmax(140px,0.9fr)_minmax(180px,1.4fr)_minmax(140px,1fr)_90px] items-center gap-3 border-b border-line/60 px-4 py-3 last:border-0 hover:bg-fog/40"
              >
                <p className="truncate text-sm font-medium" title={row.userId}>
                  {row.userLabel || shortUser(row.userId)}
                </p>
                <p
                  className="mono-cell text-xs text-slate-muted"
                  title={time.absolute}
                >
                  {time.absolute}
                </p>
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium">{row.title}</p>
                  <p className="truncate text-[11px] text-slate-muted">
                    {activityChannelLabel(row.channel)}
                  </p>
                </div>
                <div className="flex min-w-0 items-center gap-2">
                  <p className="truncate text-sm">{row.personaName}</p>
                  <Badge
                    tone={targetTypeBadgeTone(row.personaType)}
                    uppercase={false}
                    className="shrink-0"
                  >
                    {activityTargetTypeLabel(row.personaType)}
                  </Badge>
                </div>
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
