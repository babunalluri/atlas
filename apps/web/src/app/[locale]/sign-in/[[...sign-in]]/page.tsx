"use client";

import { Suspense } from "react";
import { signIn } from "next-auth/react";
import { useTranslations } from "next-intl";
import { useSearchParams } from "next/navigation";

import { Link } from "@/i18n/navigation";
import { safeAuthCallbackUrl } from "@/lib/auth/callback-url";

function SignInActions() {
  const t = useTranslations("auth");
  const searchParams = useSearchParams();
  const callbackUrl = safeAuthCallbackUrl(
    searchParams.get("callbackUrl") ?? searchParams.get("redirect_url"),
  );

  return (
    <button
      type="button"
      onClick={() => signIn("keycloak", { callbackUrl })}
      className="rounded-md bg-ink px-4 py-3 text-sm font-medium text-canvas"
    >
      {t("continueWithKeycloak")}
    </button>
  );
}

export default function SignInPage() {
  const t = useTranslations("auth");
  const tc = useTranslations("common");

  return (
    <main className="mx-auto flex min-h-screen max-w-md flex-col justify-center gap-6 px-6">
      <div>
        <p className="text-sm font-medium text-accent">Atlas</p>
        <h1 className="mt-2 font-display text-3xl text-ink">{t("signInTitle")}</h1>
        <p className="mt-2 text-sm text-slate-muted">{t("signInSubtitle")}</p>
      </div>
      <Suspense
        fallback={
          <button
            type="button"
            disabled
            className="rounded-md bg-ink px-4 py-3 text-sm font-medium text-canvas opacity-70"
          >
            {t("continueWithKeycloak")}
          </button>
        }
      >
        <SignInActions />
      </Suspense>
      <p className="text-xs text-slate-muted">
        {t("devUsers")}: <code>admin@atlas.local</code> / <code>atlas-admin</code> ·{" "}
        <code>ops@acme.atlas.local</code> / <code>atlas-acme</code>
      </p>
      <Link href="/" className="text-sm text-accent underline">
        {t("backToHome")}
      </Link>
      <p className="sr-only">{tc("signIn")}</p>
    </main>
  );
}
