"use client";

import { Suspense } from "react";
import { signIn } from "next-auth/react";
import { useTranslations } from "next-intl";
import { useSearchParams } from "next/navigation";

import { Link } from "@/i18n/navigation";
import { safeAuthCallbackUrl } from "@/lib/auth/callback-url";

function SignUpActions() {
  const t = useTranslations("auth");
  const searchParams = useSearchParams();
  const callbackUrl = safeAuthCallbackUrl(searchParams.get("callbackUrl"));

  return (
    <button
      type="button"
      onClick={() => signIn("keycloak", { callbackUrl, redirect: true })}
      className="rounded-md bg-ink px-4 py-3 text-sm font-medium text-canvas"
    >
      {t("createAccount")}
    </button>
  );
}

export default function SignUpPage() {
  const t = useTranslations("auth");

  return (
    <main className="mx-auto flex min-h-screen max-w-md flex-col justify-center gap-6 px-6">
      <div>
        <p className="text-sm font-medium text-accent">Atlas</p>
        <h1 className="mt-2 font-display text-3xl text-ink">{t("signUpTitle")}</h1>
        <p className="mt-2 text-sm text-slate-muted">{t("signUpSubtitle")}</p>
      </div>
      <Suspense
        fallback={
          <button
            type="button"
            disabled
            className="rounded-md bg-ink px-4 py-3 text-sm font-medium text-canvas opacity-70"
          >
            {t("signUpFallback")}
          </button>
        }
      >
        <SignUpActions />
      </Suspense>
      <Link href="/sign-in" className="text-sm text-accent underline">
        {t("alreadyHaveAccount")}
      </Link>
    </main>
  );
}
