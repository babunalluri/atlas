"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";

import { BackLink } from "@/components/ui/BackLink";
import { Badge } from "@/components/ui/Badge";
import { Button, buttonClassName } from "@/components/ui/Button";
import { EditorActions } from "@/components/ui/EditorActions";
import { Input, Label, Select } from "@/components/ui/Field";
import { SaveIcon, TrashIcon } from "@/components/ui/icons";
import {
  createTenantUser,
  deleteTenantUser,
  syncTenantUserIdentity,
  updateTenantUser,
} from "@/lib/api/admin";
import type {
  TeamSummary,
  TenantUser,
  TenantUserInput,
  WorkflowSummary,
} from "@/lib/api/types";
import { useAgentOsToken } from "@/lib/auth/token";
import { formatRelative } from "@/lib/utils";
import { UserVaultSection } from "@/components/vault/UserVaultSection";

export function UserEditor({
  initial,
  workflows,
  teams,
  mode,
  defaultDisplayName = "",
}: {
  initial?: TenantUser;
  workflows: WorkflowSummary[];
  teams: TeamSummary[];
  mode: "create" | "edit";
  defaultDisplayName?: string;
}) {
  const router = useRouter();
  const { getAccessToken } = useAgentOsToken();
  const [form, setForm] = useState<TenantUserInput>(() =>
    initial
      ? {
          userId: initial.userId,
          displayName: initial.displayName,
          email: initial.email ?? "",
          phone: initial.phone ?? "",
          role: initial.role,
          isActive: initial.isActive,
          workflowIds: initial.workflowIds,
          teamIds: initial.teamIds,
        }
      : {
          displayName: defaultDisplayName,
          email: "",
          phone: "",
          role: "end_user",
          isActive: true,
          workflowIds: [],
          teamIds: [],
        },
  );
  const [busy, setBusy] = useState<"save" | "delete" | "sync" | null>(null);
  const [banner, setBanner] = useState<string | null>(null);
  const needsIdentitySync =
    mode === "edit" &&
    Boolean(initial?.email) &&
    (Boolean(initial?.invitePending) ||
      !form.userId ||
      form.userId.startsWith("pending:") ||
      form.userId.startsWith("invite:"));

  const publishedWorkflows = useMemo(
    () => workflows.filter((workflow) => workflow.status === "published"),
    [workflows],
  );
  const publishedTeams = useMemo(
    () => teams.filter((team) => team.status === "published"),
    [teams],
  );

  function toggleWorkflow(workflowId: string) {
    setForm((current) => ({
      ...current,
      workflowIds: current.workflowIds.includes(workflowId)
        ? current.workflowIds.filter((id) => id !== workflowId)
        : [...current.workflowIds, workflowId],
    }));
  }

  function toggleTeam(teamId: string) {
    setForm((current) => ({
      ...current,
      teamIds: current.teamIds.includes(teamId)
        ? current.teamIds.filter((id) => id !== teamId)
        : [...current.teamIds, teamId],
    }));
  }

  async function save() {
    if (!form.displayName.trim()) {
      setBanner("Display name is required");
      return;
    }
    if (mode === "create" && !form.email.trim()) {
      setBanner("Email is required");
      return;
    }
    setBusy("save");
    setBanner(null);
    try {
      const token = await getAccessToken();
      if (mode === "create") {
        const created = await createTenantUser(token, {
          ...form,
          email: form.email.trim(),
        });
        if (created.temporaryPassword) {
          window.sessionStorage.setItem(
            `atlas_dev_password_${created.id}`,
            created.temporaryPassword,
          );
        }
        router.push(`/admin/users/${created.id}`);
        return;
      }
      if (!initial) return;
      const updated = await updateTenantUser(token, initial.id, {
        displayName: form.displayName,
        email: form.email.trim() || "",
        phone: form.phone?.trim() || "",
        role: form.role,
        isActive: form.isActive,
        workflowIds: form.workflowIds,
        teamIds: form.teamIds,
      });
      setForm({
        userId: updated.userId,
        displayName: updated.displayName,
        email: updated.email ?? "",
        phone: updated.phone ?? "",
        role: updated.role,
        isActive: updated.isActive,
        workflowIds: updated.workflowIds,
        teamIds: updated.teamIds,
      });
      setBanner("Saved");
      router.refresh();
    } catch (reason) {
      setBanner(reason instanceof Error ? reason.message : "Save failed");
    } finally {
      setBusy(null);
    }
  }

  async function remove() {
    if (!initial || mode !== "edit") return;
    setBusy("delete");
    setBanner(null);
    try {
      await deleteTenantUser(await getAccessToken(), initial.id);
      router.push("/admin/users");
    } catch (reason) {
      setBanner(reason instanceof Error ? reason.message : "Delete failed");
      setBusy(null);
    }
  }

  async function syncIdentity() {
    if (!initial || mode !== "edit") return;
    setBusy("sync");
    setBanner(null);
    try {
      const updated = await syncTenantUserIdentity(
        await getAccessToken(),
        initial.id,
      );
      setForm({
        userId: updated.userId,
        displayName: updated.displayName,
        email: updated.email ?? "",
        phone: updated.phone ?? "",
        role: updated.role,
        isActive: updated.isActive,
        workflowIds: updated.workflowIds,
        teamIds: updated.teamIds,
      });
      setBanner(
        updated.temporaryPassword
          ? `Synced. Temporary password: ${updated.temporaryPassword}`
          : updated.invitePending || updated.userId.startsWith("pending:")
            ? "Pending membership ready — create/link the user in Keycloak, then have them sign in"
            : "Identity sync recorded",
      );
      router.refresh();
    } catch (reason) {
      setBanner(reason instanceof Error ? reason.message : "Sync failed");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="space-y-3">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="text-xs text-slate-muted">
            <Link href="/admin/users" className="hover:text-ink">
              Users
            </Link>
            <span className="mx-1.5">/</span>
            {mode === "create" ? "New" : form.displayName || "User"}
          </p>
          <div className="flex min-w-0 items-center gap-1.5">
            <BackLink href="/admin/users" label="Back to users" />
            <h1 className="min-w-0 truncate py-0.5 font-display text-2xl font-semibold leading-snug tracking-tight">
              {mode === "create"
                ? "New user"
                : form.displayName || "Untitled user"}
            </h1>
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-slate-muted">
            {mode === "edit" && initial ? (
              <>
                <Badge tone={form.isActive ? "success" : "warning"}>
                  {form.isActive ? "active" : "inactive"}
                </Badge>
                <Badge
                  tone={form.role === "tenant_admin" ? "info" : "neutral"}
                >
                  {form.role === "tenant_admin" ? "Admin" : "User"}
                </Badge>
                {initial.invitePending || needsIdentitySync ? (
                  <Badge tone="warning">not synced</Badge>
                ) : null}
                <span>{formatRelative(initial.updatedAt)}</span>
              </>
            ) : (
              <span>
                Enter email and name — Atlas invites them into this organization
                and maps the account automatically.
              </span>
            )}
          </div>
        </div>
        <EditorActions>
          {mode === "edit" && form.userId && !form.userId.startsWith("invite:") ? (
            <>
              <Link
                href={`/admin/notifications?userId=${encodeURIComponent(form.userId)}`}
                className={buttonClassName({ variant: "secondary", size: "sm" })}
              >
                Notify
              </Link>
              <Link
                href={`/admin/billing?userId=${encodeURIComponent(form.userId)}`}
                className={buttonClassName({ variant: "secondary", size: "sm" })}
              >
                Grant credits
              </Link>
            </>
          ) : null}
          {mode === "edit" && needsIdentitySync ? (
            <Button
              variant="secondary"
              size="sm"
              onClick={() => void syncIdentity()}
              disabled={busy !== null}
            >
              {busy === "sync" ? "Syncing…" : "Sync to sign-in"}
            </Button>
          ) : null}
          {mode === "edit" ? (
            <Button
              variant="secondary"
              size="sm"
              onClick={() => void remove()}
              disabled={busy !== null}
            >
              <TrashIcon />
              {busy === "delete" ? "Deleting…" : "Delete"}
            </Button>
          ) : null}
          <Button
            variant="accent"
            size="sm"
            onClick={() => void save()}
            disabled={busy !== null}
          >
            <SaveIcon />
            {busy === "save"
              ? "Saving…"
              : mode === "create"
                ? "Invite"
                : "Save"}
          </Button>
        </EditorActions>
      </header>

      {banner ? (
        <p className="rounded-md border border-teal/30 bg-teal/10 px-3 py-1.5 text-sm">
          {banner}
        </p>
      ) : null}

      <section className="rounded-xl border border-line bg-raised/40 p-3">
        <div className="grid gap-2.5 sm:grid-cols-2">
          {mode === "edit" ? (
            <div className="sm:col-span-2">
              <Label htmlFor="user-id" hint="Assigned automatically on invite / first sign-in">
                Account ID
              </Label>
              <Input
                id="user-id"
                value={form.userId ?? ""}
                disabled
                placeholder="pending…"
              />
            </div>
          ) : null}
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
            <Label htmlFor="email" hint={mode === "create" ? "Invite is sent to this address" : undefined}>
              Email
            </Label>
            <Input
              id="email"
              type="email"
              value={form.email ?? ""}
              disabled={mode === "edit" && Boolean(initial?.invitePending)}
              onChange={(event) =>
                setForm((current) => ({
                  ...current,
                  email: event.target.value,
                }))
              }
            />
          </div>
          <div>
            <Label htmlFor="phone" hint="Optional">
              Phone
            </Label>
            <Input
              id="phone"
              type="tel"
              value={form.phone ?? ""}
              placeholder="+1 555 0100"
              onChange={(event) =>
                setForm((current) => ({
                  ...current,
                  phone: event.target.value,
                }))
              }
            />
          </div>
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
      </section>

      {mode === "edit" && form.userId && !form.userId.startsWith("invite:") ? (
        <UserVaultSection userId={form.userId} />
      ) : mode === "edit" ? (
        <section className="rounded-xl border border-line bg-raised/40 p-3">
          <h2 className="text-sm font-semibold">Secrets & Variables</h2>
          <p className="mt-1 text-xs text-slate-muted">
            Sync this user to a sign-in account first, then you can set their
            personal tool keys here.
          </p>
        </section>
      ) : null}

      <section className="rounded-xl border border-line bg-raised/40 p-3">
        <div className="mb-2 flex items-center justify-between gap-2">
          <div>
            <h2 className="text-sm font-semibold">Assigned workflows</h2>
            <p className="text-xs text-slate-muted">
              Published workflows this user can open in chat.
            </p>
          </div>
          <Badge tone="info">{form.workflowIds.length} selected</Badge>
        </div>
        <ul className="max-h-72 divide-y divide-line overflow-y-auto rounded-md border border-line">
          {publishedWorkflows.map((workflow) => (
            <li key={workflow.id}>
              <label className="flex cursor-pointer items-center gap-2.5 px-2.5 py-1.5 hover:bg-mist/60">
                <input
                  type="checkbox"
                  className="size-3.5 accent-teal"
                  checked={form.workflowIds.includes(workflow.id)}
                  onChange={() => toggleWorkflow(workflow.id)}
                />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-medium">
                    {workflow.name}
                  </span>
                  <span className="mono-cell block truncate text-[11px] text-slate-muted">
                    /{workflow.slug}
                  </span>
                </span>
              </label>
            </li>
          ))}
          {publishedWorkflows.length === 0 ? (
            <li className="px-3 py-4 text-center text-sm text-slate-muted">
              Publish a workflow first, then assign it here.
            </li>
          ) : null}
        </ul>
      </section>

      <section className="rounded-xl border border-line bg-raised/40 p-3">
        <div className="mb-2 flex items-center justify-between gap-2">
          <div>
            <h2 className="text-sm font-semibold">Assigned teams</h2>
            <p className="text-xs text-slate-muted">
              Published teams this user can open in chat.
            </p>
          </div>
          <Badge tone="info">{form.teamIds.length} selected</Badge>
        </div>
        <ul className="max-h-72 divide-y divide-line overflow-y-auto rounded-md border border-line">
          {publishedTeams.map((team) => (
            <li key={team.id}>
              <label className="flex cursor-pointer items-center gap-2.5 px-2.5 py-1.5 hover:bg-mist/60">
                <input
                  type="checkbox"
                  className="size-3.5 accent-teal"
                  checked={form.teamIds.includes(team.id)}
                  onChange={() => toggleTeam(team.id)}
                />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-medium">
                    {team.name}
                  </span>
                  <span className="mono-cell block truncate text-[11px] text-slate-muted">
                    /{team.slug}
                  </span>
                </span>
              </label>
            </li>
          ))}
          {publishedTeams.length === 0 ? (
            <li className="px-3 py-4 text-center text-sm text-slate-muted">
              Publish a team first, then assign it here.
            </li>
          ) : null}
        </ul>
      </section>
    </div>
  );
}
