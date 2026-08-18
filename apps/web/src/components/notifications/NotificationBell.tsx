"use client";

import { useTranslations } from "next-intl";
import { useCallback, useEffect, useRef, useState } from "react";

import { formatNotificationForDesk } from "@/components/domains/desk-chat-draft";
import {
  getMyUnreadNotificationCount,
  listMyNotifications,
  markAllNotificationsRead,
  markNotificationRead,
} from "@/lib/api/admin";
import type { UserNotification } from "@/lib/api/types";
import { useAgentOsToken } from "@/lib/auth/token";
import { cn, formatRelative } from "@/lib/utils";

function BellGlyph({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.75"
      className={className}
      aria-hidden
    >
      <path d="M6 9a6 6 0 0 1 12 0c0 7 3 7 3 7H3s3 0 3-7" />
      <path d="M10 19a2 2 0 0 0 4 0" />
    </svg>
  );
}

function CopyGlyph({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.75"
      className={className}
      aria-hidden
    >
      <rect x="9" y="9" width="13" height="13" rx="2" />
      <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
    </svg>
  );
}

/** Inbox bell — loads on mount and when opened (no polling loop). */
export function NotificationBell({ className }: { className?: string }) {
  const t = useTranslations("common");
  const { getAccessToken, isLoaded, isSignedIn } = useAgentOsToken();
  const [open, setOpen] = useState(false);
  const [unread, setUnread] = useState(0);
  const [items, setItems] = useState<UserNotification[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const copiedTimerRef = useRef<number | null>(null);

  const refreshCount = useCallback(async () => {
    if (!isLoaded || !isSignedIn) return;
    try {
      const token = await getAccessToken();
      setUnread(await getMyUnreadNotificationCount(token));
    } catch {
      // Bell is best-effort; ignore auth race on first paint.
    }
  }, [getAccessToken, isLoaded, isSignedIn]);

  const refreshList = useCallback(async () => {
    if (!isLoaded || !isSignedIn) return;
    setLoading(true);
    setError(null);
    try {
      const token = await getAccessToken();
      const [rows, count] = await Promise.all([
        listMyNotifications(token, { limit: 30 }),
        getMyUnreadNotificationCount(token),
      ]);
      setItems(rows);
      setUnread(count);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  }, [getAccessToken, isLoaded, isSignedIn]);

  useEffect(() => {
    void refreshCount();
  }, [refreshCount]);

  useEffect(() => {
    if (!open) return;
    void refreshList();
  }, [open, refreshList]);

  useEffect(() => {
    if (!open) return;
    function onPointerDown(event: MouseEvent) {
      if (!rootRef.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  useEffect(() => {
    return () => {
      if (copiedTimerRef.current != null) {
        window.clearTimeout(copiedTimerRef.current);
      }
    };
  }, []);

  if (!isLoaded || !isSignedIn) {
    return null;
  }

  async function copyNotification(item: UserNotification) {
    const text = formatNotificationForDesk(item);
    try {
      await navigator.clipboard.writeText(text);
      setCopiedId(item.id);
      if (copiedTimerRef.current != null) {
        window.clearTimeout(copiedTimerRef.current);
      }
      copiedTimerRef.current = window.setTimeout(() => {
        setCopiedId((current) => (current === item.id ? null : current));
      }, 2000);
    } catch {
      setError(t("notifications.copyFailed"));
    }
  }

  async function onRead(id: string) {
    const wasUnread = items.some((row) => row.id === id && !row.readAt);
    try {
      const token = await getAccessToken();
      const updated = await markNotificationRead(token, id);
      setItems((current) =>
        current.map((row) => (row.id === id ? updated : row)),
      );
      if (wasUnread) {
        setUnread((count) => Math.max(0, count - 1));
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Update failed");
    }
  }

  async function onReadAll() {
    try {
      const token = await getAccessToken();
      await markAllNotificationsRead(token);
      setItems((current) =>
        current.map((row) => ({
          ...row,
          readAt: row.readAt ?? new Date().toISOString(),
        })),
      );
      setUnread(0);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Update failed");
    }
  }

  return (
    <div ref={rootRef} className={cn("relative", className)}>
      <button
        type="button"
        aria-label={
          unread > 0
            ? `Notifications, ${unread} unread`
            : "Notifications"
        }
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        className="relative inline-flex size-8 items-center justify-center rounded-md border border-line bg-raised/70 text-slate-muted transition hover:border-line-strong hover:text-ink"
      >
        <BellGlyph className="size-4" />
        {unread > 0 ? (
          <span className="absolute -right-1 -top-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-teal px-1 text-[10px] font-semibold text-canvas">
            {unread > 99 ? "99+" : unread}
          </span>
        ) : null}
      </button>

      {open ? (
        <div className="absolute right-0 z-50 mt-2 w-[min(22rem,calc(100vw-2rem))] overflow-hidden rounded-xl border border-line bg-canvas shadow-lg">
          <div className="flex items-center justify-between border-b border-line px-3 py-2">
            <p className="text-xs font-semibold uppercase tracking-[0.12em] text-slate-muted">
              Inbox
            </p>
            {unread > 0 ? (
              <button
                type="button"
                onClick={() => void onReadAll()}
                className="text-[11px] font-medium text-teal hover:underline"
              >
                Mark all read
              </button>
            ) : null}
          </div>
          <div className="max-h-80 overflow-y-auto">
            {loading ? (
              <p className="px-3 py-6 text-center text-sm text-slate-muted">
                Loading…
              </p>
            ) : error ? (
              <p className="px-3 py-4 text-sm text-amber">{error}</p>
            ) : items.length === 0 ? (
              <p className="px-3 py-6 text-center text-sm text-slate-muted">
                No notifications yet.
              </p>
            ) : (
              <ul className="divide-y divide-line/70">
                {items.map((item) => {
                  const unreadItem = !item.readAt;
                  return (
                    <li key={item.id}>
                      <div
                        className={cn(
                          "flex gap-2 px-3 py-2.5 transition hover:bg-mist/60",
                          unreadItem && "bg-teal/5",
                        )}
                      >
                        <button
                          type="button"
                          onClick={() => {
                            if (unreadItem) void onRead(item.id);
                          }}
                          className="min-w-0 flex-1 text-left"
                        >
                          <div className="flex items-start justify-between gap-2">
                            <p className="text-sm font-medium text-ink">
                              {item.title}
                            </p>
                            {unreadItem ? (
                              <span className="mt-1 size-1.5 shrink-0 rounded-full bg-teal" />
                            ) : null}
                          </div>
                          <p className="mt-0.5 line-clamp-3 whitespace-pre-wrap text-xs text-slate-muted">
                            {item.body}
                          </p>
                          <p className="mt-1 text-[10px] uppercase tracking-[0.1em] text-slate-muted">
                            {formatRelative(item.createdAt)}
                          </p>
                        </button>
                        <button
                          type="button"
                          title={
                            copiedId === item.id
                              ? t("notifications.copied")
                              : t("notifications.copy")
                          }
                          aria-label={
                            copiedId === item.id
                              ? t("notifications.copied")
                              : t("notifications.copy")
                          }
                          onClick={(event) => {
                            event.stopPropagation();
                            void copyNotification(item);
                          }}
                          className={cn(
                            "mt-0.5 inline-flex size-7 shrink-0 items-center justify-center rounded-md border border-line text-slate-muted transition hover:border-line-strong hover:bg-raised/80 hover:text-ink",
                            copiedId === item.id && "border-teal/40 text-teal",
                          )}
                        >
                          <CopyGlyph className="size-3.5" />
                        </button>
                      </div>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        </div>
      ) : null}
    </div>
  );
}
