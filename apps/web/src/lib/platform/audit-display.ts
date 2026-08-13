import type { PlatformAuditEvent, PlatformTenant } from "@/lib/api/types";

export const AUDIT_VISIBLE_ROWS = 15;

export type AuditViewer = {
  id?: string | null;
  email?: string | null;
  name?: string | null;
};

export function isEmptyAuditDetails(
  details: Record<string, unknown> | null | undefined,
): boolean {
  return !details || Object.keys(details).length === 0;
}

export function prettyAuditDetails(
  details: Record<string, unknown> | null | undefined,
): string {
  try {
    return JSON.stringify(details ?? {}, null, 2);
  } catch {
    return String(details);
  }
}

export function resolveAuditActor(
  event: PlatformAuditEvent,
  viewer?: AuditViewer | null,
): { label: string; email: string | null; name: string | null } {
  let name = event.actorName?.trim() || null;
  let email = event.actorEmail?.trim() || null;
  if ((!name || !email) && viewer?.id && viewer.id === event.actorId) {
    name = name || viewer.name?.trim() || null;
    email = email || viewer.email?.trim() || null;
  }
  if (name && email && name !== email) return { label: name, email, name };
  if (name) return { label: name, email, name };
  if (email) return { label: email, email, name };
  return { label: event.actorId, email: null, name: null };
}

export function shortActorId(actorId: string): string {
  if (actorId.length > 18) {
    return `${actorId.slice(0, 10)}…${actorId.slice(-4)}`;
  }
  return actorId;
}

export function formatAuditAbsolute(iso: string, locale?: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  try {
    return new Intl.DateTimeFormat(locale || undefined, {
      dateStyle: "medium",
      timeStyle: "medium",
    }).format(date);
  } catch {
    return date.toISOString();
  }
}

export function describeAuditTenant(
  event: PlatformAuditEvent,
  tenants: PlatformTenant[],
): { name: string | null; slug: string | null; id: string | null } {
  if (!event.tenantId) {
    return { name: null, slug: null, id: null };
  }
  const tenant = tenants.find((row) => row.id === event.tenantId);
  return {
    name: tenant?.name ?? null,
    slug: tenant?.slug ?? null,
    id: event.tenantId,
  };
}
