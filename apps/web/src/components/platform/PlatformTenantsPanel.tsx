"use client";

import { useEffect, useMemo, useState } from "react";

import { PlatformGrantCreditsPanel } from "@/components/platform/PlatformGrantCreditsPanel";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Input, Label } from "@/components/ui/Field";
import { SearchableSelect } from "@/components/ui/SearchableSelect";
import {
  TimezoneSelect,
  browserTimezone,
} from "@/components/ui/TimezoneSelect";
import {
  createPlatformTenant,
  enterPlatformTenant,
  importPlatformTenantResources,
  listPlatformAudit,
  listPlatformTenantCatalog,
  setPlatformTenantActive,
  type PlatformCatalogItem,
  type PlatformImportResult,
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
  const [audit, setAudit] = useState(initialAudit);
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [authOrgId, setAuthOrgId] = useState("");
  const [timezone, setTimezone] = useState(browserTimezone);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { getAccessToken } = useAgentOsToken();

  const [sourceTenantId, setSourceTenantId] = useState("");
  const [destTenantId, setDestTenantId] = useState("");
  const [catalog, setCatalog] = useState<PlatformCatalogItem[]>([]);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [catalogLoading, setCatalogLoading] = useState(false);
  const [importing, setImporting] = useState(false);
  const [importResult, setImportResult] = useState<PlatformImportResult | null>(
    null,
  );

  const activeTenants = useMemo(
    () => tenants.filter((tenant) => tenant.isActive),
    [tenants],
  );

  useEffect(() => {
    if (!sourceTenantId) {
      setCatalog([]);
      setSelectedIds([]);
      return;
    }
    let cancelled = false;
    setCatalogLoading(true);
    setImportResult(null);
    void (async () => {
      try {
        const rows = await listPlatformTenantCatalog(
          await getAccessToken(),
          sourceTenantId,
        );
        if (!cancelled) {
          setCatalog(rows);
          setSelectedIds([]);
          setError(null);
        }
      } catch (reason) {
        if (!cancelled) {
          setCatalog([]);
          setError(
            reason instanceof Error
              ? reason.message
              : "Could not load source catalog",
          );
        }
      } finally {
        if (!cancelled) setCatalogLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [getAccessToken, sourceTenantId]);

  async function createTenant() {
    if (!name.trim() || !authOrgId.trim()) return;
    setCreating(true);
    setError(null);
    try {
      const created = await createPlatformTenant(await getAccessToken(), {
        name: name.trim(),
        slug: slugifyName(slug || name),
        authOrgId: authOrgId.trim(),
        timezone,
      });
      setTenants((current) =>
        [...current, created].sort((a, b) => a.name.localeCompare(b.name)),
      );
      setName("");
      setSlug("");
      setAuthOrgId("");
      setTimezone(browserTimezone);
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
        current.map((row) => (row.id === updated.id ? updated : row)),
      );
      setAudit(await listPlatformAudit(await getAccessToken()));
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : `Could not ${action} tenant`,
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
      document.cookie = `${PLATFORM_TENANT_COOKIE}=${tenant.id}; Path=/; SameSite=Lax`;
      document.cookie = `${PLATFORM_TENANT_NAME_COOKIE}=${encodeURIComponent(tenant.name)}; Path=/; SameSite=Lax`;
      window.location.assign("/admin/agents");
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Could not enter tenant workspace",
      );
      setBusyId(null);
    }
  }

  function toggleCatalogItem(id: string) {
    setSelectedIds((current) =>
      current.includes(id)
        ? current.filter((item) => item !== id)
        : [...current, id],
    );
  }

  async function runImport() {
    if (!sourceTenantId || !destTenantId || selectedIds.length === 0) return;
    setImporting(true);
    setError(null);
    setImportResult(null);
    try {
      const teamIds = selectedIds.filter((id) =>
        catalog.some((item) => item.id === id && item.kind === "team"),
      );
      const workflowIds = selectedIds.filter((id) =>
        catalog.some((item) => item.id === id && item.kind === "workflow"),
      );
      const result = await importPlatformTenantResources(await getAccessToken(), {
        sourceTenantId,
        destinationTenantId: destTenantId,
        teamIds,
        workflowIds,
      });
      setImportResult(result);
      setAudit(await listPlatformAudit(await getAccessToken()));
      setSelectedIds([]);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Import failed");
    } finally {
      setImporting(false);
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
                <Label htmlFor="org-id" hint="Must match the signed-in organization id">
                  Organization ID
                </Label>
                <Input
                  id="org-id"
                  value={authOrgId}
                  placeholder="org_..."
                  onChange={(event) => setAuthOrgId(event.target.value)}
                />
              </div>
              <div className="sm:col-span-2">
                <TimezoneSelect
                  id="tenant-timezone"
                  value={timezone}
                  onChange={setTimezone}
                  hint="Default for traces and new users"
                />
              </div>
            </div>
            <Button
              className="mt-3 w-full"
              variant="accent"
              disabled={creating || !name.trim() || !authOrgId.trim()}
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
            <div className="grid grid-cols-[1.3fr_1fr_0.7fr_0.7fr_1fr] gap-3 border-b border-line px-4 py-2.5">
              <span className="th-label">Tenant</span>
              <span className="th-label">Organization ID</span>
              <span className="th-label">Timezone</span>
              <span className="th-label">Status</span>
              <span className="th-label text-right">Actions</span>
            </div>
            {tenants.map((tenant) => (
              <div
                key={tenant.id}
                className="grid grid-cols-[1.3fr_1fr_0.7fr_0.7fr_1fr] items-center gap-3 border-b border-line/60 px-4 py-3 last:border-0"
              >
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium">{tenant.name}</p>
                  <p className="mono-cell truncate text-slate-muted">
                    /{tenant.slug}
                  </p>
                </div>
                <p className="mono-cell truncate text-slate-muted">
                  {tenant.authOrgId}
                </p>
                <p className="mono-cell truncate text-slate-muted">
                  {tenant.timezone || "UTC"}
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

      <PlatformGrantCreditsPanel tenants={tenants} />

      <section className="rounded-xl border border-line bg-raised/40 p-5">
        <h2 className="font-display text-2xl font-semibold">
          Import across tenants
        </h2>
        <p className="mt-1 max-w-2xl text-sm text-slate-muted">
          Copy teams and/or workflows into another tenant, including agents,
          tools, and knowledge metadata. Credentials are never copied. Results
          land as drafts.
        </p>
        <div className="mt-4 grid gap-3 md:grid-cols-2">
          <div>
            <Label htmlFor="import-source">Source tenant</Label>
            <SearchableSelect
              id="import-source"
              value={sourceTenantId}
              onChange={setSourceTenantId}
              placeholder="Select source…"
              emptyMessage="No matching tenants"
              options={activeTenants.map((tenant) => ({
                value: tenant.id,
                label: tenant.name,
              }))}
            />
          </div>
          <div>
            <Label htmlFor="import-dest">Destination tenant</Label>
            <SearchableSelect
              id="import-dest"
              value={destTenantId}
              onChange={setDestTenantId}
              placeholder="Select destination…"
              emptyMessage="No matching tenants"
              options={activeTenants
                .filter((tenant) => tenant.id !== sourceTenantId)
                .map((tenant) => ({
                  value: tenant.id,
                  label: tenant.name,
                }))}
            />
          </div>
        </div>

        <div className="mt-4">
          <p className="th-label mb-2">Teams and workflows</p>
          {!sourceTenantId ? (
            <p className="text-sm text-slate-muted">
              Choose a source tenant to load its catalog.
            </p>
          ) : catalogLoading ? (
            <p className="text-sm text-slate-muted">Loading catalog…</p>
          ) : catalog.length === 0 ? (
            <p className="text-sm text-slate-muted">
              No teams or workflows in this tenant yet.
            </p>
          ) : (
            <div className="max-h-64 space-y-2 overflow-y-auto rounded-lg border border-line bg-canvas/40 p-3">
              {catalog.map((item) => {
                const checked = selectedIds.includes(item.id);
                return (
                  <label
                    key={item.id}
                    className="flex cursor-pointer items-center gap-3 rounded-md px-2 py-1.5 hover:bg-raised/80"
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => toggleCatalogItem(item.id)}
                      className="size-4 accent-teal"
                    />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-medium">
                        {item.name}
                      </span>
                      <span className="mono-cell text-slate-muted">
                        {item.kind} · /{item.slug} · {item.status}
                      </span>
                    </span>
                  </label>
                );
              })}
            </div>
          )}
        </div>

        <Button
          className="mt-4"
          variant="accent"
          disabled={
            importing ||
            !sourceTenantId ||
            !destTenantId ||
            sourceTenantId === destTenantId ||
            selectedIds.length === 0
          }
          onClick={runImport}
        >
          {importing ? "Importing…" : "Import selected"}
        </Button>

        {importResult ? (
          <div className="mt-4 rounded-lg border border-teal/30 bg-teal/10 px-3 py-3 text-sm">
            <p className="font-medium">
              Imported {importResult.counts.workflows ?? 0} workflow(s),{" "}
              {importResult.counts.teams ?? 0} team(s),{" "}
              {importResult.counts.agents ?? 0} agent(s),{" "}
              {importResult.counts.tools ?? 0} tool(s),{" "}
              {importResult.counts.knowledge_bases ?? 0} knowledge base(s).
            </p>
            {importResult.warnings.length > 0 ? (
              <ul className="mt-2 list-disc space-y-1 pl-5 text-slate-muted">
                {importResult.warnings.map((warning) => (
                  <li key={warning}>{warning}</li>
                ))}
              </ul>
            ) : null}
          </div>
        ) : null}
      </section>

      <section>
        <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
          <h2 className="font-display text-xl font-semibold">Platform audit</h2>
          <p className="text-xs text-slate-muted">
            Recent provisioning and access changes
          </p>
        </div>
        <div className="table-shell rounded-xl">
          {audit.length === 0 ? (
            <p className="px-3 py-6 text-center text-sm text-slate-muted">
              No platform changes have been recorded yet.
            </p>
          ) : (
            <div className="max-h-[min(28rem,70vh)] overflow-auto">
            <table className="min-w-full text-left text-sm">
              <thead className="sticky top-0 z-10 border-b border-line/70 bg-raised text-[10px] uppercase tracking-[0.12em] text-slate-muted">
                <tr>
                  <th className="px-3 py-2 font-medium">Action</th>
                  <th className="px-3 py-2 font-medium">Actor</th>
                  <th className="px-3 py-2 font-medium">Tenant</th>
                  <th className="px-3 py-2 font-medium text-right">When</th>
                </tr>
              </thead>
              <tbody>
                {audit.map((event) => {
                  const tenant = event.tenantId
                    ? tenants.find((row) => row.id === event.tenantId)
                    : null;
                  const actorShort =
                    event.actorId.length > 18
                      ? `${event.actorId.slice(0, 10)}…${event.actorId.slice(-4)}`
                      : event.actorId;
                  return (
                    <tr
                      key={event.id}
                      className="border-b border-line/50 last:border-0"
                    >
                      <td className="max-w-[240px] truncate px-3 py-1.5 font-medium">
                        {event.action}
                      </td>
                      <td
                        className="mono-cell max-w-[140px] truncate px-3 py-1.5 text-slate-muted"
                        title={event.actorId}
                      >
                        {actorShort}
                      </td>
                      <td
                        className="max-w-[160px] truncate px-3 py-1.5 text-slate-muted"
                        title={tenant?.slug ?? event.tenantId ?? undefined}
                      >
                        {tenant?.name ?? tenant?.slug ?? "—"}
                      </td>
                      <td className="mono-cell whitespace-nowrap px-3 py-1.5 text-right text-slate-muted">
                        {formatRelative(event.createdAt)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            </div>
          )}
        </div>
      </section>
    </div>
  );
}
