"use client";

import {
  SignedIn,
  SignedOut,
  SignInButton,
  SignUpButton,
  UserButton,
  useAuth,
} from "@clerk/nextjs";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useId, useRef, useState } from "react";

import { BrandMark } from "@/components/layout/BrandMark";
import { NotificationBell } from "@/components/notifications/NotificationBell";
import {
  clearPlatformTenantSelection,
  PLATFORM_TENANT_NAME_COOKIE,
  tokenIsPlatformAdmin,
} from "@/lib/auth/access-context";
import { getOnboardingStatus, getWorkspaceInfo } from "@/lib/api/admin";
import { useAgentOsToken } from "@/lib/auth/token";
import {
  ThemeToggle,
  useSurfaceTheme,
} from "@/components/layout/ThemeToggle";
import { cn } from "@/lib/utils";

type NavItem = {
  href: string;
  label: string;
  hint: string;
  icon: React.ReactNode;
};

function icon(children: React.ReactNode) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      className="size-4 shrink-0"
      aria-hidden
    >
      {children}
    </svg>
  );
}

const icons = {
  agents: icon(
    <>
      <circle cx="12" cy="8" r="4" />
      <path d="M5 20c1.5-3.2 4-5 7-5s5.5 1.8 7 5" />
    </>,
  ),
  teams: icon(
    <>
      <circle cx="9" cy="9" r="3.5" />
      <circle cx="16.5" cy="10.5" r="2.5" />
      <path d="M3.5 19c1.2-2.6 3.2-4 5.5-4s4.3 1.4 5.5 4" />
      <path d="M15.5 15.5c2 .2 3.6 1.4 4.8 3.5" />
    </>,
  ),
  workflows: icon(
    <>
      <rect x="3" y="4" width="6" height="5" rx="1" />
      <rect x="15" y="15" width="6" height="5" rx="1" />
      <path d="M9 6.5h5a2 2 0 0 1 2 2V15" />
    </>,
  ),
  tools: icon(
    <path d="M14.5 6.5a4 4 0 0 1 5-5l-3 3 .7 2.3 2.3.7 3-3a4 4 0 0 1-5 5L8 19a2.1 2.1 0 0 1-3-3l9.5-9.5Z" />,
  ),
  knowledge: icon(
    <>
      <path d="M4 19V5a2 2 0 0 1 2-2h13v16H6a2 2 0 0 0-2 2Z" />
      <path d="M4 19a2 2 0 0 0 2 2h13" />
      <path d="M9 7h6" />
    </>,
  ),
  approvals: icon(
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="m8.5 12 2.5 2.5 4.5-5" />
    </>,
  ),
  schedules: icon(
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3.5 2" />
    </>,
  ),
  traces: icon(
    <path d="M3 12h4l2.5-6 5 12L17 12h4" />,
  ),
  metrics: icon(
    <>
      <path d="M4 20V10" />
      <path d="M10 20V4" />
      <path d="M16 20v-8" />
      <path d="M22 20H2" />
    </>,
  ),
  evals: icon(
    <>
      <rect x="5" y="3" width="14" height="18" rx="2" />
      <path d="m9 12 2 2 4-4" />
      <path d="M9 7h6" />
    </>,
  ),
  memories: icon(
    <>
      <path d="M4 7a4 4 0 0 1 4-4h8a4 4 0 0 1 4 4v10a3 3 0 0 1-3 3H7a3 3 0 0 1-3-3V7Z" />
      <path d="M8 11h8" />
      <path d="M8 15h5" />
    </>,
  ),
  learnings: icon(
    <>
      <path d="M5 4h9l5 5v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2Z" />
      <path d="M14 4v5h5" />
      <path d="M8 13h8" />
      <path d="M8 17h6" />
    </>,
  ),
  credentials: icon(
    <>
      <circle cx="8" cy="14" r="4" />
      <path d="m11 11 8-8" />
      <path d="M16 6l2 2" />
      <path d="M19 3l2 2" />
    </>,
  ),
  integrations: icon(
    <>
      <path d="M8 3v5" />
      <path d="M16 3v5" />
      <path d="M5 8h14v3a7 7 0 0 1-7 7v3" />
    </>,
  ),
  access: icon(
    <>
      <rect x="3" y="5" width="18" height="14" rx="2" />
      <circle cx="9" cy="11" r="2" />
      <path d="M6 17c.7-1.6 1.7-2.4 3-2.4s2.3.8 3 2.4" />
      <path d="M14 9h5" />
      <path d="M14 13h5" />
    </>,
  ),
  publicApi: icon(
    <>
      <path d="M4 7h16" />
      <path d="M4 12h10" />
      <path d="M4 17h7" />
      <path d="M16 14l4 3-4 3" />
    </>,
  ),
  mcp: icon(
    <>
      <rect x="3" y="4" width="18" height="7" rx="2" />
      <rect x="3" y="13" width="18" height="7" rx="2" />
      <path d="M7 7.5h.01" />
      <path d="M7 16.5h.01" />
    </>,
  ),
  platform: icon(
    <>
      <path d="M12 3 4 7v5c0 5 3.4 8 8 9 4.6-1 8-4 8-9V7l-8-4Z" />
      <path d="M9 12h6" />
      <path d="M12 9v6" />
    </>,
  ),
  users: icon(
    <>
      <circle cx="9" cy="8" r="3.5" />
      <path d="M3.5 19c1.2-2.8 3.3-4.2 5.5-4.2S13.3 16.2 14.5 19" />
      <circle cx="17" cy="9" r="2.5" />
      <path d="M15 15.8c1.7.3 3.1 1.3 4.2 3.2" />
    </>,
  ),
  notifications: icon(
    <>
      <path d="M6 9a6 6 0 0 1 12 0c0 7 3 7 3 7H3s3 0 3-7" />
      <path d="M10 19a2 2 0 0 0 4 0" />
    </>,
  ),
};

const navGroups: Array<{
  label: string;
  items: NavItem[];
}> = [
  {
    label: "Build",
    items: [
      {
        href: "/admin/workflows",
        label: "Workflows",
        hint: "What users and APIs can run",
        icon: icons.workflows,
      },
      {
        href: "/admin/teams",
        label: "Teams",
        hint: "Coordinate multiple agents",
        icon: icons.teams,
      },
      {
        href: "/admin/agents",
        label: "Agents",
        hint: "Specialists with prompts and tools",
        icon: icons.agents,
      },
      {
        href: "/admin/tools",
        label: "Tools",
        hint: "API, Python, MCP, and toolkits",
        icon: icons.tools,
      },
      {
        href: "/admin/integrations",
        label: "Toolkit catalog",
        hint: "Enable built-in toolkits as tools",
        icon: icons.integrations,
      },
    ],
  },
  {
    label: "Monitor",
    items: [
      {
        href: "/admin/traces",
        label: "Traces",
        hint: "Chat logs, runs, and errors",
        icon: icons.traces,
      },
      {
        href: "/admin/approvals",
        label: "Approvals",
        hint: "Paused tool actions waiting on you",
        icon: icons.approvals,
      },
      {
        href: "/admin/schedules",
        label: "Schedules",
        hint: "Timed and recurring runs",
        icon: icons.schedules,
      },
      {
        href: "/admin/metrics",
        label: "Metrics",
        hint: "Usage and error rates",
        icon: icons.metrics,
      },
      {
        href: "/admin/evals",
        label: "Evaluations",
        hint: "Quality test suites",
        icon: icons.evals,
      },
    ],
  },
  {
    label: "Configure",
    items: [
      {
        href: "/admin/knowledge",
        label: "Knowledge",
        hint: "Documents agents can use",
        icon: icons.knowledge,
      },
      {
        href: "/admin/memories",
        label: "Memories",
        hint: "Persistent user memory rows",
        icon: icons.memories,
      },
      {
        href: "/admin/learnings",
        label: "Learnings",
        hint: "Learning store records",
        icon: icons.learnings,
      },
      {
        href: "/admin/users",
        label: "Users",
        hint: "People and workflow access",
        icon: icons.users,
      },
      {
        href: "/admin/notifications",
        label: "Notifications",
        hint: "Message one user or everyone",
        icon: icons.notifications,
      },
      {
        href: "/admin/credentials",
        label: "Credentials",
        hint: "API keys kept server-side",
        icon: icons.credentials,
      },
      {
        href: "/admin/public-api",
        label: "Public API",
        hint: "Secret keys and try-it calls",
        icon: icons.publicApi,
      },
      {
        href: "/admin/service-accounts",
        label: "Service accounts",
        hint: "Scoped machine tokens",
        icon: icons.access,
      },
      {
        href: "/admin/mcp",
        label: "MCP server",
        hint: "Expose agents over MCP",
        icon: icons.mcp,
      },
    ],
  },
];

const platformGroup = {
  label: "Platform",
  items: [
    {
      href: "/admin/platform/tenants",
      label: "Super admin",
      hint: "Manage and enter tenant workspaces",
      icon: icons.platform,
    },
    {
      href: "/admin/platform/sandbox-packages",
      label: "Sandbox packages",
      hint: "Allowlist pins for editable Python",
      icon: icons.platform,
    },
  ],
};

function NavLinks({
  pathname,
  isPlatformAdmin,
  onNavigate,
}: {
  pathname: string;
  isPlatformAdmin: boolean;
  onNavigate?: () => void;
}) {
  const groups = isPlatformAdmin ? [platformGroup, ...navGroups] : navGroups;
  // Ignore repeat clicks while a navigation is still in flight (slow RSC).
  const pendingHref = useRef<string | null>(null);

  useEffect(() => {
    pendingHref.current = null;
  }, [pathname]);

  return (
    <nav className="flex flex-col gap-6">
      {groups.map((group) => (
        <div key={group.label}>
          <p className="px-3 pb-2 text-[10px] font-semibold uppercase tracking-[0.16em] text-slate-muted">
            {group.label}
          </p>
          <div className="flex flex-col gap-0.5">
            {group.items.map((item) => {
              const active = pathname.startsWith(item.href);
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  // Default prefetch floods ~15 RSC payloads on every shell paint.
                  prefetch={false}
                  onClick={(event) => {
                    if (
                      pendingHref.current !== null &&
                      pendingHref.current !== item.href
                    ) {
                      event.preventDefault();
                      return;
                    }
                    if (pendingHref.current === item.href) {
                      event.preventDefault();
                      return;
                    }
                    pendingHref.current = item.href;
                    onNavigate?.();
                  }}
                  title={item.hint}
                  className={cn(
                    "flex items-center gap-2.5 rounded-md border-l-2 px-3 py-1.5 text-sm font-medium transition",
                    active
                      ? "border-teal bg-ink text-canvas"
                      : "border-transparent text-slate-muted hover:bg-fog/70 hover:text-ink",
                  )}
                >
                  <span className={active ? "text-teal-bright" : "text-slate-muted"}>
                    {item.icon}
                  </span>
                  {item.label}
                </Link>
              );
            })}
          </div>
        </div>
      ))}
    </nav>
  );
}

export function AdminShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { getAccessToken, isLoaded, isSignedIn } = useAgentOsToken();
  const { orgId } = useAuth();
  const { theme, changeTheme } = useSurfaceTheme("admin");
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [isPlatformAdmin, setIsPlatformAdmin] = useState(false);
  const [selectedTenantName, setSelectedTenantName] = useState<string | null>(
    null,
  );
  const [workspaceHref, setWorkspaceHref] = useState<string | null>(null);
  const onOnboardingRoute = pathname.startsWith("/admin/onboarding");
  const mobileNavId = useId();
  const menuButtonRef = useRef<HTMLButtonElement>(null);
  const drawerRef = useRef<HTMLElement>(null);
  const lastOrgIdRef = useRef<string | null | undefined>(undefined);

  useEffect(() => {
    const nameCookie = document.cookie
      .split(";")
      .map((part) => part.trim())
      .find((part) => part.startsWith(`${PLATFORM_TENANT_NAME_COOKIE}=`));
    if (nameCookie) {
      setSelectedTenantName(
        decodeURIComponent(nameCookie.split("=").slice(1).join("=")),
      );
    }
  }, []);

  // Switching Clerk orgs must drop Platform → Open workspace override so
  // requests use the home tenant for the active org (avoids stale 403s).
  useEffect(() => {
    if (lastOrgIdRef.current === undefined) {
      lastOrgIdRef.current = orgId;
      return;
    }
    if (lastOrgIdRef.current !== orgId) {
      lastOrgIdRef.current = orgId;
      clearPlatformTenantSelection();
      setSelectedTenantName(null);
    }
  }, [orgId]);

  // Re-check after Clerk is ready. Depend only on auth readiness — not on
  // getAccessToken identity — to avoid effect thrash / click lag.
  useEffect(() => {
    if (!isLoaded) return;
    let cancelled = false;
    void (async () => {
      try {
        if (!isSignedIn) {
          if (!cancelled) setIsPlatformAdmin(false);
          return;
        }
        const token = await getAccessToken();
        if (!cancelled) {
          setIsPlatformAdmin(token ? tokenIsPlatformAdmin(token) : false);
        }
      } catch {
        if (!cancelled) setIsPlatformAdmin(false);
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- getAccessToken is stable via ref
  }, [isLoaded, isSignedIn]);

  useEffect(() => {
    if (onOnboardingRoute || !isLoaded || !isSignedIn) return;
    let cancelled = false;
    void (async () => {
      try {
        const token = await getAccessToken();
        if (!token || cancelled) return;
        const status = await getOnboardingStatus(token);
        if (cancelled) return;
        if (!status.provisioned) {
          router.replace("/admin/onboarding");
          return;
        }
        // End users do not build the control plane — only run assigned workflows.
        const workspace = await getWorkspaceInfo(token);
        if (cancelled) return;
        const slug = workspace.slug || status.tenant_slug;
        if (slug) {
          setWorkspaceHref(`/t/${slug}/chat`);
        }
        if (workspace.can_administer === false) {
          router.replace(`/t/${workspace.slug}/chat`);
        }
      } catch {
        // Ignore — individual pages still surface API errors.
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- run once per auth readiness
  }, [isLoaded, isSignedIn, onOnboardingRoute, router]);

  useEffect(() => {
    setMobileNavOpen(false);
  }, [pathname]);

  useEffect(() => {
    if (!mobileNavOpen) return;
    const drawer = drawerRef.current;
    if (!drawer) return;
    const previouslyFocused = document.activeElement as HTMLElement | null;
    const focusables = () =>
      Array.from(
        drawer.querySelectorAll<HTMLElement>(
          'a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ),
      );

    const items = focusables();
    (items[0] ?? drawer).focus();

    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        setMobileNavOpen(false);
        return;
      }
      if (event.key !== "Tab") return;
      const nodes = focusables();
      if (nodes.length === 0) return;
      const first = nodes[0];
      const last = nodes[nodes.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }

    document.addEventListener("keydown", onKeyDown);
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = previousOverflow;
      (previouslyFocused ?? menuButtonRef.current)?.focus?.();
    };
  }, [mobileNavOpen]);

  function leaveTenantWorkspace() {
    clearPlatformTenantSelection();
    setSelectedTenantName(null);
    window.location.assign("/admin/platform/tenants");
  }

  const publishableKey = process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY;
  const clerkReady =
    !!publishableKey && !publishableKey.includes("replace_me");

  return (
    <div
      data-theme={theme === "dark" ? "dark" : undefined}
      className="app-canvas flex h-dvh flex-col overflow-hidden text-ink"
    >
      <header className="glass-bar z-30 shrink-0 border-b border-line/70">
        <div className="flex items-center justify-between gap-6 px-5 py-3">
          <div className="flex min-w-0 items-center gap-3">
            <button
              ref={menuButtonRef}
              type="button"
              aria-label="Toggle navigation"
              aria-expanded={mobileNavOpen}
              aria-controls={mobileNavId}
              onClick={() => setMobileNavOpen((open) => !open)}
              className="shrink-0 rounded-md border border-line bg-raised px-2.5 py-1.5 text-xs font-medium text-slate-muted hover:text-ink lg:hidden"
            >
              Menu
            </button>
            <BrandMark href="/admin/agents" />
          </div>
          <div className="flex shrink-0 items-center gap-3">
            {workspaceHref && !onOnboardingRoute ? (
              <Link
                href={workspaceHref}
                className="rounded-md border border-line bg-raised px-2.5 py-1.5 text-xs font-medium text-slate-muted hover:border-teal/40 hover:text-ink"
              >
                Workspace
              </Link>
            ) : null}
            {isPlatformAdmin && selectedTenantName ? (
              <button
                type="button"
                onClick={leaveTenantWorkspace}
                title="Return to platform administration"
                className="hidden max-w-[14rem] truncate rounded-md border border-teal/30 bg-teal/10 px-2.5 py-1.5 text-xs font-medium text-teal hover:bg-teal/15 sm:block"
              >
                Tenant: {selectedTenantName} · Exit
              </button>
            ) : null}
            <ThemeToggle theme={theme} onChange={changeTheme} />
            <NotificationBell />
            {clerkReady ? (
              <>
                <SignedOut>
                  <SignInButton mode="modal">
                    <button className="rounded-md border border-line bg-raised px-2.5 py-1.5 text-xs font-medium text-slate-muted hover:text-ink">
                      Sign in
                    </button>
                  </SignInButton>
                  <SignUpButton mode="modal">
                    <button className="rounded-md bg-ink px-2.5 py-1.5 text-xs font-medium text-canvas">
                      Sign up
                    </button>
                  </SignUpButton>
                </SignedOut>
                <SignedIn>
                  <UserButton />
                </SignedIn>
              </>
            ) : (
              <Link
                href="/sign-in"
                className="rounded-md border border-line bg-raised px-2.5 py-1.5 text-xs font-medium text-slate-muted hover:text-ink"
              >
                Dev account
              </Link>
            )}
          </div>
        </div>
      </header>
      <div className="relative mx-auto flex min-h-0 w-full max-w-[1600px] flex-1">
        {mobileNavOpen ? (
          <div className="absolute inset-0 z-20 lg:hidden">
            <div
              className="absolute inset-0 bg-ink/30"
              onClick={() => setMobileNavOpen(false)}
            />
            <aside
              ref={drawerRef}
              id={mobileNavId}
              role="dialog"
              aria-modal="true"
              aria-label="Navigation"
              tabIndex={-1}
              className="glass-bar absolute inset-y-0 left-0 w-64 overflow-y-auto border-r border-line/70 px-3 py-6 outline-none"
            >
              <NavLinks
                pathname={pathname}
                isPlatformAdmin={isPlatformAdmin}
                onNavigate={() => setMobileNavOpen(false)}
              />
            </aside>
          </div>
        ) : null}
        <aside className="hidden h-full w-60 shrink-0 overflow-y-auto border-r border-line/70 px-3 py-6 lg:block">
          <NavLinks
            pathname={pathname}
            isPlatformAdmin={isPlatformAdmin}
          />
        </aside>
        <main className="min-w-0 flex-1 overflow-y-auto px-5 py-8">
          {children}
        </main>
      </div>
    </div>
  );
}
