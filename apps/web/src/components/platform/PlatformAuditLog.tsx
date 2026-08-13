"use client";

import { useSession } from "next-auth/react";
import { useLocale } from "next-intl";
import { Fragment, useState, type ReactNode } from "react";

import { ChevronDownIcon } from "@/components/ui/icons";
import type { PlatformAuditEvent, PlatformTenant } from "@/lib/api/types";
import {
  AUDIT_VISIBLE_ROWS,
  describeAuditTenant,
  formatAuditAbsolute,
  isEmptyAuditDetails,
  prettyAuditDetails,
  resolveAuditActor,
  shortActorId,
} from "@/lib/platform/audit-display";
import { cn, formatRelative } from "@/lib/utils";

function AuditDetailField({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <div className="min-w-0">
      <p className="th-label">{label}</p>
      <div className="mt-1 text-sm text-ink">{children}</div>
    </div>
  );
}

export function PlatformAuditLog({
  events,
  tenants,
}: {
  events: PlatformAuditEvent[];
  tenants: PlatformTenant[];
}) {
  const locale = useLocale();
  const { data: session } = useSession();
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const viewer = session?.user;

  function toggle(id: string) {
    setExpandedId((current) => (current === id ? null : id));
  }

  if (events.length === 0) {
    return (
      <p className="px-3 py-6 text-center text-sm text-slate-muted">
        No platform changes have been recorded yet.
      </p>
    );
  }

  return (
    <div
      className="overlay-y-auto"
      style={{ maxHeight: `calc(2.25rem * ${AUDIT_VISIBLE_ROWS + 1})` }}
    >
      <table className="min-w-full text-left text-sm">
        <thead className="sticky top-0 z-10 border-b border-line/70 bg-raised text-[10px] uppercase tracking-[0.12em] text-slate-muted">
          <tr className="h-9">
            <th className="w-8 px-2 py-0 font-medium">
              <span className="sr-only">Detail</span>
            </th>
            <th className="px-3 py-0 font-medium">Action</th>
            <th className="px-3 py-0 font-medium">Actor</th>
            <th className="px-3 py-0 font-medium">Tenant</th>
            <th className="px-3 py-0 text-right font-medium">When</th>
          </tr>
        </thead>
        <tbody>
          {events.map((event) => {
            const open = expandedId === event.id;
            const tenant = describeAuditTenant(event, tenants);
            const actor = resolveAuditActor(event, viewer);
            const actorSummary =
              actor.label === event.actorId
                ? shortActorId(event.actorId)
                : actor.label;
            return (
              <Fragment key={event.id}>
                <tr
                  className={cn(
                    "h-9 cursor-pointer border-b border-line/50 last:border-0",
                    open && "border-b-0 bg-mist/40",
                  )}
                  onClick={() => toggle(event.id)}
                >
                  <td className="px-2 py-0">
                    <button
                      type="button"
                      className="inline-flex h-7 w-7 items-center justify-center rounded-md text-slate-muted hover:bg-fog/70 hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ink/20"
                      aria-expanded={open}
                      aria-controls={`platform-audit-${event.id}`}
                      aria-label={
                        open ? "Hide event detail" : "Show event detail"
                      }
                      onClick={(click) => {
                        click.stopPropagation();
                        toggle(event.id);
                      }}
                    >
                      <ChevronDownIcon
                        className={cn(
                          "h-3.5 w-3.5 transition-transform",
                          open && "rotate-180",
                        )}
                      />
                    </button>
                  </td>
                  <td className="max-w-[240px] truncate px-3 py-0 font-medium">
                    {event.action}
                  </td>
                  <td
                    className={cn(
                      "max-w-[160px] truncate px-3 py-0 text-slate-muted",
                      actor.label === event.actorId && "mono-cell",
                    )}
                    title={actor.email || event.actorId}
                  >
                    {actorSummary}
                  </td>
                  <td
                    className="max-w-[160px] truncate px-3 py-0 text-slate-muted"
                    title={tenant.slug ?? tenant.id ?? undefined}
                  >
                    {tenant.name ?? tenant.slug ?? "—"}
                  </td>
                  <td className="mono-cell whitespace-nowrap px-3 py-0 text-right text-slate-muted">
                    {formatRelative(event.createdAt)}
                  </td>
                </tr>
                {open ? (
                  <tr className="border-b border-line/50 last:border-0">
                    <td colSpan={5} className="px-3 pb-3 pt-0">
                      <div
                        id={`platform-audit-${event.id}`}
                        className="rounded-lg border border-line bg-canvas/70 px-3 py-3"
                      >
                        <div className="grid gap-3 sm:grid-cols-2">
                          <AuditDetailField label="Action">
                            <p className="break-all font-medium">
                              {event.action}
                            </p>
                          </AuditDetailField>
                          <AuditDetailField label="When">
                            <p>
                              {formatAuditAbsolute(event.createdAt, locale)}
                            </p>
                            <p className="mt-0.5 text-xs text-slate-muted">
                              {formatRelative(event.createdAt)}
                            </p>
                          </AuditDetailField>
                          <AuditDetailField label="Actor">
                            {actor.name ? (
                              <p className="font-medium">{actor.name}</p>
                            ) : null}
                            {actor.email ? (
                              <p className="text-slate-muted">{actor.email}</p>
                            ) : null}
                            <p className="mono-cell break-all text-slate-muted">
                              {event.actorId}
                            </p>
                          </AuditDetailField>
                          <AuditDetailField label="Tenant">
                            {tenant.name || tenant.slug || tenant.id ? (
                              <>
                                {tenant.name ? (
                                  <p className="font-medium">{tenant.name}</p>
                                ) : null}
                                {tenant.slug ? (
                                  <p className="text-slate-muted">
                                    /{tenant.slug}
                                  </p>
                                ) : null}
                                {tenant.id ? (
                                  <p className="mono-cell break-all text-slate-muted">
                                    {tenant.id}
                                  </p>
                                ) : null}
                              </>
                            ) : (
                              <p className="text-slate-muted">—</p>
                            )}
                          </AuditDetailField>
                        </div>
                        <div className="mt-3">
                          <p className="th-label">Payload</p>
                          {isEmptyAuditDetails(event.details) ? (
                            <p className="mt-1 text-sm text-slate-muted">
                              No extra detail for this event.
                            </p>
                          ) : (
                            <pre className="overlay-y-auto mt-1 max-h-48 whitespace-pre-wrap break-all rounded-md border border-line bg-fog/50 p-2.5 font-mono text-[12px] leading-relaxed text-ink">
                              {prettyAuditDetails(event.details)}
                            </pre>
                          )}
                        </div>
                      </div>
                    </td>
                  </tr>
                ) : null}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
