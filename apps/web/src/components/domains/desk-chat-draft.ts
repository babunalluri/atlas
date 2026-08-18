import type { UserNotification } from "@/lib/api/types";

/** Text to paste into desk chat from an inbox notification (body only). */
export function formatNotificationForDesk(item: Pick<UserNotification, "title" | "body">): string {
  const body = item.body.trim();
  if (body) return body;
  return item.title.trim();
}
