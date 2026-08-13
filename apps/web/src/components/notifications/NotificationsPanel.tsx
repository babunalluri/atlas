"use client";

import { Link } from "@/i18n/navigation";
import { useMemo, useState } from "react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Input, Label, Textarea } from "@/components/ui/Field";
import { SearchableSelect } from "@/components/ui/SearchableSelect";
import { listSentNotifications, sendOrgNotification } from "@/lib/api/admin";
import type { NotificationBatch, TenantUser } from "@/lib/api/types";
import { useAgentOsToken } from "@/lib/auth/token";
import { formatRelative } from "@/lib/utils";

type AudienceMode = "all" | "user";

export function NotificationsPanel({
  users,
  initialSent,
  prefillUserId = null,
}: {
  users: TenantUser[];
  initialSent: NotificationBatch[];
  prefillUserId?: string | null;
}) {
  const { getAccessToken } = useAgentOsToken();
  const selectable = useMemo(
    () =>
      users.filter(
        (user) =>
          user.isActive &&
          user.userId &&
          !user.userId.startsWith("invite:") &&
          !user.invitePending,
      ),
    [users],
  );
  const [audience, setAudience] = useState<AudienceMode>(
    prefillUserId ? "user" : "all",
  );
  const [userId, setUserId] = useState(
    prefillUserId && selectable.some((u) => u.userId === prefillUserId)
      ? prefillUserId
      : selectable[0]?.userId ?? "",
  );
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [sent, setSent] = useState(initialSent);
  const [busy, setBusy] = useState(false);
  const [banner, setBanner] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function refreshSent() {
    const token = await getAccessToken();
    setSent(await listSentNotifications(token));
  }

  async function onSend() {
    if (!title.trim() || !body.trim()) {
      setError("Title and message are required");
      return;
    }
    if (selectable.length === 0) {
      setError(
        "This organization has no active users yet. Create a user under Users first.",
      );
      return;
    }
    if (audience === "user" && !userId) {
      setError("Select a user");
      return;
    }
    setBusy(true);
    setError(null);
    setBanner(null);
    try {
      const token = await getAccessToken();
      const result = await sendOrgNotification(token, {
        title: title.trim(),
        body: body.trim(),
        userId: audience === "user" ? userId : null,
      });
      setTitle("");
      setBody("");
      setBanner(
        `Sent to ${result.recipientCount} recipient${result.recipientCount === 1 ? "" : "s"}`,
      );
      await refreshSent();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Send failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-display text-2xl font-semibold tracking-tight">
            Notifications
          </h1>
          <p className="mt-1 max-w-xl text-sm text-slate-muted">
            Send an in-app message to one person or everyone in this
            organization. Recipients see it in the inbox bell.
          </p>
        </div>
        <Link
          href="/admin/users"
          className="text-xs font-medium text-slate-muted hover:text-ink"
        >
          Manage users
        </Link>
      </header>

      <section className="rounded-xl border border-line bg-raised/40 p-4">
        <div className="grid gap-3">
          <div>
            <Label>Audience</Label>
            <div className="mt-1.5 flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() => setAudience("all")}
                className={`rounded-md border px-3 py-1.5 text-xs font-medium ${
                  audience === "all"
                    ? "border-ink bg-ink text-canvas"
                    : "border-line bg-raised text-slate-muted hover:text-ink"
                }`}
              >
                All active users ({selectable.length})
              </button>
              <button
                type="button"
                onClick={() => setAudience("user")}
                className={`rounded-md border px-3 py-1.5 text-xs font-medium ${
                  audience === "user"
                    ? "border-ink bg-ink text-canvas"
                    : "border-line bg-raised text-slate-muted hover:text-ink"
                }`}
              >
                One user
              </button>
            </div>
            {selectable.length === 0 ? (
              <p className="mt-2 text-xs text-amber">
                No active members in this organization. Entering as platform
                admin does not count —{" "}
                <Link href="/admin/users" className="font-semibold underline">
                  create a user
                </Link>{" "}
                first.
              </p>
            ) : null}
          </div>

          {audience === "user" ? (
            <div>
              <Label htmlFor="notify-user">User</Label>
              <SearchableSelect
                id="notify-user"
                value={userId}
                onChange={setUserId}
                disabled={selectable.length === 0}
                placeholder={
                  selectable.length === 0 ? "No active users" : "Search users…"
                }
                emptyMessage="No matching users"
                options={selectable.map((user) => ({
                  value: user.userId,
                  label: `${user.displayName}${user.email ? ` · ${user.email}` : ""}`,
                }))}
              />
            </div>
          ) : null}

          <div>
            <Label htmlFor="notify-title">Title</Label>
            <Input
              id="notify-title"
              value={title}
              maxLength={200}
              placeholder="Maintenance window"
              onChange={(event) => setTitle(event.target.value)}
            />
          </div>
          <div>
            <Label htmlFor="notify-body">Message</Label>
            <Textarea
              id="notify-body"
              value={body}
              maxLength={4000}
              rows={4}
              placeholder="Write a short message…"
              onChange={(event) => setBody(event.target.value)}
            />
          </div>

          {error ? (
            <p className="text-sm text-amber">{error}</p>
          ) : null}
          {banner ? (
            <p className="rounded-md border border-teal/30 bg-teal/10 px-3 py-1.5 text-sm">
              {banner}
            </p>
          ) : null}

          <div>
            <Button
              variant="accent"
              size="sm"
              onClick={() => void onSend()}
              disabled={busy || selectable.length === 0}
            >
              {busy ? "Sending…" : "Send notification"}
            </Button>
          </div>
        </div>
      </section>

      <section className="rounded-xl border border-line bg-raised/40 p-4">
        <h2 className="text-sm font-semibold">Recent sends</h2>
        {sent.length === 0 ? (
          <p className="mt-2 text-sm text-slate-muted">No notifications sent yet.</p>
        ) : (
          <ul className="mt-3 divide-y divide-line/70">
            {sent.map((row) => (
              <li key={row.batchId} className="py-3">
                <div className="flex flex-wrap items-center gap-2">
                  <p className="font-medium text-ink">{row.title}</p>
                  <Badge tone={row.audience === "all" ? "info" : "neutral"}>
                    {row.audience === "all" ? "all users" : "one user"}
                  </Badge>
                  <span className="text-xs text-slate-muted">
                    {row.recipientCount} recipient
                    {row.recipientCount === 1 ? "" : "s"} ·{" "}
                    {formatRelative(row.createdAt)}
                  </span>
                </div>
                <p className="mt-1 whitespace-pre-wrap text-sm text-slate-muted">
                  {row.body}
                </p>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
