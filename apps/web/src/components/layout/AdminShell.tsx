"use client";

import {
  SignedIn,
  SignedOut,
  SignInButton,
  SignUpButton,
  UserButton,
} from "@clerk/nextjs";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

import { BrandMark } from "@/components/layout/BrandMark";
import {
  PLATFORM_TENANT_COOKIE,
  PLATFORM_TENANT_NAME_COOKIE,
  tokenIsPlatformAdmin,
} from "@/lib/auth/access-context";
import { getAccessToken } from "@/lib/auth/token";
import {
  readStoredTheme,
  THEME_STORAGE_KEY,
  ThemeToggle,
  type AdminTheme,
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
  sessions: icon(
    <path d="M21 12a8 8 0 0 1-8 8H4l2-3a8 8 0 1 1 15-5Z" />,
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
        hint: "Orchestrate teams and agents",
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
        hint: "Create and publish AI agents",
        icon: icons.agents,
      },
      {
        href: "/admin/tools",
        label: "Tools",
        hint: "HTTP, OpenAPI, toolkits, MCP",
        icon: icons.tools,
      },
      {
        href: "/admin/knowledge",
        label: "Knowledge",
        hint: "Upload docs for RAG",
        icon: icons.knowledge,
      },
    ],
  },
  {
    label: "Monitor",
    items: [
      {
        href: "/admin/sessions",
        label: "Sessions",
        hint: "Conversation history",
        icon: icons.sessions,
      },
      {
        href: "/admin/approvals",
        label: "Approvals",
        hint: "Pending human sign-offs",
        icon: icons.approvals,
      },
      {
        href: "/admin/schedules",
        label: "Schedules",
        hint: "Cron-triggered runs",
        icon: icons.schedules,
      },
      {
        href: "/admin/traces",
        label: "Traces",
        hint: "Run and tool-call debugging",
        icon: icons.traces,
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
        href: "/admin/users",
        label: "Users",
        hint: "CRUD users and assign workflows",
        icon: icons.users,
      },
      {
        href: "/admin/integrations",
        label: "Integrations",
        hint: "Configure Python toolkit requirements",
        icon: icons.integrations,
      },
      {
        href: "/admin/credentials",
        label: "Credentials",
        hint: "API keys (BYOK)",
        icon: icons.credentials,
      },
      {
        href: "/admin/service-accounts",
        label: "Service accounts",
        hint: "Machine API access",
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
                  onClick={onNavigate}
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
  const [theme, setTheme] = useState<AdminTheme>("light");
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [isPlatformAdmin, setIsPlatformAdmin] = useState(false);
  const [selectedTenantName, setSelectedTenantName] = useState<string | null>(
    null,
  );

  useEffect(() => {
    setTheme(readStoredTheme());
    void getAccessToken().then((token) => {
      if (token) setIsPlatformAdmin(tokenIsPlatformAdmin(token));
    });
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

  useEffect(() => {
    setMobileNavOpen(false);
  }, [pathname]);

  function changeTheme(next: AdminTheme) {
    setTheme(next);
    try {
      window.localStorage.setItem(THEME_STORAGE_KEY, next);
    } catch {
      // storage unavailable (private mode) — theme stays in-memory
    }
  }

  function leaveTenantWorkspace() {
    document.cookie = `${PLATFORM_TENANT_COOKIE}=; Path=/; Max-Age=0; SameSite=Lax`;
    document.cookie = `${PLATFORM_TENANT_NAME_COOKIE}=; Path=/; Max-Age=0; SameSite=Lax`;
    window.location.assign("/admin/platform/tenants");
  }

  const publishableKey = process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY;
  const clerkReady =
    !!publishableKey && !publishableKey.includes("replace_me");

  return (
    <div
      data-theme={theme === "dark" ? "dark" : undefined}
      className="app-canvas min-h-screen text-ink"
    >
      <header className="glass-bar sticky top-0 z-30 border-b border-line/70">
        <div className="flex items-center justify-between gap-6 px-5 py-3">
          <div className="flex items-center gap-3">
            <button
              type="button"
              aria-label="Toggle navigation"
              onClick={() => setMobileNavOpen((open) => !open)}
              className="rounded-md border border-line bg-raised px-2.5 py-1.5 text-xs font-medium text-slate-muted hover:text-ink lg:hidden"
            >
              Menu
            </button>
            <BrandMark />
          </div>
          <div className="flex items-center gap-3">
            {isPlatformAdmin && selectedTenantName ? (
              <button
                type="button"
                onClick={leaveTenantWorkspace}
                title="Return to platform administration"
                className="hidden rounded-md border border-teal/30 bg-teal/10 px-2.5 py-1.5 text-xs font-medium text-teal hover:bg-teal/15 sm:block"
              >
                Tenant: {selectedTenantName} · Exit
              </button>
            ) : null}
            <ThemeToggle theme={theme} onChange={changeTheme} />
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
      <div className="mx-auto flex w-full max-w-[1600px]">
        {mobileNavOpen ? (
          <div className="fixed inset-0 z-20 lg:hidden">
            <div
              className="absolute inset-0 bg-ink/30"
              onClick={() => setMobileNavOpen(false)}
            />
            <aside className="glass-bar absolute inset-y-0 left-0 w-64 overflow-y-auto border-r border-line/70 px-3 pb-8 pt-20">
              <NavLinks
                pathname={pathname}
                isPlatformAdmin={isPlatformAdmin}
                onNavigate={() => setMobileNavOpen(false)}
              />
            </aside>
          </div>
        ) : null}
        <aside className="sticky top-[57px] hidden h-[calc(100vh-57px)] w-60 shrink-0 overflow-y-auto border-r border-line/70 px-3 py-6 lg:block">
          <NavLinks
            pathname={pathname}
            isPlatformAdmin={isPlatformAdmin}
          />
        </aside>
        <main className="min-w-0 flex-1 px-5 py-8">{children}</main>
      </div>
    </div>
  );
}
