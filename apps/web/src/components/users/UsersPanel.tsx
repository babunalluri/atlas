"use client";

import { useMemo, useState } from "react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Input, Label, Select } from "@/components/ui/Field";
import {
  createTenantUser,
  deleteTenantUser,
  updateTenantUser,
} from "@/lib/api/admin";
import type {
  TenantUser,
  TenantUserInput,
  WorkflowSummary,
} from "@/lib/api/types";
import { useAgentOsToken } from "@/lib/auth/token";
import { formatRelative } from "@/lib/utils";

const emptyForm = (): TenantUserInput => ({
  userId: "",
  displayName: "",
  email: "",
  role: "end_user",
  isActive: true,
  workflowIds: [],
});

export function UsersPanel({
  initialUsers,
  workflows,
}: {
  initialUsers: TenantUser[];
  workflows: WorkflowSummary[];
}) {
  const { getAccessToken } = useAgentOsToken();
  const [users, setUsers] = useState(initialUsers);
  const [selectedId, setSelectedId] = useState<string | null>(
    initialUsers[0]?.id ?? null,
  );
  const [creating, setCreating] = useState(initialUsers.length === 0);
  const [form, setForm] = useState<TenantUserInput>(
    initialUsers[0]
      ? {
          userId: initialUsers[0].userId,
          displayName: initialUsers[0].displayName,
          email: initialUsers[0].email ?? "",
          role: initialUsers[0].role,
          isActive: initialUsers[0].isActive,
          workflowIds: initialUsers[0].workflowIds,
        }
      : emptyForm(),
  );
  const [busy, setBusy] = useState<"save" | "delete" | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const publishedWorkflows = useMemo(
    () => workflows.filter((workflow) => workflow.status === "published"),
    [workflows],
  );

  const selected = users.find((user) => user.id === selectedId) ?? null;

  function startCreate() {
    setCreating(true);
    setSelectedId(null);
    setForm(emptyForm());
    setMessage(null);
  }

  function selectUser(user: TenantUser) {
    setCreating(false);
    setSelectedId(user.id);
    setForm({
      userId: user.userId,
      displayName: user.displayName,
      email: user.email ?? "",
      role: user.role,
      isActive: user.isActive,
      workflowIds: user.workflowIds,
    });
    setMessage(null);
  }

  function toggleWorkflow(workflowId: string) {
    setForm((current) => ({
      ...current,
      workflowIds: current.workflowIds.includes(workflowId)
        ? current.workflowIds.filter((id) => id !== workflowId)
        : [...current.workflowIds, workflowId],
    }));
  }

  async function save() {
    if (!form.displayName.trim() || (!creating && !selected)) {
      setMessage("Display name is required");
      return;
    }
    if (creating && !form.userId.trim()) {
      setMessage("Clerk user ID is required");
      return;
    }
    setBusy("save");
    setMessage(null);
    try {
      const token = await getAccessToken();
      if (creating) {
        const created = await createTenantUser(token, {
          ...form,
          email: form.email?.trim() || null,
        });
        setUsers((current) =>
          [...current, created].sort((a, b) =>
            a.displayName.localeCompare(b.displayName),
          ),
        );
        selectUser(created);
        setMessage("User created");
      } else if (selected) {
        const updated = await updateTenantUser(token, selected.id, {
          displayName: form.displayName,
          email: form.email?.trim() || null,
          role: form.role,
          isActive: form.isActive,
          workflowIds: form.workflowIds,
        });
        setUsers((current) =>
          current.map((user) => (user.id === updated.id ? updated : user)),
        );
        selectUser(updated);
        setMessage("User updated");
      }
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "Save failed");
    } finally {
      setBusy(null);
    }
  }

  async function remove() {
    if (!selected || creating) return;
    setBusy("delete");
    setMessage(null);
    try {
      await deleteTenantUser(await getAccessToken(), selected.id);
      const remaining = users.filter((user) => user.id !== selected.id);
      setUsers(remaining);
      if (remaining[0]) {
        selectUser(remaining[0]);
      } else {
        startCreate();
      }
      setMessage("User deleted");
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "Delete failed");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.14em] text-teal">
            Configure
          </p>
          <h1 className="font-display text-4xl font-semibold tracking-tight">
            Users
          </h1>
          <p className="mt-2 max-w-2xl text-sm text-slate-muted">
            Manage organization users and assign the workflows they can open in
            chat.
          </p>
        </div>
        <Button variant="accent" onClick={startCreate}>
          Add user
        </Button>
      </header>

      {message ? (
        <div className="rounded-lg border border-teal/30 bg-teal/10 px-3 py-2 text-sm">
          {message}
        </div>
      ) : null}

      <section className="grid gap-4 lg:grid-cols-[minmax(0,1.1fr)_minmax(320px,0.9fr)]">
        <aside className="table-shell rounded-xl">
          <div className="grid grid-cols-[1.4fr_0.8fr_0.6fr] gap-3 border-b border-line px-4 py-2.5">
            <span className="th-label">User</span>
            <span className="th-label">Role</span>
            <span className="th-label text-right">Workflows</span>
          </div>
          <ul>
            {users.map((user) => (
              <li key={user.id} className="border-b border-line/60 last:border-0">
                <button
                  type="button"
                  onClick={() => selectUser(user)}
                  className={`grid w-full grid-cols-[1.4fr_0.8fr_0.6fr] items-center gap-3 px-4 py-2.5 text-left transition ${
                    selectedId === user.id && !creating
                      ? "bg-mist/80"
                      : "hover:bg-mist/50"
                  }`}
                >
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium">
                      {user.displayName}
                    </p>
                    <p className="mono-cell truncate text-slate-muted">
                      {user.email || user.userId}
                    </p>
                  </div>
                  <div className="flex flex-wrap gap-1">
                    <Badge tone={user.role === "tenant_admin" ? "info" : "neutral"}>
                      {user.role === "tenant_admin" ? "Admin" : "User"}
                    </Badge>
                    {!user.isActive ? <Badge tone="warning">Inactive</Badge> : null}
                  </div>
                  <p className="text-right text-sm text-slate-muted">
                    {user.workflowIds.length}
                  </p>
                </button>
              </li>
            ))}
            {users.length === 0 ? (
              <li className="px-4 py-10 text-center text-sm text-slate-muted">
                No users yet — add the first organization member.
              </li>
            ) : null}
          </ul>
        </aside>

        <section className="surface-panel rounded-2xl p-5">
          <div className="flex items-start justify-between gap-3">
            <div>
              <h2 className="font-display text-lg font-semibold">
                {creating ? "New user" : selected?.displayName || "User"}
              </h2>
              <p className="mt-1 text-sm text-slate-muted">
                {creating
                  ? "Create a directory entry and assign published workflows."
                  : `Updated ${selected ? formatRelative(selected.updatedAt) : "—"}`}
              </p>
            </div>
            {!creating && selected ? (
              <Button
                variant="secondary"
                onClick={remove}
                disabled={busy !== null}
              >
                {busy === "delete" ? "Deleting…" : "Delete"}
              </Button>
            ) : null}
          </div>

          <div className="mt-4 grid gap-4">
            <div>
              <Label htmlFor="user-id" hint="Clerk user ID">
                User ID
              </Label>
              <Input
                id="user-id"
                value={form.userId}
                disabled={!creating}
                placeholder="user_..."
                onChange={(event) =>
                  setForm((current) => ({
                    ...current,
                    userId: event.target.value,
                  }))
                }
              />
            </div>
            <div>
              <Label htmlFor="display-name">Display name</Label>
              <Input
                id="display-name"
                value={form.displayName}
                onChange={(event) =>
                  setForm((current) => ({
                    ...current,
                    displayName: event.target.value,
                  }))
                }
              />
            </div>
            <div>
              <Label htmlFor="email">Email</Label>
              <Input
                id="email"
                type="email"
                value={form.email ?? ""}
                onChange={(event) =>
                  setForm((current) => ({
                    ...current,
                    email: event.target.value,
                  }))
                }
              />
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <Label htmlFor="role">Role</Label>
                <Select
                  id="role"
                  value={form.role}
                  onChange={(event) =>
                    setForm((current) => ({
                      ...current,
                      role: event.target.value as TenantUserInput["role"],
                    }))
                  }
                >
                  <option value="end_user">End user</option>
                  <option value="tenant_admin">Tenant admin</option>
                </Select>
              </div>
              <div>
                <Label htmlFor="active">Status</Label>
                <Select
                  id="active"
                  value={form.isActive ? "active" : "inactive"}
                  onChange={(event) =>
                    setForm((current) => ({
                      ...current,
                      isActive: event.target.value === "active",
                    }))
                  }
                >
                  <option value="active">Active</option>
                  <option value="inactive">Inactive</option>
                </Select>
              </div>
            </div>
          </div>

          <div className="mt-5 border-t border-line pt-4">
            <div className="flex items-center justify-between gap-3">
              <h3 className="text-sm font-semibold">Assigned workflows</h3>
              <Badge tone="info">{form.workflowIds.length} selected</Badge>
            </div>
            <div className="mt-3 space-y-2">
              {publishedWorkflows.map((workflow) => (
                <label
                  key={workflow.id}
                  className="flex cursor-pointer items-center gap-3 rounded-xl border border-line bg-raised/50 px-3 py-2.5 hover:border-teal/40"
                >
                  <input
                    type="checkbox"
                    className="size-4 accent-teal"
                    checked={form.workflowIds.includes(workflow.id)}
                    onChange={() => toggleWorkflow(workflow.id)}
                  />
                  <span className="min-w-0">
                    <span className="block truncate text-sm font-medium">
                      {workflow.name}
                    </span>
                    <span className="mono-cell block truncate text-slate-muted">
                      /{workflow.slug}
                    </span>
                  </span>
                </label>
              ))}
              {publishedWorkflows.length === 0 ? (
                <p className="rounded-xl border border-dashed border-line px-3 py-5 text-center text-sm text-slate-muted">
                  Publish a workflow first, then assign it here.
                </p>
              ) : null}
            </div>
          </div>

          <div className="mt-5 flex justify-end">
            <Button variant="accent" onClick={save} disabled={busy !== null}>
              {busy === "save"
                ? "Saving…"
                : creating
                  ? "Create user"
                  : "Save changes"}
            </Button>
          </div>
        </section>
      </section>
    </div>
  );
}
