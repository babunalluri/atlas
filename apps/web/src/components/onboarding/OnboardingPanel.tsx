"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { Button } from "@/components/ui/Button";
import { Input, Label } from "@/components/ui/Field";
import {
  createSelfServeWorkspace,
  getOnboardingStatus,
} from "@/lib/api/admin";
import { useAgentOsToken } from "@/lib/auth/token";
import { slugifyName } from "@/lib/validation/agent-form";

export function OnboardingPanel() {
  const { getAccessToken, isLoaded, isSignedIn } = useAgentOsToken();
  const router = useRouter();
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
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
        const status = await getOnboardingStatus(await getAccessToken());
        if (cancelled) return;
        if (status.provisioned) {
          router.replace("/admin/agents");
          return;
        }
        setOrgId(status.org_id);
        setCanCreate(status.can_create);
        setError(null);
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
      });
      router.replace("/admin/agents");
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
        Sign in with Clerk to create or request a workspace.
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
          Your Clerk organization is signed in but not linked to an Atlas
          tenant yet. As the first org admin you can create a workspace with
          sensible defaults, then publish agents and share a customer chat
          widget.
        </p>
        {orgId ? (
          <p className="mt-2 mono-cell text-xs text-slate-muted">
            Clerk org: {orgId}
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
              placeholder="Acme Support"
            />
          </div>
          <div>
            <Label htmlFor="workspace-slug">URL slug</Label>
            <Input
              id="workspace-slug"
              value={slug}
              onChange={(event) => setSlug(slugifyName(event.target.value))}
              placeholder="acme"
            />
            <p className="mt-1 text-xs text-slate-muted">
              Customer chat will live at /t/{slug || "your-slug"}/chat/…
            </p>
          </div>
          <Button
            variant="accent"
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
