"use client";

import { useState } from "react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Input, Label } from "@/components/ui/Field";
import {
  createPlatformTenant,
  enterPlatformTenant,
  setPlatformTenantActive,
} from "@/lib/api/admin";
import type {
  PlatformAuditEvent,
  PlatformTenant,
} from "@/lib/api/types";
import {
  PLATFORM_TENANT_COOKIE,
  PLATFORM_TENANT_NAME_COOKIE,
} from "@/lib/auth/access-context";
import { useAgentOsToken } from "@/lib/auth/token";
import { formatRelative } from "@/lib/utils";
import { slugifyName } from "@/lib/validation/agent-form";

export function PlatformTenantsPanel({
  initialTenants,
  initialAudit,
}: {
  initialTenants: PlatformTenant[];
  initialAudit: PlatformAuditEvent[];
}) {
  const [tenants, setTenants] = useState(initialTenants);
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [clerkOrgId, setClerkOrgId] = useState("");
  const [busyId, setBusyId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { getAccessToken } = useAgentOsToken();

  async function createTenant() {
    if (!name.trim() || !clerkOrgId.trim()) return;
    setCreating(true);
    setError(null);
    try {
      const created = await createPlatformTenant(await getAccessToken(), {
        name: name.trim(),
        slug: slugifyName(slug || name),
        clerkOrgId: clerkOrgId.trim(),
      });
      setTenants((current) =>
        [...current, created].sort((a, b) => a.name.localeCompare(b.name)),
      );
      setName("");
      setSlug("");
      setClerkOrgId("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Tenant creation failed");
    } finally {
      setCreating(false);
    }
  }

  async function toggleTenant(tenant: PlatformTenant) {
    const action = tenant.isActive ? "suspend" : "reactivate";
    if (
      tenant.isActive &&
      !window.confirm(
        `Suspend ${tenant.name}? Its users and platform-admin tenant access will be blocked.`,
      )
    ) {
      return;
    }
    setBusyId(tenant.id);
    setError(null);
    try {
      const updated = await setPlatformTenantActive(
        await getAccessToken(),
        tenant.id,
        !tenant.isActive,
      );
      setTenants((current) =>
        current.map((item) => (item.id === updated.id ? updated : item)),
      );
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : `Failed to ${action} tenant`,
      );
    } finally {
      setBusyId(null);
    }
  }

  async function workInTenant(tenant: PlatformTenant) {
    setBusyId(tenant.id);
    setError(null);
    try {
      await enterPlatformTenant(await getAccessToken(), tenant.id);
      document.cookie = `${PLATFORM_TENANT_COOKIE}=${encodeURIComponent(tenant.id)}; Path=/; SameSite=Lax`;
      document.cookie = `${PLATFORM_TENANT_NAME_COOKIE}=${encodeURIComponent(tenant.name)}; Path=/; SameSite=Lax`;
      window.location.assign("/admin/agents");
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Could not enter tenant workspace",
      );
      setBusyId(null);
    }
  }

  return (
    <div className="space-y-8">
      <section className="surface-panel relative overflow-hidden rounded-2xl p-6 md:p-8">
        <div className="pointer-events-none absolute inset-0 grid-noise opacity-60" />
        <div className="relative grid gap-6 lg:grid-cols-[1.2fr_1fr] lg:items-end">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.16em] text-teal">
              Platform control
            </p>
            <h1 className="mt-2 font-display text-4xl font-semibold tracking-tight">
              Super admin
            </h1>
            <p className="mt-3 max-w-xl text-sm leading-relaxed text-slate-muted">
              Provision tenants, control access, and enter a tenant workspace
              without weakening its database isolation.
            </p>
          </div>
          <div className="rounded-xl border border-line bg-raised/90 p-4">
            <p className="mb-3 text-sm font-semibold">Provision tenant</p>
            <div className="grid gap-3 sm:grid-cols-2">
              <div>
                <Label htmlFor="tenant-name">Name</Label>
                <Input
                  id="tenant-name"
                  value={name}
                  placeholder="Acme Corp"
                  onChange={(event) => {
                    setName(event.target.value);
                    if (!slug) setSlug(slugifyName(event.target.value));
                  }}
                />
              </div>
              <div>
                <Label htmlFor="tenant-slug">Slug</Label>
                <Input
                  id="tenant-slug"
                  value={slug}
                  placeholder="acme"
                  onChange={(event) => setSlug(slugifyName(event.target.value))}
                />
              </div>
              <div className="sm:col-span-2">
                <Label htmlFor="clerk-org" hint="From Clerk Dashboard">
                  Clerk organization ID
                </Label>
                <Input
                  id="clerk-org"
                  value={clerkOrgId}
                  placeholder="org_..."
                  onChange={(event) => setClerkOrgId(event.target.value)}
                />
              </div>
            </div>
            <Button
              className="mt-3 w-full"
              variant="accent"
              disabled={creating || !name.trim() || !clerkOrgId.trim()}
              onClick={createTenant}
            >
              {creating ? "Provisioning…" : "Provision tenant"}
            </Button>
          </div>
        </div>
      </section>

      {error ? (
        <p className="rounded-md border border-rose/30 bg-rose/10 px-3 py-2 text-sm text-rose">
          {error}
        </p>
      ) : null}

      <section>
        <div className="mb-3 flex items-end justify-between gap-4">
          <div>
            <h2 className="font-display text-2xl font-semibold">Tenants</h2>
            <p className="text-sm text-slate-muted">
              {tenants.length} provisioned organization
              {tenants.length === 1 ? "" : "s"}
            </p>
          </div>
        </div>
        <div className="table-shell overflow-x-auto rounded-xl">
          <div className="min-w-[760px]">
            <div className="grid grid-cols-[1.3fr_1fr_0.7fr_1fr] gap-3 border-b border-line px-4 py-2.5">
              <span className="th-label">Tenant</span>
              <span className="th-label">Clerk organization</span>
              <span className="th-label">Status</span>
              <span className="th-label text-right">Actions</span>
            </div>
            {tenants.map((tenant) => (
              <div
                key={tenant.id}
                className="grid grid-cols-[1.3fr_1fr_0.7fr_1fr] items-center gap-3 border-b border-line/60 px-4 py-3 last:border-0"
              >
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium">{tenant.name}</p>
                  <p className="mono-cell truncate text-slate-muted">
                    /{tenant.slug}
                  </p>
                </div>
                <p className="mono-cell truncate text-slate-muted">
                  {tenant.clerkOrgId}
                </p>
                <Badge dot tone={tenant.isActive ? "success" : "danger"}>
                  {tenant.isActive ? "Active" : "Suspended"}
                </Badge>
                <div className="flex justify-end gap-2">
                  <Button
                    size="sm"
                    variant="secondary"
                    disabled={!tenant.isActive || busyId === tenant.id}
                    onClick={() => workInTenant(tenant)}
                  >
                    Open workspace
                  </Button>
                  <Button
                    size="sm"
                    variant={tenant.isActive ? "danger" : "secondary"}
                    disabled={busyId === tenant.id}
                    onClick={() => toggleTenant(tenant)}
                  >
                    {tenant.isActive ? "Suspend" : "Reactivate"}
                  </Button>
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section>
        <h2 className="font-display text-2xl font-semibold">Platform audit</h2>
        <p className="mb-3 text-sm text-slate-muted">
          Recent tenant provisioning and access-control changes.
        </p>
        <div className="table-shell rounded-xl">
          {initialAudit.map((event) => (
            <div
              key={event.id}
              className="flex flex-wrap items-center justify-between gap-3 border-b border-line/60 px-4 py-3 last:border-0"
            >
              <div>
                <p className="text-sm font-medium">{event.action}</p>
                <p className="mono-cell text-slate-muted">{event.actorId}</p>
              </div>
              <p className="mono-cell text-slate-muted">
                {formatRelative(event.createdAt)}
              </p>
            </div>
          ))}
          {initialAudit.length === 0 ? (
            <p className="px-4 py-8 text-center text-sm text-slate-muted">
              No platform changes have been recorded yet.
            </p>
          ) : null}
        </div>
      </section>
    </div>
  );
}
