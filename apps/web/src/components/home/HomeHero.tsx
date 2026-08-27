"use client";

import { Link, useRouter } from "@/i18n/navigation";
import { useTranslations } from "next-intl";
import { useEffect, useState } from "react";

import { useSignInModal } from "@/components/auth/SignInModalProvider";
import { BrandMark } from "@/components/layout/BrandMark";
import { Button } from "@/components/ui/Button";
import { CheckIcon, SignInIcon } from "@/components/ui/icons";
import { getOnboardingStatus, getWorkspaceInfo } from "@/lib/api/admin";
import { useAgentOsToken } from "@/lib/auth/token";
import { cn } from "@/lib/utils";

type HomeMode = "loading" | "signed_out" | "onboarding" | "redirecting";

/**
 * Signed-out landing stays. Signed-in users go straight to the workspace
 * portal; Admin is available from the portal header and admin shell.
 */
export function HomeHero() {
  const router = useRouter();
  const tHome = useTranslations("home");
  const tCommon = useTranslations("common");
  const { getAccessToken, isLoaded, isSignedIn } = useAgentOsToken();
  const [mode, setMode] = useState<HomeMode>("loading");
  const [workspaceName, setWorkspaceName] = useState<string | undefined>();

  useEffect(() => {
    if (!isLoaded) {
      setMode("loading");
      return;
    }
    if (!isSignedIn) {
      setMode("signed_out");
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
          setWorkspaceName(status.tenant_name ?? undefined);
          setMode("onboarding");
          return;
        }
        const workspace = await getWorkspaceInfo(token);
        if (cancelled) return;
        const slug = workspace.slug || status.tenant_slug || "";
        const portalHref = slug ? `/t/${slug}/chat` : "/chat";
        setMode("redirecting");
        router.replace(portalHref);
      } catch {
        if (!cancelled) setMode("signed_out");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [getAccessToken, isLoaded, isSignedIn, router]);

  if (mode === "onboarding") {
    return (
      <div className="relative min-h-screen overflow-hidden">
        <div className="pointer-events-none absolute inset-0 grid-noise opacity-50" />
        <div className="relative mx-auto flex min-h-screen max-w-5xl flex-col px-6 py-10 md:py-14">
          <header>
            <BrandMark
              href="/admin/onboarding"
              subtitle={tHome("onboardingBrandSubtitle")}
            />
          </header>
          <div className="flex flex-1 flex-col justify-center py-16">
            {workspaceName ? (
              <p className="mb-3 text-xs font-semibold uppercase tracking-[0.16em] text-teal">
                {workspaceName}
              </p>
            ) : null}
            <h1 className="max-w-3xl font-display text-4xl font-semibold tracking-tight text-ink sm:text-5xl">
              {tHome("onboardingHeadline")}
            </h1>
            <p className="mt-5 max-w-xl text-base leading-relaxed text-slate-muted">
              {tHome("onboardingSupport")}
            </p>
            <div className="mt-8">
              <Link href="/admin/onboarding">
                <Button
                  variant="accent"
                  className="min-w-[10rem]"
                  icon={<CheckIcon />}
                >
                  {tHome("finishSetup")}
                </Button>
              </Link>
            </div>
          </div>
        </div>
      </div>
    );
  }

  if (mode === "redirecting" || (isLoaded && isSignedIn && mode === "loading")) {
    return (
      <main className="flex min-h-screen items-center justify-center px-6">
        <p className="text-sm text-slate-muted">{tHome("openingWorkspace")}</p>
      </main>
    );
  }

  return <SignedOutLanding loading={mode === "loading"} tCommon={tCommon} tHome={tHome} />;
}

function SignedOutLanding({
  loading,
  tCommon,
  tHome,
}: {
  loading: boolean;
  tCommon: ReturnType<typeof useTranslations<"common">>;
  tHome: ReturnType<typeof useTranslations<"home">>;
}) {
  const { openSignIn } = useSignInModal();

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

      <div className="relative mx-auto max-w-6xl px-6 py-10 md:px-8 md:py-14">
        <header className="portal-rise">
          <BrandMark href="/" size="lg" subtitle={tHome("brandSubtitle")} />
        </header>

        <section
          className={cn(
            "mt-10 grid items-center gap-10 md:mt-14 md:grid-cols-[minmax(0,1.05fr)_minmax(0,0.95fr)] md:gap-14",
            loading && "opacity-50",
          )}
        >
          <div className="portal-rise portal-rise-delay-1">
            <h1 className="max-w-xl font-display text-4xl font-semibold tracking-tight text-ink sm:text-5xl lg:text-[3.35rem] lg:leading-[1.08]">
              {tHome("headline")}
            </h1>
            <p className="mt-5 max-w-lg text-lg leading-relaxed text-slate-muted">
              {tHome("support")}
            </p>
            <div className="mt-8 flex flex-wrap items-center gap-3">
              <Button
                type="button"
                variant="accent"
                className="min-w-[9.5rem]"
                icon={<SignInIcon />}
                onClick={() => openSignIn()}
              >
                {tCommon("signIn")}
              </Button>
            </div>
          </div>

          <div className="portal-rise portal-rise-delay-2">
            <WorkspacePreview tHome={tHome} />
          </div>
        </section>

        <section className="portal-rise portal-rise-delay-3 mt-14 md:mt-16">
          <p className="text-xs font-semibold uppercase tracking-[0.16em] text-teal">
            {tHome("tenantsEyebrow")}
          </p>
          <h2 className="mt-3 max-w-2xl font-display text-3xl font-semibold tracking-tight text-ink">
            {tHome("tenantsTitle")}
          </h2>
          <div className="mt-8 grid gap-4 sm:grid-cols-3">
            {[
              {
                name: tHome("tenantStockbroker"),
                hint: tHome("tenantStockbrokerHint"),
              },
              {
                name: tHome("tenantDental"),
                hint: tHome("tenantDentalHint"),
              },
              {
                name: tHome("tenantMore"),
                hint: tHome("tenantMoreHint"),
              },
            ].map((card) => (
              <div
                key={card.name}
                className="rounded-2xl border border-line/80 bg-canvas/80 p-4 shadow-[0_1px_0_rgba(7,16,24,0.04)]"
              >
                <p className="font-display text-lg font-semibold tracking-tight text-ink">
                  {card.name}
                </p>
                <p className="mt-2 text-sm leading-relaxed text-slate-muted">
                  {card.hint}
                </p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-16 border-t border-line/70 pt-12 md:mt-20">
          <p className="text-xs font-semibold uppercase tracking-[0.16em] text-teal">
            {tHome("twoDoorsEyebrow")}
          </p>
          <h2 className="mt-3 max-w-xl font-display text-3xl font-semibold tracking-tight text-ink">
            {tHome("twoDoorsTitle")}
          </h2>
          <div className="mt-8 grid gap-8 sm:grid-cols-2 md:gap-12">
            <div>
              <h3 className="font-display text-xl font-semibold text-ink">
                {tHome("controlPlaneTitle")}
              </h3>
              <p className="mt-2 text-sm leading-relaxed text-slate-muted">
                {tHome("controlPlaneBody")}
              </p>
              <ul className="mt-5 space-y-2.5 text-sm leading-relaxed text-ink-soft">
                {[
                  tHome("controlPlaneBullet1"),
                  tHome("controlPlaneBullet2"),
                  tHome("controlPlaneBullet3"),
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
                {tHome("workspacePortalTitle")}
              </h3>
              <p className="mt-2 text-sm leading-relaxed text-slate-muted">
                {tHome("workspacePortalBody")}
              </p>
              <ul className="mt-5 space-y-2.5 text-sm leading-relaxed text-ink-soft">
                {[
                  tHome("workspacePortalBullet1"),
                  tHome("workspacePortalBullet2"),
                  tHome("workspacePortalBullet3"),
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

        <section className="mt-16 md:mt-20">
          <p className="text-xs font-semibold uppercase tracking-[0.16em] text-teal">
            {tHome("pathEyebrow")}
          </p>
          <h2 className="mt-3 font-display text-3xl font-semibold tracking-tight text-ink">
            {tHome("pathTitle")}
          </h2>
          <ol className="mt-8 grid gap-6 sm:grid-cols-3">
            {[
              { step: "01", title: tHome("step1Title"), copy: tHome("step1Copy") },
              { step: "02", title: tHome("step2Title"), copy: tHome("step2Copy") },
              { step: "03", title: tHome("step3Title"), copy: tHome("step3Copy") },
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

        <footer className="mt-16 flex flex-col gap-3 border-t border-line/70 pt-8 text-sm text-slate-muted sm:flex-row sm:items-center sm:justify-between">
          <p>
            {tHome("title")} — {tHome("platformDescriptor")}
          </p>
          <p className="text-xs uppercase tracking-[0.12em]">{tHome("footerCta")}</p>
        </footer>
      </div>
    </div>
  );
}

function WorkspacePreview({
  tHome,
}: {
  tHome: ReturnType<typeof useTranslations<"home">>;
}) {
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
              {tHome("previewTenant")}
            </p>
            <p className="mt-0.5 text-xs text-white/45">{tHome("previewPortalLabel")}</p>
          </div>
          <span className="rounded-full border border-white/15 px-2.5 py-1 text-[10px] text-white/55">
            {tHome("previewSignedIn")}
          </span>
        </div>
        <p className="mt-6 font-display text-2xl font-semibold tracking-tight text-white">
          {tHome("previewPrompt")}
        </p>
        <p className="mt-2 max-w-sm text-sm text-white/50">{tHome("previewSubtext")}</p>
        <div className="mt-6 grid gap-3 sm:grid-cols-2">
          {[
            {
              kind: tHome("previewWorkflowKind"),
              name: tHome("previewWorkflowName"),
              hint: tHome("previewWorkflowHint"),
            },
            {
              kind: tHome("previewTeamKind"),
              name: tHome("previewTeamName"),
              hint: tHome("previewTeamHint"),
            },
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
