"use client";

import Link from "next/link";
import { useMemo, useState } from "react";

import { Badge } from "@/components/ui/Badge";
import { Button, buttonClassName } from "@/components/ui/Button";
import { Input } from "@/components/ui/Field";
import { PencilIcon, TrashIcon } from "@/components/ui/icons";
import { deleteTenantUser } from "@/lib/api/admin";
import type { TenantUser } from "@/lib/api/types";
import { useAgentOsToken } from "@/lib/auth/token";
import { cn, formatRelative } from "@/lib/utils";

type StatusFilter = "all" | "active" | "inactive";

const PAGE_SIZE = 25;

export function UserList({ initialUsers }: { initialUsers: TenantUser[] }) {
  const { getAccessToken } = useAgentOsToken();
  const [users, setUsers] = useState(initialUsers);
  const [error, setError] = useState<string | null>(null);
  const [deletingIds, setDeletingIds] = useState<Set<string>>(() => new Set());
  const [q, setQ] = useState("");
  const [status, setStatus] = useState<StatusFilter>("all");
  const [page, setPage] = useState(1);

  const filtered = useMemo(() => {
    const query = q.trim().toLowerCase();
    return users.filter((user) => {
      if (status === "active" && !user.isActive) return false;
      if (status === "inactive" && user.isActive) return false;
      if (!query) return true;
      const haystack = [
        user.displayName,
        user.email ?? "",
        user.userId,
        user.role,
      ]
        .join(" ")
        .toLowerCase();
      return haystack.includes(query);
    });
  }, [q, status, users]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const safePage = Math.min(page, totalPages);
  const start = (safePage - 1) * PAGE_SIZE;
  const pageItems = filtered.slice(start, start + PAGE_SIZE);
  const end = Math.min(filtered.length, start + PAGE_SIZE);

  async function onDelete(user: TenantUser) {
    if (
      !window.confirm(
        `Delete “${user.displayName}”? This cannot be undone.`,
      )
    ) {
      return;
    }
    setDeletingIds((current) => new Set(current).add(user.id));
    setError(null);
    try {
      await deleteTenantUser(await getAccessToken(), user.id);
      setUsers((prev) => prev.filter((item) => item.id !== user.id));
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Failed to delete user",
      );
    } finally {
      setDeletingIds((current) => {
        const next = new Set(current);
        next.delete(user.id);
        return next;
      });
    }
  }

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-display text-2xl font-semibold tracking-tight">
            Users
          </h1>
          <p className="mt-0.5 text-sm text-slate-muted">
            Directory members and the workflows they can open in chat.
          </p>
        </div>
        <Link
          href="/admin/users/new"
          className={buttonClassName({ variant: "accent" })}
        >
          Create
        </Link>
      </header>
      {error ? <p className="text-sm text-rose">{error}</p> : null}

      <section className="table-shell rounded-xl">
        <div className="space-y-3 border-b border-line px-4 py-3">
          <div className="flex flex-wrap items-center gap-2">
            <Input
              value={q}
              placeholder="Search users…"
              className="min-w-[220px] flex-1"
              onChange={(event) => {
                setQ(event.target.value);
                setPage(1);
              }}
            />
            {(
              [
                ["all", "All"],
                ["active", "Active"],
                ["inactive", "Inactive"],
              ] as const
            ).map(([value, label]) => (
              <button
                key={value}
                type="button"
                onClick={() => {
                  setStatus(value);
                  setPage(1);
                }}
                className={cn(
                  "rounded-md border px-2.5 py-1.5 text-xs font-medium transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ink/20 focus-visible:ring-offset-1 focus-visible:ring-offset-canvas",
                  status === value
                    ? "border-line-strong bg-mist text-ink"
                    : "border-transparent bg-raised text-slate-muted hover:bg-mist",
                )}
              >
                {label}
              </button>
            ))}
          </div>
          <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-slate-muted">
            <p>
              {filtered.length === 0
                ? "No users match"
                : `Showing ${start + 1}–${end} of ${filtered.length} users`}
            </p>
            <div className="flex items-center gap-2">
              <Button
                size="sm"
                variant="secondary"
                disabled={safePage <= 1}
                onClick={() => setPage((current) => Math.max(1, current - 1))}
              >
                Previous
              </Button>
              <span className="mono-cell">
                {safePage} / {totalPages}
              </span>
              <Button
                size="sm"
                variant="secondary"
                disabled={safePage >= totalPages}
                onClick={() =>
                  setPage((current) => Math.min(totalPages, current + 1))
                }
              >
                Next
              </Button>
            </div>
          </div>
        </div>

        <div className="hidden items-center gap-3 border-b border-line px-4 py-2 md:flex">
          <div className="grid min-w-0 flex-1 grid-cols-[1.4fr_0.7fr_0.6fr_0.6fr] gap-3">
            <span className="th-label">User</span>
            <span className="th-label">Role</span>
            <span className="th-label">Access</span>
            <span className="th-label text-right">Updated</span>
          </div>
          <span className="th-label w-auto shrink-0 text-right">Actions</span>
        </div>
        <ul>
          {pageItems.map((user) => (
            <li key={user.id} className="border-b border-line/60 last:border-0">
              <div className="flex items-center gap-3 px-4 py-2.5 transition hover:bg-mist/70">
                <Link
                  href={`/admin/users/${user.id}`}
                  className="grid min-w-0 flex-1 items-center gap-3 md:grid-cols-[1.4fr_0.7fr_0.6fr_0.6fr]"
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
                    <Badge
                      tone={user.role === "tenant_admin" ? "info" : "neutral"}
                    >
                      {user.role === "tenant_admin" ? "Admin" : "User"}
                    </Badge>
                    {!user.isActive ? (
                      <Badge tone="warning">Inactive</Badge>
                    ) : null}
                    {user.invitePending ? (
                      <Badge tone="warning">Invite pending</Badge>
                    ) : null}
                  </div>
                  <p className="text-sm text-slate-muted">
                    {user.workflowIds.length + user.teamIds.length}
                  </p>
                  <p className="mono-cell text-right text-slate-muted">
                    {formatRelative(user.updatedAt)}
                  </p>
                </Link>
                <div className="flex shrink-0 items-center justify-end gap-0.5">
                  <Link
                    href={`/admin/users/${user.id}`}
                    className={buttonClassName({
                      variant: "ghost",
                      size: "icon",
                    })}
                    aria-label={`Edit ${user.displayName}`}
                    title="Edit"
                  >
                    <PencilIcon />
                  </Link>
                  <Button
                    size="icon"
                    variant="danger"
                    aria-label={`Delete ${user.displayName}`}
                    title="Delete"
                    disabled={deletingIds.has(user.id)}
                    onClick={() => void onDelete(user)}
                  >
                    {deletingIds.has(user.id) ? "…" : <TrashIcon />}
                  </Button>
                </div>
              </div>
            </li>
          ))}
          {filtered.length === 0 ? (
            <li className="px-4 py-10 text-center text-sm text-slate-muted">
              {users.length === 0
                ? "No users yet — create one above."
                : "No users match this search."}
            </li>
          ) : null}
        </ul>
      </section>
    </div>
  );
}
