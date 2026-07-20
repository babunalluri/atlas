"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import {
  getWorkflowAssignments,
  listTenantUsers,
  saveWorkflowAssignments,
} from "@/lib/api/admin";
import type { TenantUser } from "@/lib/api/types";
import { useAgentOsToken } from "@/lib/auth/token";

export function WorkflowAccessPanel({ workflowId }: { workflowId: string }) {
  const { getAccessToken } = useAgentOsToken();
  const [users, setUsers] = useState<TenantUser[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const token = await getAccessToken();
        const [directory, assignments] = await Promise.all([
          listTenantUsers(token),
          getWorkflowAssignments(token, workflowId),
        ]);
        if (cancelled) return;
        setUsers(directory.filter((user) => user.isActive));
        setSelected(assignments.userIds);
      } catch (reason) {
        if (!cancelled) {
          setMessage(
            reason instanceof Error ? reason.message : "Could not load access",
          );
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [getAccessToken, workflowId]);

  function toggle(userId: string) {
    setSelected((current) =>
      current.includes(userId)
        ? current.filter((value) => value !== userId)
        : [...current, userId],
    );
  }

  async function save() {
    setSaving(true);
    setMessage(null);
    try {
      const result = await saveWorkflowAssignments(
        await getAccessToken(),
        workflowId,
        selected,
      );
      setSelected(result.userIds);
      setMessage(`Access saved for ${result.userIds.length} user(s)`);
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "Could not save access");
    } finally {
      setSaving(false);
    }
  }

  const knownIds = new Set(users.map((user) => user.userId));
  const orphanIds = selected.filter((userId) => !knownIds.has(userId));

  return (
    <section className="surface-panel rounded-2xl p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="font-display text-lg font-semibold">User access</h2>
            <Badge tone="info">{selected.length} assigned</Badge>
          </div>
          <p className="mt-1 text-sm text-slate-muted">
            Assign this workflow from the Users directory. Prefer Configure →
            Users for full CRUD.
          </p>
        </div>
        <div className="flex gap-2">
          <Link
            href="/admin/users"
            className="inline-flex items-center justify-center rounded-md border border-line bg-raised px-3.5 py-2 text-sm font-medium text-ink transition hover:border-line-strong hover:bg-mist"
          >
            Manage users
          </Link>
          <Button variant="accent" onClick={save} disabled={loading || saving}>
            {saving ? "Saving…" : "Save access"}
          </Button>
        </div>
      </div>

      <div className="mt-4 space-y-2">
        {users.map((user) => (
          <label
            key={user.id}
            className="flex cursor-pointer items-center gap-3 rounded-xl border border-line bg-raised/50 px-3 py-2.5 hover:border-teal/40"
          >
            <input
              type="checkbox"
              checked={selected.includes(user.userId)}
              onChange={() => toggle(user.userId)}
              className="size-4 accent-teal"
            />
            <span className="min-w-0">
              <span className="block truncate text-sm font-medium">
                {user.displayName}
              </span>
              <span className="mono-cell block truncate text-slate-muted">
                {user.email || user.userId}
              </span>
            </span>
          </label>
        ))}
        {!loading && users.length === 0 ? (
          <p className="rounded-xl border border-dashed border-line px-3 py-5 text-center text-sm text-slate-muted">
            No active users yet. Create them under Configure → Users.
          </p>
        ) : null}
      </div>

      {orphanIds.length > 0 ? (
        <div className="mt-3 flex flex-wrap gap-2">
          {orphanIds.map((userId) => (
            <button key={userId} type="button" onClick={() => toggle(userId)}>
              <Badge tone="neutral">{userId} ×</Badge>
            </button>
          ))}
        </div>
      ) : null}
      {message ? <p className="mt-3 text-xs text-slate-muted">{message}</p> : null}
    </section>
  );
}
