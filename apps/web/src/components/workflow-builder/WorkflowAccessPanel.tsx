"use client";

import { useOrganization } from "@clerk/nextjs";
import { useEffect, useMemo, useState } from "react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Input, Label } from "@/components/ui/Field";
import {
  getWorkflowAssignments,
  saveWorkflowAssignments,
} from "@/lib/api/admin";
import { useAgentOsToken } from "@/lib/auth/token";

export function WorkflowAccessPanel({ workflowId }: { workflowId: string }) {
  const { getAccessToken } = useAgentOsToken();
  const { memberships } = useOrganization({
    memberships: { infinite: true },
  });
  const [selected, setSelected] = useState<string[]>([]);
  const [customUserId, setCustomUserId] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const organizationUsers = useMemo(
    () =>
      (memberships?.data ?? []).flatMap((membership) => {
        const profile = membership.publicUserData;
        if (!profile?.userId) return [];
        const name =
          [profile.firstName, profile.lastName].filter(Boolean).join(" ") ||
          profile.identifier ||
          profile.userId;
        return [{ id: profile.userId, name, identifier: profile.identifier }];
      }),
    [memberships?.data],
  );

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const result = await getWorkflowAssignments(
          await getAccessToken(),
          workflowId,
        );
        if (!cancelled) setSelected(result.userIds);
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

  function addCustomUser() {
    const value = customUserId.trim();
    if (!value) return;
    setSelected((current) =>
      current.includes(value) ? current : [...current, value],
    );
    setCustomUserId("");
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

  const knownIds = new Set(organizationUsers.map((user) => user.id));
  const customIds = selected.filter((userId) => !knownIds.has(userId));

  return (
    <section className="surface-panel rounded-2xl p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="font-display text-lg font-semibold">User access</h2>
            <Badge tone="info">{selected.length} assigned</Badge>
          </div>
          <p className="mt-1 text-sm text-slate-muted">
            Choose which organization users can select and run this workflow in
            chat. Tenant and platform admins retain access automatically.
          </p>
        </div>
        <Button variant="accent" onClick={save} disabled={loading || saving}>
          {saving ? "Saving…" : "Save access"}
        </Button>
      </div>

      <div className="mt-4 space-y-2">
        {organizationUsers.map((user) => (
          <label
            key={user.id}
            className="flex cursor-pointer items-center gap-3 rounded-xl border border-line bg-raised/50 px-3 py-2.5 hover:border-teal/40"
          >
            <input
              type="checkbox"
              checked={selected.includes(user.id)}
              onChange={() => toggle(user.id)}
              className="size-4 accent-teal"
            />
            <span className="min-w-0">
              <span className="block truncate text-sm font-medium">{user.name}</span>
              <span className="mono-cell block truncate text-slate-muted">
                {user.identifier || user.id}
              </span>
            </span>
          </label>
        ))}
        {!memberships?.isLoading && organizationUsers.length === 0 ? (
          <p className="rounded-xl border border-dashed border-line px-3 py-5 text-center text-sm text-slate-muted">
            No Clerk organization members were returned. Add a user ID below.
          </p>
        ) : null}
      </div>

      <div className="mt-4">
        <Label htmlFor="workflow-user-id" hint="Clerk user ID">
          Add user manually
        </Label>
        <div className="flex gap-2">
          <Input
            id="workflow-user-id"
            value={customUserId}
            placeholder="user_..."
            onChange={(event) => setCustomUserId(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                addCustomUser();
              }
            }}
          />
          <Button variant="secondary" onClick={addCustomUser}>
            Add
          </Button>
        </div>
      </div>

      {customIds.length > 0 ? (
        <div className="mt-3 flex flex-wrap gap-2">
          {customIds.map((userId) => (
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
