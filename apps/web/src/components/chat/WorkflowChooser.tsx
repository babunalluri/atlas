"use client";

import Link from "next/link";
import { useEffect, useState, type CSSProperties } from "react";

import { listAvailableWorkflows } from "@/lib/api/admin";
import type { AvailableWorkflow, TenantBranding } from "@/lib/api/types";
import { useAgentOsToken } from "@/lib/auth/token";

export function WorkflowChooser({ tenant }: { tenant: TenantBranding }) {
  const { getAccessToken, isSignedIn, isLoaded } = useAgentOsToken();
  const [workflows, setWorkflows] = useState<AvailableWorkflow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!isLoaded) return;
    if (!isSignedIn) {
      setLoading(false);
      setWorkflows([]);
      setError(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    void (async () => {
      try {
        const rows = await listAvailableWorkflows(await getAccessToken());
        if (!cancelled) {
          setWorkflows(rows);
          setError(null);
        }
      } catch (reason) {
        if (!cancelled) {
          setError(
            reason instanceof Error
              ? reason.message
              : "Could not load your workflows",
          );
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [getAccessToken, isLoaded, isSignedIn]);

  const cssVars = {
    "--tenant-primary": tenant.primaryColor,
    "--tenant-accent": tenant.accentColor,
  } as CSSProperties;

  return (
    <main
      style={cssVars}
      data-theme="dark"
      className="min-h-screen text-white"
    >
      <div
        className="relative min-h-screen overflow-hidden"
        style={{
          background: `
            radial-gradient(1000px 500px at 15% 0%, color-mix(in oklab, var(--tenant-accent) 35%, transparent), transparent 55%),
            linear-gradient(160deg, var(--tenant-primary) 0%, #04110c 55%, #020807 100%)
          `,
        }}
      >
        <div className="pointer-events-none absolute inset-0 opacity-[0.18] grid-noise" />
        <div className="relative mx-auto max-w-5xl px-5 py-12 md:py-20">
          <p className="text-xs font-semibold uppercase tracking-[0.16em] text-[var(--tenant-accent)]">
            {tenant.name}
          </p>
          <h1 className="mt-3 max-w-2xl font-display text-4xl font-semibold tracking-tight md:text-5xl">
            What would you like to work on?
          </h1>
          <p className="mt-3 max-w-2xl text-white/65">
            Select one of the workflows assigned to your account. Each workflow
            coordinates the right teams and agents for the task.
          </p>

          {!isLoaded ? (
            <p className="mt-10 text-sm text-white/60">Checking sign-in…</p>
          ) : null}

          {isLoaded && !isSignedIn ? (
            <div className="mt-10 rounded-2xl border border-white/10 bg-white/5 p-6">
              <p className="text-sm text-white/70">
                Sign in with your organization account to see your workflows.
              </p>
              <Link
                href={`/sign-in?redirect_url=${encodeURIComponent(`/t/${tenant.slug}/chat`)}`}
                className="mt-4 inline-flex rounded-lg bg-[var(--tenant-accent)] px-4 py-2 text-sm font-semibold text-slate-950"
              >
                Sign in
              </Link>
            </div>
          ) : null}

          {isLoaded && isSignedIn && loading ? (
            <p className="mt-10 text-sm text-white/60">Loading workflows…</p>
          ) : null}
          {isLoaded && isSignedIn && error ? (
            <p className="mt-10 text-sm text-amber">{error}</p>
          ) : null}

          {isLoaded && isSignedIn && !loading && !error ? (
            <div className="mt-10 grid gap-4 md:grid-cols-2">
              {workflows.map((workflow) => (
                <Link
                  key={workflow.id}
                  href={`/t/${tenant.slug}/workflows/${workflow.slug}`}
                  className="group rounded-2xl border border-white/10 bg-white/[0.06] p-5 transition hover:-translate-y-0.5 hover:border-[var(--tenant-accent)]/60 hover:bg-white/10"
                >
                  <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-[var(--tenant-accent)]">
                    Workflow
                  </p>
                  <h2 className="mt-2 font-display text-2xl font-semibold">
                    {workflow.name}
                  </h2>
                  <p className="mt-2 min-h-10 text-sm leading-relaxed text-white/60">
                    {workflow.description || "Start this guided workflow."}
                  </p>
                  <span className="mt-5 inline-flex text-sm font-semibold text-[var(--tenant-accent)]">
                    Open chat <span className="ml-1 transition group-hover:translate-x-1">→</span>
                  </span>
                </Link>
              ))}
              {workflows.length === 0 ? (
                <div className="rounded-2xl border border-dashed border-white/15 p-8 text-center text-sm text-white/60 md:col-span-2">
                  <p>
                    No workflows are assigned to your account yet. Ask your
                    organization administrator to grant access.
                  </p>
                  <Link
                    href={`/t/${tenant.slug}/chat/support-concierge`}
                    className="mt-4 inline-flex text-sm font-semibold text-[var(--tenant-accent)]"
                  >
                    Or open Support Concierge →
                  </Link>
                </div>
              ) : null}
            </div>
          ) : null}
        </div>
      </div>
    </main>
  );
}
