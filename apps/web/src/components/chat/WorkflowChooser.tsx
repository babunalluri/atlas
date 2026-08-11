"use client";

import { Link, useRouter } from "@/i18n/navigation";
import { useEffect, useMemo, useState, type CSSProperties } from "react";

import { ChatAccountBar } from "@/components/chat/ChatAccountBar";
import { NotificationBell } from "@/components/notifications/NotificationBell";
import {
  ThemeToggle,
  useSurfaceTheme,
} from "@/components/layout/ThemeToggle";
import {
  getWorkspaceInfo,
  listAvailableTeams,
  listAvailableWorkflows,
} from "@/lib/api/admin";
import type {
  AvailableTeam,
  AvailableWorkflow,
  TenantBranding,
} from "@/lib/api/types";
import { useAgentOsToken } from "@/lib/auth/token";
import { cn } from "@/lib/utils";

type FilterKind = "all" | "workflow" | "team";

type PortalItem =
  | { kind: "workflow"; item: AvailableWorkflow }
  | { kind: "team"; item: AvailableTeam };

export function WorkflowChooser({ tenant }: { tenant: TenantBranding }) {
  const router = useRouter();
  const { getAccessToken, isSignedIn, isLoaded } = useAgentOsToken();
  const [workflows, setWorkflows] = useState<AvailableWorkflow[]>([]);
  const [teams, setTeams] = useState<AvailableTeam[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<FilterKind>("all");
  const [canAdminister, setCanAdminister] = useState(false);
  const { theme, dark, changeTheme } = useSurfaceTheme("workspace");

  useEffect(() => {
    if (!isLoaded) return;
    if (!isSignedIn) {
      router.replace(
        `/sign-in?redirect_url=${encodeURIComponent(`/t/${tenant.slug}/chat`)}`,
      );
      return;
    }
    let cancelled = false;
    setLoading(true);
    void (async () => {
      try {
        const token = await getAccessToken();
        const [workflowRows, teamRows, workspace] = await Promise.all([
          listAvailableWorkflows(token),
          listAvailableTeams(token),
          getWorkspaceInfo(token).catch(() => null),
        ]);
        if (!cancelled) {
          setWorkflows(workflowRows);
          setTeams(teamRows);
          setCanAdminister(workspace == null ? false : workspace.can_administer !== false);
          setError(null);
        }
      } catch (reason) {
        if (!cancelled) {
          setError(
            reason instanceof Error
              ? reason.message
              : "Could not load your assigned surfaces",
          );
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [getAccessToken, isLoaded, isSignedIn, router, tenant.slug]);

  const cssVars = {
    "--tenant-primary": tenant.primaryColor,
    "--tenant-accent": tenant.accentColor,
  } as CSSProperties;

  const items = useMemo(() => {
    const rows: PortalItem[] = [
      ...workflows.map((item) => ({ kind: "workflow" as const, item })),
      ...teams.map((item) => ({ kind: "team" as const, item })),
    ];
    const needle = query.trim().toLowerCase();
    return rows.filter((row) => {
      if (filter !== "all" && row.kind !== filter) return false;
      if (!needle) return true;
      const hay = `${row.item.name} ${row.item.description ?? ""} ${row.item.slug}`.toLowerCase();
      return hay.includes(needle);
    });
  }, [filter, query, teams, workflows]);

  const emptyAssigned = workflows.length === 0 && teams.length === 0;
  const emptyFiltered = !emptyAssigned && items.length === 0;
  const showFilter = workflows.length + teams.length > 3;

  return (
    <main
      style={cssVars}
      data-theme={dark ? "dark" : undefined}
      className="app-canvas min-h-screen text-ink"
    >
      <div
        className="relative min-h-screen overflow-hidden"
        style={
          dark
            ? {
                background: `
            radial-gradient(900px 420px at 8% -8%, color-mix(in oklab, var(--tenant-accent) 28%, transparent), transparent 55%),
            radial-gradient(700px 380px at 92% 8%, color-mix(in oklab, var(--tenant-primary) 40%, transparent), transparent 50%),
            linear-gradient(165deg, color-mix(in oklab, var(--tenant-primary) 88%, #020807) 0%, #04110c 48%, #020807 100%)
          `,
              }
            : undefined
        }
      >
        <div className="pointer-events-none absolute inset-0 opacity-[0.14] grid-noise" />
        <div
          className="pointer-events-none absolute inset-x-0 top-0 h-px"
          style={{
            background:
              "linear-gradient(90deg, transparent, color-mix(in oklab, var(--tenant-accent) 55%, transparent), transparent)",
          }}
        />

        <div className="relative mx-auto max-w-5xl px-5 py-10 md:px-8 md:py-16">
          <header className="portal-rise flex items-start justify-between gap-4">
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.18em] text-[var(--tenant-accent)]">
                {tenant.name}
              </p>
              <p className="mt-1 text-sm text-slate-muted">Workspace portal</p>
            </div>
            <div className="flex items-center gap-2">
              <NotificationBell />
              <ThemeToggle theme={theme} onChange={changeTheme} />
              {canAdminister ? (
                <Link
                  href="/admin/agents"
                  className="rounded-lg border border-line bg-raised/70 px-3 py-1.5 text-xs font-medium text-slate-muted transition hover:border-[var(--tenant-accent)]/60 hover:text-ink"
                >
                  Admin
                </Link>
              ) : null}
              <ChatAccountBar
                tenantSlug={tenant.slug}
                signInRedirect={`/t/${tenant.slug}/chat`}
              />
            </div>
          </header>

          <div className="portal-rise portal-rise-delay-1 mt-10 max-w-2xl">
            <h1 className="font-display text-4xl font-semibold tracking-tight text-ink md:text-5xl">
              What would you like to work on?
            </h1>
            <p className="mt-3 text-base leading-relaxed text-slate-muted">
              Choose a workflow or team assigned to your organization account.
            </p>
          </div>

          {!isLoaded || !isSignedIn ? (
            <p className="mt-12 text-sm text-slate-muted">
              Redirecting to sign in…
            </p>
          ) : null}

          {isLoaded && isSignedIn && loading ? (
            <div className="mt-12 grid gap-4 md:grid-cols-2">
              {[0, 1, 2, 3].map((i) => (
                <div
                  key={i}
                  className="h-40 animate-pulse rounded-2xl border border-line bg-raised/50"
                  style={{ animationDelay: `${i * 80}ms` }}
                />
              ))}
            </div>
          ) : null}

          {isLoaded && isSignedIn && error ? (
            <div className="mt-12 rounded-2xl border border-amber/40 bg-amber/10 px-5 py-4 text-sm text-amber">
              {error}
            </div>
          ) : null}

          {isLoaded && isSignedIn && !loading && !error ? (
            <div className="portal-rise portal-rise-delay-2 mt-12">
              {showFilter ? (
                <div className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                  <div className="flex flex-wrap gap-2">
                    {(
                      [
                        ["all", "All"],
                        ["workflow", "Workflows"],
                        ["team", "Teams"],
                      ] as const
                    ).map(([value, label]) => (
                      <button
                        key={value}
                        type="button"
                        onClick={() => setFilter(value)}
                        className={cn(
                          "rounded-full border px-3 py-1.5 text-xs font-semibold tracking-wide transition",
                          filter === value
                            ? "border-[var(--tenant-accent)] bg-[var(--tenant-accent)]/15 text-[var(--tenant-accent)]"
                            : "border-line bg-raised/60 text-slate-muted hover:border-line-strong hover:text-ink",
                        )}
                      >
                        {label}
                      </button>
                    ))}
                  </div>
                  <label className="relative block w-full sm:max-w-xs">
                    <span className="sr-only">Search</span>
                    <input
                      value={query}
                      onChange={(e) => setQuery(e.target.value)}
                      placeholder="Search workflows and teams"
                      className="w-full rounded-xl border border-line bg-raised px-3.5 py-2.5 text-sm text-ink placeholder:text-slate-muted outline-none transition focus:border-[var(--tenant-accent)]/50"
                    />
                  </label>
                </div>
              ) : null}

              <div className="grid gap-4 md:grid-cols-2">
                {items.map((row, index) => {
                  const href =
                    row.kind === "workflow"
                      ? `/t/${tenant.slug}/workflows/${row.item.slug}`
                      : `/t/${tenant.slug}/teams/${row.item.slug}`;
                  return (
                    <Link
                      key={`${row.kind}-${row.item.id}`}
                      href={href}
                      className="portal-rise group relative overflow-hidden rounded-2xl border border-line bg-raised/70 p-5 transition duration-200 hover:-translate-y-0.5 hover:border-[var(--tenant-accent)]/55"
                      style={{
                        animationDelay: `${0.08 + index * 0.045}s`,
                      }}
                    >
                      <div
                        className="pointer-events-none absolute inset-x-0 top-0 h-px opacity-0 transition group-hover:opacity-100"
                        style={{
                          background:
                            "linear-gradient(90deg, transparent, var(--tenant-accent), transparent)",
                        }}
                      />
                      <div className="flex items-center justify-between gap-3">
                        <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-[var(--tenant-accent)]">
                          {row.kind === "workflow" ? "Workflow" : "Team"}
                        </p>
                        <span className="rounded-md border border-line px-1.5 py-0.5 font-mono text-[10px] text-slate-muted">
                          {row.item.slug}
                        </span>
                      </div>
                      <h2 className="mt-3 font-display text-2xl font-semibold tracking-tight text-ink">
                        {row.item.name}
                      </h2>
                      <p className="mt-2 min-h-10 text-sm leading-relaxed text-slate-muted">
                        {row.item.description ||
                          (row.kind === "workflow"
                            ? "Start this guided workflow."
                            : "Chat with this coordinated team.")}
                      </p>
                      <span className="mt-6 inline-flex items-center text-sm font-semibold text-[var(--tenant-accent)]">
                        Open chat
                        <span className="ml-1.5 transition-transform group-hover:translate-x-1">
                          →
                        </span>
                      </span>
                    </Link>
                  );
                })}

                {emptyAssigned ? (
                  <div className="rounded-2xl border border-dashed border-line bg-raised/40 p-10 text-center md:col-span-2">
                    <p className="font-display text-xl font-semibold text-ink">
                      Nothing assigned yet
                    </p>
                    <p className="mx-auto mt-2 max-w-md text-sm leading-relaxed text-slate-muted">
                      Ask your organization administrator to grant access to a
                      published workflow or team under Users.
                    </p>
                  </div>
                ) : null}

                {emptyFiltered ? (
                  <div className="rounded-2xl border border-dashed border-line p-8 text-center text-sm text-slate-muted md:col-span-2">
                    No matches for “{query.trim() || filter}”. Try another
                    filter or clear search.
                  </div>
                ) : null}
              </div>
            </div>
          ) : null}
        </div>
      </div>
    </main>
  );
}
