"use client";

import { useEffect, useState } from "react";
import { useRouter } from "@/i18n/navigation";

import { Button } from "@/components/ui/Button";
import { PlusIcon } from "@/components/ui/icons";
import { Input, Label } from "@/components/ui/Field";
import { SearchableSelect } from "@/components/ui/SearchableSelect";
import {
  createSelfServeWorkspace,
  getOnboardingStatus,
  listWorkspaceDomains,
} from "@/lib/api/admin";
import type { WorkspaceDomain, WorkspaceDomainOption } from "@/lib/api/types";
import { clearPlatformTenantSelection } from "@/lib/auth/access-context";
import { ORG_ADMIN_HREF } from "@/lib/auth/desk-admin";
import { useAgentOsToken } from "@/lib/auth/token";
import { slugifyName } from "@/lib/validation/agent-form";

const FALLBACK_DOMAINS: WorkspaceDomainOption[] = [
  { id: "generic", label: "General" },
  { id: "stock_broker", label: "Stock Broker" },
  { id: "dental_clinic", label: "Dental Clinic" },
];

export function OnboardingPanel() {
  const { getAccessToken, isLoaded, isSignedIn } = useAgentOsToken();
  const router = useRouter();
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [domain, setDomain] = useState<WorkspaceDomain>("generic");
  const [domainOptions, setDomainOptions] =
    useState<WorkspaceDomainOption[]>(FALLBACK_DOMAINS);
  const [orgId, setOrgId] = useState<string | null>(null);
  const [canCreate, setCanCreate] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!isLoaded) return;
    if (!isSignedIn) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        const token = await getAccessToken();
        const status = await getOnboardingStatus(token);
        if (cancelled) return;
        if (status.provisioned) {
          router.replace(ORG_ADMIN_HREF);
          return;
        }
        setOrgId(status.org_id);
        setCanCreate(status.can_create);
        setError(null);
        try {
          const options = await listWorkspaceDomains(token);
          if (!cancelled && options.length > 0) {
            setDomainOptions(options);
          }
        } catch {
          // Fall back to static domain list when catalog endpoint is unavailable.
        }
      } catch (reason) {
        if (!cancelled) {
          setError(
            reason instanceof Error
              ? reason.message
              : "Could not check workspace status",
          );
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [getAccessToken, isLoaded, isSignedIn, router]);

  async function onCreate() {
    setSaving(true);
    setError(null);
    try {
      await createSelfServeWorkspace(await getAccessToken(), {
        name: name.trim(),
        slug: slug.trim(),
        domain,
      });
      clearPlatformTenantSelection();
      router.replace(ORG_ADMIN_HREF);
      router.refresh();
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Could not create workspace",
      );
    } finally {
      setSaving(false);
    }
  }

  if (!isLoaded || loading) {
    return (
      <p className="text-sm text-slate-muted">Checking your organization…</p>
    );
  }

  if (!isSignedIn) {
    return (
      <p className="text-sm text-slate-muted">
        Sign in to create or request a workspace.
      </p>
    );
  }

  return (
    <div className="mx-auto max-w-lg space-y-4">
      <header>
        <h1 className="font-display text-2xl font-semibold tracking-tight">
          Create your workspace
        </h1>
        <p className="mt-1 text-sm text-slate-muted">
          Choose a domain for your organization. Atlas will provision agents,
          teams, and workflows tailored to that industry for every member of
          your org.
        </p>
        {orgId ? (
          <p className="mt-2 mono-cell text-xs text-slate-muted">
            Organization: {orgId}
          </p>
        ) : null}
      </header>

      {!canCreate ? (
        <p className="rounded-md border border-amber/30 bg-amber/10 px-3 py-2 text-sm">
          Ask an organization admin to create the workspace, or contact your
          Atlas platform administrator to provision access.
        </p>
      ) : (
        <section className="space-y-3 rounded-xl border border-line bg-raised/40 p-4">
          <div>
            <Label htmlFor="workspace-name">Workspace name</Label>
            <Input
              id="workspace-name"
              value={name}
              onChange={(event) => {
                const next = event.target.value;
                setName(next);
                if (!slug || slug === slugifyName(name)) {
                  setSlug(slugifyName(next));
                }
              }}
              placeholder="Acme Trading"
            />
          </div>
          <div>
            <Label htmlFor="workspace-slug">URL slug</Label>
            <Input
              id="workspace-slug"
              value={slug}
              onChange={(event) => setSlug(slugifyName(event.target.value))}
              placeholder="acme-trading"
            />
            <p className="mt-1 text-xs text-slate-muted">
              Customer chat will live at /t/{slug || "your-slug"}/teams/… or
              /workflows/…
            </p>
          </div>
          <div>
            <Label htmlFor="workspace-domain">Industry domain</Label>
            <SearchableSelect
              id="workspace-domain"
              value={domain}
              onChange={(value) => setDomain(value as WorkspaceDomain)}
              placeholder="Select domain"
              options={domainOptions.map((option) => ({
                value: option.id,
                label: option.label,
              }))}
            />
            <p className="mt-1 text-xs text-slate-muted">
              All users in this organization inherit the selected domain workspace.
            </p>
          </div>
          <Button
            variant="accent"
            icon={<PlusIcon />}
            disabled={saving || !name.trim() || !slug.trim()}
            onClick={() => void onCreate()}
          >
            {saving ? "Creating…" : "Create workspace"}
          </Button>
        </section>
      )}

      {error ? <p className="text-sm text-rose">{error}</p> : null}
    </div>
  );
}
