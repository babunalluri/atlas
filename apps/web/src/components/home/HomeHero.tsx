"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { BrandMark } from "@/components/layout/BrandMark";
import { Button } from "@/components/ui/Button";
import { getOnboardingStatus, getWorkspaceInfo } from "@/lib/api/admin";
import { useAgentOsToken } from "@/lib/auth/token";
import { cn } from "@/lib/utils";

type HomeMode = "loading" | "signed_out" | "onboarding" | "redirecting";

type HomeState = {
  mode: HomeMode;
  primaryHref: string;
  primaryLabel: string;
  workspaceName?: string;
  brandHref: string;
  brandSubtitle: string;
  headline: string;
  support: string;
};

const SIGNED_OUT: HomeState = {
  mode: "signed_out",
  primaryHref: "/sign-in",
  primaryLabel: "Sign in",
  brandHref: "/",
  brandSubtitle: "Agent workspace",
  headline: "Your team's agents, ready when you are.",
  support:
    "Sign in with your organization account to open assigned workflows and teams, or administer the control plane.",
};

/**
 * Signed-out landing stays. Signed-in users go straight to the workspace
 * portal; Admin is available from the portal header and admin shell.
 */
export function HomeHero() {
  const router = useRouter();
  const { getAccessToken, isLoaded, isSignedIn } = useAgentOsToken();
  const [state, setState] = useState<HomeState>({
    ...SIGNED_OUT,
    mode: "loading",
    headline: "…",
    support: "",
  });

  useEffect(() => {
    if (!isLoaded) {
      setState((prev) => ({ ...prev, mode: "loading" }));
      return;
    }
    if (!isSignedIn) {
      setState(SIGNED_OUT);
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        const token = await getAccessToken();
        if (!token || cancelled) return;
        const status = await getOnboardingStatus(token);
        if (cancelled) return;
        if (!status.provisioned) {
          setState({
            mode: "onboarding",
            primaryHref: "/admin/onboarding",
            primaryLabel: "Finish setup",
            workspaceName: status.tenant_name ?? undefined,
            brandHref: "/admin/onboarding",
            brandSubtitle: "Workspace setup",
            headline: "Finish setting up your workspace.",
            support:
              "Provision your organization tenant, then publish workflows your teammates can run.",
          });
          return;
        }
        const workspace = await getWorkspaceInfo(token);
        if (cancelled) return;
        const slug = workspace.slug || status.tenant_slug || "";
        const portalHref = slug ? `/t/${slug}/chat` : "/chat";
        if (!cancelled) {
          setState((prev) => ({ ...prev, mode: "redirecting" }));
          router.replace(portalHref);
        }
      } catch {
        if (!cancelled) setState(SIGNED_OUT);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [getAccessToken, isLoaded, isSignedIn, router]);

  if (state.mode === "onboarding") {
    return (
      <div className="relative min-h-screen overflow-hidden">
        <div className="pointer-events-none absolute inset-0 grid-noise opacity-50" />
        <div className="relative mx-auto flex min-h-screen max-w-5xl flex-col px-6 py-10 md:py-14">
          <header>
            <BrandMark href={state.brandHref} subtitle={state.brandSubtitle} />
          </header>
          <div className="flex flex-1 flex-col justify-center py-16">
            {state.workspaceName ? (
              <p className="mb-3 text-xs font-semibold uppercase tracking-[0.16em] text-teal">
                {state.workspaceName}
              </p>
            ) : null}
            <h1 className="max-w-3xl font-display text-4xl font-semibold tracking-tight text-ink sm:text-5xl">
              {state.headline}
            </h1>
            <p className="mt-5 max-w-xl text-base leading-relaxed text-slate-muted">
              {state.support}
            </p>
            <div className="mt-8">
              <Link href={state.primaryHref}>
                <Button variant="accent" className="min-w-[10rem]">
                  {state.primaryLabel}
                </Button>
              </Link>
            </div>
          </div>
        </div>
      </div>
    );
  }

  if (
    state.mode === "redirecting" ||
    (isLoaded && isSignedIn && state.mode === "loading")
  ) {
    return (
      <main className="flex min-h-screen items-center justify-center px-6">
        <p className="text-sm text-slate-muted">Opening your workspace…</p>
      </main>
    );
  }

  return <SignedOutLanding loading={state.mode === "loading"} />;
}

function SignedOutLanding({ loading }: { loading: boolean }) {
  return (
    <div className="relative overflow-hidden">
      <div className="pointer-events-none absolute inset-0 grid-noise opacity-45" />
      <div
        className="pointer-events-none absolute -left-32 top-0 h-[28rem] w-[28rem] rounded-full opacity-45"
        style={{
          background:
            "radial-gradient(circle, color-mix(in oklab, var(--tone-accent) 26%, transparent), transparent 68%)",
        }}
      />
      <div
        className="pointer-events-none absolute right-[-8%] top-[18%] h-[32rem] w-[32rem] rounded-full opacity-35"
        style={{
          background:
            "radial-gradient(circle, color-mix(in oklab, var(--tone-info) 16%, transparent), transparent 70%)",
        }}
      />

      <div className="relative mx-auto max-w-6xl px-6 pb-24 pt-10 md:px-8 md:pt-14">
        <header className="portal-rise">
          <BrandMark href="/" subtitle="Agent workspace" />
        </header>

        <section
          className={cn(
            "mt-14 grid items-center gap-12 lg:mt-20 lg:grid-cols-[minmax(0,1.05fr)_minmax(0,0.95fr)] lg:gap-16",
            loading && "opacity-50",
          )}
        >
          <div className="portal-rise portal-rise-delay-1">
            <h1 className="max-w-xl font-display text-4xl font-semibold tracking-tight text-ink sm:text-5xl lg:text-[3.35rem] lg:leading-[1.08]">
              Your team&apos;s agents, ready when you are.
            </h1>
            <p className="mt-5 max-w-md text-base leading-relaxed text-slate-muted">
              Organization members sign in to open assigned workflows and teams,
              or administer the control plane.
            </p>
            <div className="mt-8 flex flex-wrap items-center gap-3">
              <Link href="/sign-in">
                <Button variant="accent" className="min-w-[9.5rem]">
                  Sign in
                </Button>
              </Link>
              <Link href="/sign-up">
                <Button variant="secondary">Create account</Button>
              </Link>
            </div>
          </div>

          <div className="portal-rise portal-rise-delay-2">
            <WorkspacePreview />
          </div>
        </section>

        <section className="portal-rise portal-rise-delay-3 mt-24 border-t border-line/70 pt-14 md:mt-28">
          <p className="text-xs font-semibold uppercase tracking-[0.16em] text-teal">
            Two doors, one platform
          </p>
          <h2 className="mt-3 max-w-xl font-display text-3xl font-semibold tracking-tight text-ink">
            Built for operators and the people who run the work.
          </h2>
          <div className="mt-10 grid gap-10 md:grid-cols-2 md:gap-14">
            <div>
              <h3 className="font-display text-xl font-semibold text-ink">
                Control plane
              </h3>
              <p className="mt-2 text-sm leading-relaxed text-slate-muted">
                Admins configure agents, teams, knowledge, tools, and approvals —
                then publish experiences for org members.
              </p>
              <ul className="mt-5 space-y-2.5 text-sm text-ink-soft">
                {[
                  "Draft, version, and publish agents & workflows",
                  "Attach knowledge, credentials, and sandbox tools",
                  "Trace runs, resolve approvals, manage access",
                ].map((line) => (
                  <li key={line} className="flex gap-2.5">
                    <span className="mt-2 h-1 w-1 shrink-0 rounded-full bg-teal" />
                    <span>{line}</span>
                  </li>
                ))}
              </ul>
            </div>
            <div>
              <h3 className="font-display text-xl font-semibold text-ink">
                Workspace portal
              </h3>
              <p className="mt-2 text-sm leading-relaxed text-slate-muted">
                Teammates sign in, land in a tenant-branded portal, pick an
                assigned workflow or team, and chat — no anonymous guest access.
              </p>
              <ul className="mt-5 space-y-2.5 text-sm text-ink-soft">
                {[
                  "Only surfaces assigned to your org account",
                  "Streaming chat with pause, cancel, and resume",
                  "Organization identity on every run",
                ].map((line) => (
                  <li key={line} className="flex gap-2.5">
                    <span className="mt-2 h-1 w-1 shrink-0 rounded-full bg-teal" />
                    <span>{line}</span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </section>

        <section className="mt-20 md:mt-24">
          <p className="text-xs font-semibold uppercase tracking-[0.16em] text-teal">
            From sign-in to conversation
          </p>
          <h2 className="mt-3 font-display text-3xl font-semibold tracking-tight text-ink">
            A straight path into the right surface.
          </h2>
          <ol className="mt-10 grid gap-6 sm:grid-cols-3">
            {[
              {
                step: "01",
                title: "Authenticate",
                copy: "Sign in with your organization account. Atlas resolves your tenant and role.",
              },
              {
                step: "02",
                title: "Choose a surface",
                copy: "Open the workspace portal, then use Admin when you need the control plane.",
              },
              {
                step: "03",
                title: "Run the work",
                copy: "Start a workflow or team chat — streaming responses with human approvals when needed.",
              },
            ].map((item) => (
              <li key={item.step} className="relative pt-1">
                <p className="font-mono text-xs text-teal">{item.step}</p>
                <h3 className="mt-2 font-display text-lg font-semibold text-ink">
                  {item.title}
                </h3>
                <p className="mt-2 text-sm leading-relaxed text-slate-muted">
                  {item.copy}
                </p>
              </li>
            ))}
          </ol>
        </section>

        <footer className="mt-24 flex flex-col gap-3 border-t border-line/70 pt-8 text-sm text-slate-muted sm:flex-row sm:items-center sm:justify-between">
          <p>Atlas Agents — multi-tenant agent platform</p>
          <p className="text-xs uppercase tracking-[0.12em]">
            Sign in to continue
          </p>
        </footer>
      </div>
    </div>
  );
}

/** Decorative product mock — fills the empty hero plane without stock imagery. */
function WorkspacePreview() {
  return (
    <div
      className="relative overflow-hidden rounded-2xl border border-line bg-ink text-canvas shadow-[0_24px_60px_-28px_rgba(7,16,24,0.55)]"
      aria-hidden
    >
      <div
        className="absolute inset-0 opacity-90"
        style={{
          background: `
            radial-gradient(600px 280px at 12% 0%, rgba(24, 196, 168, 0.28), transparent 55%),
            linear-gradient(160deg, #0a2a24 0%, #071018 55%, #04110c 100%)
          `,
        }}
      />
      <div className="pointer-events-none absolute inset-0 opacity-[0.12] grid-noise" />
      <div className="relative p-5 sm:p-6">
        <div className="flex items-center justify-between gap-3">
          <div>
            <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-teal-bright">
              Acme Corp
            </p>
            <p className="mt-0.5 text-xs text-white/45">Workspace portal</p>
          </div>
          <span className="rounded-full border border-white/15 px-2.5 py-1 text-[10px] text-white/55">
            Signed in
          </span>
        </div>
        <p className="mt-6 font-display text-2xl font-semibold tracking-tight text-white">
          What would you like to work on?
        </p>
        <p className="mt-2 max-w-sm text-sm text-white/50">
          Assigned workflows and teams for your account.
        </p>
        <div className="mt-6 grid gap-3 sm:grid-cols-2">
          {[
            { kind: "Workflow", name: "Customer intake", hint: "Guided steps" },
            { kind: "Team", name: "Front line", hint: "Open chat" },
          ].map((card) => (
            <div
              key={card.name}
              className="rounded-xl border border-white/10 bg-white/[0.05] p-3.5"
            >
              <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-teal-bright">
                {card.kind}
              </p>
              <p className="mt-1.5 font-display text-base font-semibold text-white">
                {card.name}
              </p>
              <p className="mt-1 text-xs text-white/45">{card.hint} →</p>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
