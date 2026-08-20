"use client";

import { useLocale } from "next-intl";
import { useEffect, useMemo, useState } from "react";

import { PlatformAuditLog } from "@/components/platform/PlatformAuditLog";
import { PlatformGrantCreditsPanel } from "@/components/platform/PlatformGrantCreditsPanel";
import { EditTenantDialog } from "@/components/platform/EditTenantDialog";
import { ProvisionTenantDialog } from "@/components/platform/ProvisionTenantDialog";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Input, Label } from "@/components/ui/Field";
import {
  ChevronLeftIcon,
  ChevronRightIcon,
  ExternalLinkIcon,
  PauseIcon,
  PencilIcon,
  PlayIcon,
  PlusIcon,
  UploadIcon,
} from "@/components/ui/icons";
import { SearchableSelect } from "@/components/ui/SearchableSelect";
import { ORG_ADMIN_HREF } from "@/lib/auth/desk-admin";
import { localePrefixedPath } from "@/lib/auth/post-login";
import {
  enterPlatformTenant,
  importPlatformTenantResources,
  listPlatformAudit,
  listPlatformTenantCatalog,
  setPlatformTenantActive,
  type PlatformCatalogItem,
  type PlatformImportResult,
} from "@/lib/api/admin";
import type { PlatformAuditEvent, PlatformTenant } from "@/lib/api/types";
import {
  PLATFORM_TENANT_COOKIE,
  PLATFORM_TENANT_NAME_COOKIE,
} from "@/lib/auth/access-context";
import { useAgentOsToken } from "@/lib/auth/token";
import { groupCatalogItems } from "@/lib/catalog/domain-groups";
import { cn } from "@/lib/utils";

type StatusFilter = "all" | "active" | "suspended";

const PAGE_SIZE = 15;

export function PlatformTenantsPanel({
  initialTenants,
  initialAudit,
}: {
  initialTenants: PlatformTenant[];
  initialAudit: PlatformAuditEvent[];
}) {
  const [tenants, setTenants] = useState(initialTenants);
  const [audit, setAudit] = useState(initialAudit);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [provisioning, setProvisioning] = useState(false);
  const [editingTenant, setEditingTenant] = useState<PlatformTenant | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { getAccessToken } = useAgentOsToken();
  const locale = useLocale();

  const [status, setStatus] = useState<StatusFilter>("all");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
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
  const filteredTenants = useMemo(() => {
    const query = q.trim().toLowerCase();
    return tenants.filter((tenant) => {
      if (status === "active" && !tenant.isActive) return false;
      if (status === "suspended" && tenant.isActive) return false;
      if (!query) return true;
      const haystack = [
        tenant.name,
        tenant.slug,
        tenant.authOrgId,
        tenant.ownerEmail ?? "",
      ]
        .join(" ")
        .toLowerCase();
      return haystack.includes(query);
    });
  }, [q, status, tenants]);

  const totalPages = Math.max(1, Math.ceil(filteredTenants.length / PAGE_SIZE));
  const safePage = Math.min(page, totalPages);
  const start = (safePage - 1) * PAGE_SIZE;
  const pageItems = filteredTenants.slice(start, start + PAGE_SIZE);
  const end = Math.min(filteredTenants.length, start + PAGE_SIZE);

  const groupedCatalog = useMemo(
    () => groupCatalogItems(catalog),
    [catalog],
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

  async function refreshAudit() {
    try {
      setAudit(await listPlatformAudit(await getAccessToken()));
    } catch {
      /* keep current audit if refresh fails */
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
      window.location.assign(localePrefixedPath(locale, ORG_ADMIN_HREF));
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
    <>
      <div className="space-y-5">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-display text-2xl font-semibold tracking-tight">
            Tenants
          </h1>
          <p className="mt-0.5 text-sm text-slate-muted">
            Provision organizations with an owner email and password. Atlas
            creates the organization and owner account.
          </p>
        </div>
        <Button variant="accent" icon={<PlusIcon />} onClick={() => setProvisioning(true)}>
          Provision tenant
        </Button>
      </header>

      {error ? <p className="text-sm text-rose">{error}</p> : null}

      <section>
        <div className="table-shell overlay-x-auto rounded-xl">
          <div className="space-y-3 border-b border-line px-4 py-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <Input
                value={q}
                placeholder="Search tenants…"
                className="min-w-[220px] flex-1"
                onChange={(event) => {
                  setQ(event.target.value);
                  setPage(1);
                }}
              />
              <div className="flex flex-wrap items-center gap-2">
                {(
                  [
                    ["all", "All"],
                    ["active", "Active"],
                    ["suspended", "Suspended"],
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
            </div>
            <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-slate-muted">
              <p>
                {tenants.length === 0
                  ? "No tenants yet"
                  : filteredTenants.length === 0
                    ? "No tenants match"
                    : `Showing ${start + 1}–${end} of ${filteredTenants.length} tenants`}
              </p>
              <div className="flex items-center gap-2">
                <Button
                  size="sm"
                  variant="secondary"
                  icon={<ChevronLeftIcon />}
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
                  icon={<ChevronRightIcon />}
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
          <div className="min-w-[1100px]">
            <div className="grid grid-cols-[1.1fr_0.7fr_0.9fr_1fr_0.55fr_0.55fr_auto] gap-3 border-b border-line px-3 py-2">
              <span className="th-label">Tenant</span>
              <span className="th-label">Domain</span>
              <span className="th-label">Organization ID</span>
              <span className="th-label">Owner</span>
              <span className="th-label">Timezone</span>
              <span className="th-label">Status</span>
              <span className="th-label w-24 text-right">Actions</span>
            </div>
            {tenants.length === 0 ? (
              <p className="px-3 py-10 text-center text-sm text-slate-muted">
                No tenants yet — provision one above.
              </p>
            ) : filteredTenants.length === 0 ? (
              <p className="px-3 py-10 text-center text-sm text-slate-muted">
                No tenants match this search or filter.
              </p>
            ) : (
              <div>
                {pageItems.map((tenant) => (
                  <div
                    key={tenant.id}
                    className="grid min-h-14 grid-cols-[1.1fr_0.7fr_0.9fr_1fr_0.55fr_0.55fr_auto] items-center gap-3 border-b border-line/60 px-3 py-2 last:border-0"
                  >
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium">{tenant.name}</p>
                      <p className="mono-cell truncate text-slate-muted">
                        /{tenant.slug}
                      </p>
                    </div>
                    <Badge tone="info">
                      {tenant.domain === "stock_broker"
                        ? "Stock Broker"
                        : tenant.domain === "dental_clinic"
                          ? "Dental Clinic"
                          : "General"}
                    </Badge>
                    <p className="mono-cell truncate text-slate-muted">
                      {tenant.authOrgId}
                    </p>
                    <p
                      className="truncate text-sm text-slate-muted"
                      title={tenant.ownerEmail ?? undefined}
                    >
                      {tenant.ownerEmail || "—"}
                    </p>
                    <p className="mono-cell truncate text-slate-muted">
                      {tenant.timezone || "UTC"}
                    </p>
                    <Badge dot tone={tenant.isActive ? "success" : "danger"}>
                      {tenant.isActive ? "Active" : "Suspended"}
                    </Badge>
                    <div className="flex w-24 shrink-0 items-center justify-end gap-0.5">
                      <Button
                        size="icon"
                        variant="ghost"
                        aria-label={`Edit ${tenant.name}`}
                        title="Edit"
                        disabled={busyId === tenant.id}
                        onClick={() => setEditingTenant(tenant)}
                      >
                        <PencilIcon />
                      </Button>
                      <Button
                        size="icon"
                        variant="ghost"
                        aria-label={`Open workspace for ${tenant.name}`}
                        title="Open workspace"
                        disabled={!tenant.isActive || busyId === tenant.id}
                        onClick={() => workInTenant(tenant)}
                      >
                        <ExternalLinkIcon />
                      </Button>
                      <Button
                        size="icon"
                        variant={tenant.isActive ? "danger" : "ghost"}
                        aria-label={
                          tenant.isActive
                            ? `Suspend ${tenant.name}`
                            : `Reactivate ${tenant.name}`
                        }
                        title={tenant.isActive ? "Suspend" : "Reactivate"}
                        disabled={busyId === tenant.id}
                        onClick={() => toggleTenant(tenant)}
                      >
                        {busyId === tenant.id ? (
                          "…"
                        ) : tenant.isActive ? (
                          <PauseIcon />
                        ) : (
                          <PlayIcon />
                        )}
                      </Button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </section>

      <PlatformGrantCreditsPanel tenants={tenants} />

      <section className="rounded-xl border border-line bg-raised/40 p-4">
        <h2 className="font-display text-xl font-semibold">
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
            <div className="overlay-y-auto max-h-64 space-y-3 rounded-lg border border-line bg-canvas/40 p-3">
              {groupedCatalog.map((group) => (
                <div key={group.domain}>
                  <p className="th-label mb-1.5">{group.label}</p>
                  {(["team", "workflow"] as const).map((kind) => {
                    const rows = group.desks.flatMap((desk) =>
                      desk.items.filter((item) => item.kind === kind),
                    );
                    if (rows.length === 0) return null;
                    return (
                      <div key={kind} className="mb-2 last:mb-0">
                        <p className="px-1 pb-1 text-[11px] font-medium uppercase tracking-[0.14em] text-slate-muted">
                          {kind === "team" ? "Teams" : "Workflows"}
                        </p>
                        {rows.map((item) => {
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
                    );
                  })}
                </div>
              ))}
            </div>
          )}
        </div>

        <Button
          className="mt-4"
          variant="accent"
          icon={<UploadIcon />}
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
          <PlatformAuditLog events={audit} tenants={tenants} />
        </div>
      </section>
    </div>
    {provisioning ? (
      <ProvisionTenantDialog
        getAccessToken={getAccessToken}
        takenEmails={tenants
          .map((tenant) => tenant.ownerEmail)
          .filter((email): email is string => Boolean(email))}
        onCreated={(created) => {
          setTenants((current) =>
            [...current, created].sort((a, b) => a.name.localeCompare(b.name)),
          );
          void refreshAudit();
        }}
        onClose={() => setProvisioning(false)}
      />
    ) : null}
    {editingTenant ? (
      <EditTenantDialog
        key={editingTenant.id}
        tenant={editingTenant}
        getAccessToken={getAccessToken}
        takenEmails={tenants
          .filter((tenant) => tenant.id !== editingTenant.id)
          .map((tenant) => tenant.ownerEmail)
          .filter((email): email is string => Boolean(email))}
        onSaved={(updated) => {
          setTenants((current) =>
            current
              .map((row) => (row.id === updated.id ? updated : row))
              .sort((a, b) => a.name.localeCompare(b.name)),
          );
          void refreshAudit();
        }}
        onClose={() => setEditingTenant(null)}
      />
    ) : null}
    </>
  );
}
